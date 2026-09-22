"""Offline checks for inherited fixes; not a reproduction of a reported client crash.

Run: python -m pip install lupa; python tests/smoke.py
Optional --ref checks another git revision without changing the working tree.
"""
import argparse
from pathlib import Path
import subprocess

from lupa.luajit21 import LuaRuntime

ROOT = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--ref")
args = parser.parse_args()


def source(path):
    if args.ref:
        return subprocess.check_output(["git", "show", f"{args.ref}:{path}"], cwd=ROOT)
    return (ROOT / path).read_bytes()


lua = LuaRuntime(encoding=None)
compile_lua = lua.eval(b"function(s, name) assert(loadstring(s, name)) end")
paths = subprocess.check_output(["git", "ls-files", "*.lua"], cwd=ROOT).decode().splitlines()
for path in paths:
    compile_lua(source(path), path.encode())
print(f"PASS: {len(paths)} Lua files compile with LuaJIT 2.1")

# Real packet handler, synthetic synthesis packet, absent legacy Self global.
# FFI is real; the engine's target/chat/struct interfaces are the only stubs.
lua.execute(b"""
T = function(t) return setmetatable(t, {__index={append=table.insert}}) end
package.loaded.chat = {}
gStatus = { PlayerId = 123, PlayerName = 'Crafter' }
gProfileSettings = { mode = { crafting = true } }
messages = {}
targetId = 0
targetEntity = nil
GetEntity = function() return targetEntity end
local target = {
    GetTargetIndex = function() return 1 end,
    GetServerId = function() return targetId end,
}
local memory = { GetTarget = function() return target end }
local chatManager = { AddChatMessage = function(_, _, _, text)
    messages[#messages + 1] = text
end }
AshitaCore = {
    GetMemoryManager = function() return memory end,
    GetChatManager = function() return chatManager end,
}
struct = { unpack = function(fmt, data, offset)
    if fmt:sub(1,1) == 'c' then return data:sub(offset, offset+tonumber(fmt:sub(2))-1) end
    assert(fmt == 'I' and offset == 5)
    local a,b,c,d = data:byte(offset, offset+3)
    return a + b*256 + c*65536 + d*16777216
end }
ashita = {bits={unpack_be=function(data, offset, start, width)
    assert(offset == 0 and start == 9 and width == 7)
    return #data / 4
end}}
""")
lua.globals().handler = lua.execute(source("lib/packethandlers.lua"))
lua.execute(b"""
local function synth(id, result)
    local data=string.rep('\0', 4) .. string.char(id,0,0,0) ..
             string.rep('\0', 4) .. string.char(result) .. string.rep('\0', 3)
    handler.HandleIncomingPacket({id=0x030, data=data,
        data_raw=data, chunk_data_raw=data, size=#data, chunk_data=data, chunk_size=#data})
end
synth(123, 0)
assert(#messages == 1 and messages[1]:find('NQ Synthesis (Crafter)', 1, true))
targetId = 124
targetEntity = {Name='Neighbour'}
synth(124, 2)
assert(#messages == 2 and messages[2]:find('HQ Synthesis (Neighbour)', 1, true))
targetId = 0
targetEntity = nil
synth(125, 0)
assert(#messages == 2, 'unselected crafter should not produce a preview')
""")
print("PASS: own/targeted/unselected crafting messages without legacy Self")

# Actor lookup used by the log parser must match Ashita's ServerId field.
lua = LuaRuntime(encoding=None)
lua.execute(b"""
string.fmt = string.format
owner = {ServerId=123, PetTargetIndex=42, FellowTargetIndex=43}
GetEntity = function(index) if index == 7 then return owner end end
local party = {
    GetMemberIsActive = function(_, index) return index == memberIndex and 1 or 0 end,
    GetMemberServerId = function() return 123 end,
}
local memory = {GetParty = function() return party end}
AshitaCore = {GetMemoryManager = function() return memory end}
""")
lua.globals().funcs = lua.execute(source("lib/functions.lua"))
lua.execute(b"""
assert(funcs.GetEntityByServerId(123) == owner)
assert(funcs.GetEntityByServerId(999) == nil)
assert(funcs.FindPetOwner(42) == owner)
assert(funcs.FindFellowOwner(43) == owner)
for index = 0,17 do
    memberIndex = index
    local expected = index < 6 and ('p%u'):fmt(index) or
        index < 12 and ('al%u'):fmt(index-6) or ('a2%u'):fmt(index-12)
    assert(funcs.GetPartyType(123) == expected)
end
assert(funcs.GetPartyType(999) == 'other')
""")
print("PASS: server-id, pet/fellow ownership, all 18 party/alliance slots")
