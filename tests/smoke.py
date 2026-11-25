"""Offline checks for Thorny's inherited fixes and AscensionXI's own, on synthetic packets.

Not a capture from a live client.

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

# The real packet handler fed whole chunks the way Ashita 4.3 delivers them:
# one packet_in per packet, each carrying the chunk it came in.
PACKET_STUBS = b"""
T = function(t) return setmetatable(t, {__index={
    append=table.insert,
    contains=function(t, v) for _, x in ipairs(t) do if x == v then return true end end return false end,
}}) end
package.loaded.chat = {}
chat = { color1 = function(_, s) return s end, color2 = function(_, s) return s end }
messages = {}
local chatManager = { AddChatMessage = function(_, _, _, text) messages[#messages + 1] = text end }
buffNames = { [13] = 'Slow' }
local resources = { GetString = function(_, table, id) assert(table == 'buffs.names'); return buffNames[id] end }
AshitaCore = {
    GetChatManager = function() return chatManager end,
    GetResourceManager = function() return resources end,
}
struct = { unpack = function(fmt, data, offset)
    local count = fmt:match('^c(%d+)$')
    if count then return data:sub(offset, offset + tonumber(count) - 1) end
    local value = 0
    for i = ({H=2, I=4, L=4})[fmt] - 1, 0, -1 do value = value * 256 + data:byte(offset + i) end
    return value
end }
ashita = {bits={unpack_be=function(data, offset, start, width)
    assert(start == 9 and width == 7)
    return math.floor(data:byte(offset + 2) / 2)
end}}

-- Header: 9-bit id, size in 4-byte words, then the chunk's 16-bit sync.
function packet(id, size, sync, body)
    assert(size % 4 == 0 and #body <= size - 4, 'packet body does not fit its size')
    local header = string.char(id % 256, math.floor(id / 256) + size / 4 * 2, sync % 256, math.floor(sync / 256))
    return header .. body .. string.rep('\\0', size - 4 - #body)
end

function deliver(packets)
    local chunk = table.concat(packets)
    local blocked = {}
    for i, p in ipairs(packets) do
        local e = {id=p:byte(1) + p:byte(2) % 2 * 256, size=#p, data=p, data_raw=p, data_modified=p,
                   chunk_data=chunk, chunk_data_raw=chunk, chunk_size=#chunk, blocked=false}
        handler.HandleIncomingPacket(e)
        blocked[i] = e.blocked
    end
    return blocked
end
"""


def packet_runtime():
    runtime = LuaRuntime(encoding=None)
    runtime.execute(PACKET_STUBS)
    runtime.globals().handler = runtime.execute(source("lib/packethandlers.lua"))
    runtime.execute(b"gPacketHandlers = handler")
    return runtime


# AXI-89: the server can put the same packet twice in one chunk. When the
# repeat was the chunk's first packet, the addon took it for a new chunk and
# dropped every action after it: no animation, no log line, damage dealt.
lua = packet_runtime()
lua.execute(b"""
gProfileSettings = { mode = {} }
actions = {}
handler.HandleIncoming0x28 = function(e) actions[#actions + 1] = e.data; return e.data end

local health = packet(0x0DF, 8, 7, 'hp')
local strike = packet(0x028, 20, 7, 'ground strike')
local follow = packet(0x028, 20, 7, 'next round')
local first = deliver({health, strike, health, follow})
assert(not first[2] and not first[4], 'an action after a repeat of the chunk\\'s first packet was dropped')
assert(#actions == 2 and actions[2] == follow)

local resent = deliver({health, strike, health, follow})
assert(resent[2] and resent[4], 'a resent chunk must still be dropped as a duplicate')
assert(#actions == 2)

local nextChunk = deliver({packet(0x0DF, 8, 8, 'hp'), packet(0x028, 20, 8, 'ground strike')})
assert(not nextChunk[2] and #actions == 3, 'the same action in the next chunk is new')
""")
print("PASS: a repeated packet inside a chunk keeps its later actions; resends still dropped")

# AXI-126: a status the client has no name for (AscensionXI's Judgement, 828)
# wearing off unloaded the addon with "table index is nil".
lua = packet_runtime()
lua.execute(source("lib/constants.lua"))
lua.globals().res_actmsg = lua.execute(source("lib/res/action_messages.lua"))[0]  # also returns its field list
lua.globals().gFuncs = lua.execute(source("lib/functions.lua"))
lua.execute(b"""
gProfileSettings = { mode = { condensetargets = true }, lang = { msg_text = 'en', internal = 2 } }
gProfileColor = { statuscol = 1, enfeebcol = 2, itemcol = 3, spellcol = 4, abilcol = 5, me = 6, mob = 7 }
gProfileFilter = { me = {} }
pending = {}
-- Ashita's common library gives functions bind1/once; constants.lua reuses the global name debug.
require('debug').setmetatable(function() end, {__index = {
    bind1 = function(f, a) return function(...) return f(a, ...) end end,
    once = function(f) pending[#pending + 1] = f end,
}})
local actors = {
    [1] = {id=1, name='Ulti', filter='me', type='me', owner_name=''},
    [2] = {id=2, name='Goblin', filter='monsters', type='mob', owner_name=''},
}
gActionHandlers = {
    ActorParse = function(id) return actors[id] end,
    AssembleTargets = function(_, targets) return targets[1].name .. "'s" end,
}

local function u32(v) return string.char(v % 256, math.floor(v / 256) % 256, math.floor(v / 65536) % 256, 0) end
local function wearsOff(status, sync)
    -- actor, target, param, value, actor/target index, message 206
    local body = u32(1) .. u32(2) .. u32(status) .. u32(0) .. string.char(0, 0, 0, 0, 206, 0)
    return packet(0x029, 28, sync, body)
end

local blocked = deliver({wearsOff(828, 1)})
assert(blocked[1] and #messages == 1, 'the nameless wear-off line should be printed by the addon')
assert(messages[1] == "Goblin's  effect wears off.", messages[1])

deliver({wearsOff(13, 2)})
assert(#pending == 1, 'a named status still condenses')
pending[1]()
assert(#messages == 2 and messages[2] == "Goblin's Slow effect wears off.", messages[2])
""")
print("PASS: a status without a client name wears off without unloading; named ones condense")

# Geist Wall in Onslaught T4 can remove a Paladin's Light (844), which has no client
# name, and the addon unloaded with "actionhandlers.lua:372: attempt to
# concatenate local 'numb' (a nil value)". The real action parser, plain and
# simplified, one target and two, and the same gap on an additional effect.
def action_runtime(simplify):
    runtime = LuaRuntime(encoding=None)
    runtime.execute(PACKET_STUBS)
    runtime.execute(source("lib/constants.lua"))
    runtime.globals().res_actmsg = runtime.execute(source("lib/res/action_messages.lua"))[0]
    runtime.globals().gFuncs = runtime.execute(source("lib/functions.lua"))
    runtime.globals().gActionHandlers = runtime.execute(source("lib/actionhandlers.lua"))
    runtime.globals().simplify = simplify
    runtime.execute(b"""
buffNames[844] = nil
gStatus = { PlayerId = 1 }
gProfileSettings = {
    mode = { simplify = simplify, condensetargets = true },
    lang = { msg_text = 'en', internal = 2 },
    text = { line_aoebuff = '${actor} ${abil} > ${target} (${status})' },
}
gProfileColor = setmetatable({}, {__index = function() return 1 end})
local actors = {
    [1] = {id=1, server_id=1, name='Ulti', filter='me', type='me', owner_name='', race=1},
    [2] = {id=2, server_id=2, name='Yoji', filter='party', type='p1', owner_name='', race=1},
    [3] = {id=3, server_id=3, name='Gurfurlur', filter='monsters', type='mob', owner_name='', race=0},
}
gActionHandlers.ActorParse = function(id) local a = actors[id]; return a and setmetatable({}, {__index = a}) end
gActionHandlers.SpellParse = function() return {name='Geist Wall', weapon_skill='Geist Wall'} end
gFuncs.CheckFilter = function() return true end

function result(message, param, extra)
    local r = {message=message, param=param, reaction=0, effect=0, unknown=0,
               has_add_effect=false, add_effect_message=0, add_effect_param=0,
               has_spike_effect=false, spike_effect_message=0, spike_effect_param=0}
    for k, v in pairs(extra or {}) do r[k] = v end
    return r
end

function mobMove(targets)
    local act = {actor_id=3, category=11, param=1155, targets={}}
    for _, t in ipairs(targets) do
        act.targets[#act.targets + 1] = {server_id=t[1], actions={t[2]}}
    end
    messages = {}
    gActionHandlers.parse_action_packet(act)
    return messages
end
""")
    return runtime


for simplify in (False, True):
    lua = action_runtime(simplify)
    lua.execute(b"""
local lines = mobMove({{1, result(159, 844)}})
assert(#lines == 1, 'Geist Wall removing a nameless status printed ' .. #lines .. ' lines')
assert(not lines[1]:find('ERROR', 1, true), lines[1])

lines = mobMove({{1, result(159, 844)}, {2, result(159, 13)}})
assert(#lines == 2, 'two targets, one nameless status, printed ' .. #lines .. ' lines')
assert(lines[2]:find('Slow', 1, true), 'a named status keeps its name: ' .. lines[2])

lines = mobMove({{1, result(185, 120, {has_add_effect=true, add_effect_message=160, add_effect_param=844})}})
assert(#lines == 2, 'damage and additional effect printed ' .. #lines .. ' lines')
for _, line in ipairs(lines) do assert(not line:find('ERROR', 1, true), line) end

-- Curse Spikes gear on a two-hit round: the round's spike status was stored
-- under a key nothing read, and printed as an ERROR.
buffNames[9] = 'curse'
local spikes = {has_spike_effect=true, spike_effect_message=374, spike_effect_param=9}
local act = {actor_id=3, category=1, param=0, targets={{server_id=1,
             actions={result(1, 30, spikes), result(1, 25, spikes)}}}}
messages = {}
gActionHandlers.parse_action_packet(act)
local cursed = 0
for _, line in ipairs(messages) do
    assert(not line:find('ERROR', 1, true), line)
    if line:find('curse', 1, true) then cursed = cursed + 1 end
end
assert(cursed == 2, 'each hit\\'s spike line names the curse, got ' .. cursed)
""")
    print(f"PASS: Geist Wall removing a status without a client name ({'simplified' if simplify else 'plain'} log)")
