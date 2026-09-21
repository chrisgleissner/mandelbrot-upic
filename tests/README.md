# Host-side tests

`make test` builds `build/mandelupic.prg` and the test-only
`build/mandelupic-force48.prg`, then runs:

```
python3 -m unittest discover -s tests -v
```

Python 3 standard library only, no packages to install. The tests read
the PRGs and their Oscar64 `.map` files from `build/` and take about
25 seconds.

| File | Contents |
|---|---|
| `mos6502.py` | Cycle-counting NMOS 6502 emulator (documented opcodes, page-crossing and branch penalties). Raises on undocumented opcodes and decimal mode. |
| `machine.py` | Loads a PRG plus `.map` and models the Ultimate 64's turbo CPU timing, raster counter and `$D020`/`$D031` registers. |
| `test_turbo_modes.py` | Tests for the 48 MHz / 64 MHz display paths and the speed probe in `upic_select_display_path()`. |

## Timing model

`machine.py` models the Ultimate 64's turbo CPU timing as behaviour;
its docstring lists the rules. In short:

- Each phi2 cycle offers as many CPU sub-slots as the board's top
  speed (64 on Elite II / C64U, 48 on U64 / Elite I). A speed of k MHz
  lets the CPU use k of them, evenly spaced.
- The VIC-II uses one sub-slot of every phi2 (and one more on badline
  character fetch cycles while the display is on).
- A `$D031` write takes effect two sub-slots later. A VIC register
  read needs one extra sub-slot. `$D012` changes at the start of phi2
  cycle 1.
- A `$D020` write shows at the next dot boundary.
- The 1 MHz window after a reset can be switched on up to a given
  sub-slot.

Not modelled: CIA timing, sprite DMA, NTSC. How intermediate speeds
(such as index 14 on an Elite II, used by the force48 build) spread
their cycles within a phi2 is modelled only approximately.

The model is checked against real hardware in one place: the
bisection recorded in `render_frame()`'s comment, where delay `$87`
works on an Elite II and `$A5` skews. `test_model_matches_hardware_bisection`
requires the model to agree. The model also predicts that `$88` would
already fail, which has not been tried on hardware.

## What the tests check

- The release PRG's 64 MHz render code is byte-for-byte the v1.0.3
  instruction layout, and `render_frame`'s `dly`/`trb` operands are at
  the offsets the patcher and tests assume.
- `render_frame`'s delay loop does not cross a page (which would add a
  cycle per pass), and `nybbles` is page-aligned.
- `upic_select_display_path()` changes nothing on an Elite II and
  exactly the intended bytes on a U64, from several starting raster
  lines, and still does so when the forced 1 MHz window ends at
  various points during the probe.
- A full 256-row frame at 64 MHz (Elite II) and, after patching, at
  48 MHz (U64 at index 15; Elite II at index 14 in the force48 build):
  every row stays inside one raster line, rows land on consecutive
  lines, the next row's `$D012` poll finishes before its line ends,
  and pixel widths are 1-2 dots (64 MHz) or 2-3 dots (48 MHz).
- The 48 MHz path has at least the 64 MHz path's per-line slack, its
  edges are within 3 dots (left) and 1 dot (right) of the 64 MHz
  picture's, and one more delay pass would lose that slack.
- Negative controls: delay `$A5` at 64 MHz and the unpatched code at
  48 MHz both fail the row checks.

Mutations tried while writing the tests, each caught: leaving the
delay unpatched, `sta abs` instead of `sta abs,x` in the patch, and
disabling the probe's rejection of forced-1 MHz results (an Elite II
then gets the 48 MHz path).
