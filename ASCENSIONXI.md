# AscensionXI SimpleLog maintenance

The `ascensionxi` branch of `henkpoa/SimpleLog` is the launcher source for
AscensionXI. Runtime files are ThornyFFXI/SimpleLog commit
`ce015e15178533815fb19274e9772ecfad771f61` (0.1.3) plus the AscensionXI fixes
below, each marked `AscensionXI` in `lib/packethandlers.lua`. Keep the original
credits, LICENSE, and per-file notices.

## AscensionXI fixes

- **AXI-126, a status with no client name.** Our server's own statuses
  (Judgement, effect 828, among them) can have no entry in the client's buff
  name table. When one wore off, the condensed wear-off path used the missing
  name as a table key and the addon unloaded with
  `packethandlers.lua:191: table index is nil`. Such a line now skips
  condensing and prints as the game prints it.
- **AXI-89, actions dropped after a repeated packet.** The duplicate filter
  recognised the start of a packet chunk by comparing each packet with the
  chunk's first packet. Our server can put the same packet twice into one
  chunk. The repeat was taken for a new chunk, the chunk being read was filed
  as already seen, and every later action packet (0x028) and action message
  (0x029) in it was dropped: no animation, no log line, the damage still
  dealt. A packet at the position where the current chunk's next packet
  belongs now always continues that chunk. Resent chunks are still dropped.
  The tests prove the flaw and the fix on synthetic chunks. Whether this was
  every AXI-89 report is not proven: nobody captured a failing chunk.

The previous launcher pin was Spike2D/SimpleLog
`decc2f66a5072e9650dee7d95ece07a45664e41b` (0.1.2). Thorny's eleven subsequent
commits fix initialization, crafting's absent Self value, Ashita 4.30 ImGui
calls/stack balancing, party/alliance and pet/trust lookups, and opt into
packet chunks on addon interface 4.3+. No additional crash fix was invented here.

## Verification

From this checkout, with Python and `lupa` installed:

```powershell
python -m pip install lupa
python tests/smoke.py
python tests/smoke.py --ref decc2f66a5072e9650dee7d95ece07a45664e41b
```

The current code passes LuaJIT compilation of all 17 Lua files, synthetic
own/targeted/unselected crafting-result messages, server-id and pet/fellow
ownership lookup, all 18 party/alliance slots, a chunk holding a repeat of
its first packet (and the resend of that chunk), and a wear-off for a status
without a client name. The old pin deliberately fails the crafting case with
`attempt to index global 'Self' (a nil value)`. `--ref 3215adf` (before the
AscensionXI fixes) fails the chunk case. Removing the `HasStatusName` guard
fails the wear-off case with the reported `table index is nil`.
These are real Lua modules with stubbed Ashita interfaces, not live-client
tests or captured retail packets. They do not test all combat messages,
rendering, native memory, or transport.

September 22, 2026 report: owner can load the existing addon; crashes are
reported by others, with no error or exact trigger supplied. A log-processing
failure is suspected, not proven. No SimpleLog traceback was found in the
owner's Ashita logs. Do not describe the reported crash as resolved without
a client playtest or a matching capture/error.

## Updating the installation

The server repo owns `client/addons/catalog.json`. Its `simplelog` source
pins a full commit on this branch; `tests` and `ASCENSIONXI.md` are excluded
from player downloads. Update only that source ref and SimpleLog metadata,
then run `python tools/addon-catalog.py resolve --only simplelog` and
`python tools/addon-catalog.py verify`. Submit a server topic PR; agents
never merge or deploy. The existing SimpleLog name, off-by-default setting,
and per-character files under `Ashita/config/addons/simplelog` are retained.

After the human release, sync the launcher, enable SimpleLog, load a character,
open/close `/slog`, and check ordinary attacks, misses, weapon skills, spells,
status messages, Trust combat, crafting (self and a targeted other character),
zoning and job changes. Record the precise message/action and Ashita error
if it fails. The owner operates game clients.

The server's full handoff is `documentation/custom/simplelog-addon-fork.md`.
Rollback is a reviewed catalog change restoring the old pin and generated
file map; disabling SimpleLog in the launcher is the immediate workaround.
