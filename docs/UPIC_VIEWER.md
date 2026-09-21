# Upic picture viewer

How `include/upic_viewer.c`/`upic_viewer.h` display a 384x256, 16-color
picture using the Upic border-color raster technique -- no bitmap
mode, no sprites, just the VIC-II border color changed at the right
moment on every scanline.

See `CREDITS.md` for attribution: the technique itself is Aleksi
Eeben's; this is a from-scratch Oscar64/C port for the Ultimate 64.

## The technique

With the VIC-II's display enable bit (`DEN`, `$D011` bit 4) held at 0,
the whole visible area becomes "border" -- and the border color
(`$D020`) can be changed by the CPU on every scanline, faster than the
VIC-II can settle into a stable color, producing a visible pixel for
each change. `render_frame()` holds `DEN=0` for the picture's entire
256-row height and, on each scanline, writes one packed picture byte's
low nibble to `$D020` (as a raw byte value, exploiting nibble/color-
index equivalence), then that same byte's high nibble via a lookup
table, producing 2 horizontal pixels per byte per scanline. This is
cycle-exact, hand-tuned 6502 assembly -- `render_line_pixels()` is a
single, fully unrolled sequence (no loop) so its per-pixel timing
never drifts from the raster beam's own position.

`render_frame()` is called once per displayed frame
(`upic_show_frame()`); the whole call is `SEI`-protected in this
project (see `main.c`'s own comment on the global interrupt mask) so
nothing can interrupt the cycle-exact timing mid-scanline.

## CPU timing on the Ultimate 64

In turbo mode the Ultimate 64 does not simply run the CPU from a
faster clock. Each phi2 cycle offers as many CPU sub-slots as the
board's top speed: 64 on Elite II / C64U, 48 on the original Ultimate
64 / Elite I. The `$D031` speed index selects how many of them the CPU
may use: all of them at the top index, one per phi2 at index 0
(1 MHz). The VIC-II uses one sub-slot of every phi2 for its own memory
access, so the CPU gets:

| Board | Index 15 | CPU cycles per phi2 | Cycles per PAL line (63 phi2) |
|---|---|---|---|
| Elite II / C64U | 64 MHz | 63 | 3,969 |
| U64 / Elite I | 48 MHz | 47 | 2,961 |

A line is 504 dots. A `$D020` write shows up at the next dot boundary.

`render_frame()`'s per-line `sta $d031 (#$80)` / `stx $d031 (#$8f)`
drops to index 0 for the `stx`'s last three cycles. Each of those
finishes at the end of a phi2 cycle, so turbo resumes at the start of
phi2 cycle 4 of every line, whatever the polling jitter was. The next
line's `lda $d012` must finish before the line ends; a VIC register
read costs one extra sub-slot. At 64 MHz that leaves 60 x 63 = 3,780
cycles, and the loop uses 3,778 of them with the delay value `$87`.
That 2-sub-slot margin is consistent with the hardware bisection in
`render_frame()`'s comment, where the next value tried, `$A5`, skews.

## 48 MHz path (original Ultimate 64 / Elite I)

At 48 MHz the unchanged line needs 192 x 16 = 3,072 cycles of pixel
writes alone, against 2,961 cycles in a whole line, so the full
horizontal resolution cannot be shown. The 48 MHz path shows each
packed byte's low nibble (the even pixel) as one pixel about 2 dots
wide.

At startup `upic_select_display_path()` measures the speed and, on a
48 MHz machine, patches `render_line_pixels()` in place, keeping its
12-byte stride per byte column:

| Bytes | 64 MHz (as built) | Cycles | 48 MHz (patched) | Cycles |
|---|---|---|---|---|
| 0-2 | `ldx col,y` | 4 | `lda col,y` | 4 |
| 3-5 | `stx $d020` | 4 | `sta $d020,x` (X = 0) | 5 |
| 6-8 | `lda nybbles,x` | 4 | `jmp` next column | 3 |
| 9-11 | `sta $d020` | 4 | (skipped) | |

That gives one border-colour write every 12 cycles, 2,304 cycles per
line. `sta abs,x` and `jmp` are used because their cycle counts are
fixed, independent of data and of where the column lands in memory.
Because 47 cycles per phi2 don't divide evenly into 12-cycle steps,
about one pixel in 24 is 3 dots wide instead of 2. The 64 MHz path has
the same effect: about one pixel in 64 is 2 dots wide instead of 1.

The delay before the first pixel (`render_frame.dly`) is patched from
135 loop passes to `UPIC_DELAY_48` = 96. With 5 cycles per pass the line
then uses 5 x 96 + 2,335 = 2,815 of its 2,820 cycles after the resync,
leaving 5 sub-slots to spare, against 2 for the 64 MHz path. 97 would
leave 0, and 98 does not fit. With 96 the 48 MHz picture starts 3 dots
left of the 64 MHz picture and ends 1 dot short of it. The picture
buffer, the generator and `zoom.c` are unchanged. The corner markers
are 2x2 pixels, so each always covers one even pixel and stays
visible.

### Speed probe

`upic_select_display_path()` times a fixed 64,764-cycle loop against
`$D012`, which advances once per real PAL line at any CPU speed:
16.3 lines at 64 MHz (`$D012` ends at `$30`), 21.9 lines at 48 MHz
(`$35`). Every loop-back is an absolute `jmp`, so the cycle count does
not depend on where the linker places the function.

The Ultimate 64 runs the CPU at 1 MHz for a few seconds after every
CPU reset, whatever `$D031` says, and briefly after IEC bus activity.
When the program is started from the Ultimate menu the probe runs
inside the first of those windows. A whole loop at 1 MHz
spans 1,028 lines and always ends at `$7C`, which is rejected as
invalid. A loop during which the window ends can end on any line, so
a result is only accepted once two loops in a row give the same
answer. The 48 MHz answer patches the renderer; the 64 MHz answer
changes nothing.

### Checking the 48 MHz path on a 64 MHz machine

`make force48` builds a test-only PRG that skips the probe, always
patches the renderer, and also patches the per-line turbo byte
(`render_frame.trb`) from `$8F` to `$8E`. Index 14 is 48 MHz on Elite
II / C64U. How its 48 cycles per phi2 are spread within each phi2 may
differ from a U64's top speed, so this build checks the 48 MHz path
closely but not identically. On an original Ultimate 64
the release PRG keeps `$8F`, since index 15 is already 48 MHz there.

### Status

The patch, the line budget, the probe and its forced-1 MHz handling
are checked by `make test` (`tests/`), which runs the compiled code
against a model of the timing behaviour above. The model reproduces
the `$87` / `$A5` hardware bisection. None of the 48 MHz behaviour has
been seen on real hardware yet. The picture's appearance, in
particular whether its left edge (3 dots further left than the 64 MHz
picture's) is still inside the visible area, needs a real screen.

## Picture buffer: split across two locations

The picture is 384x256 pixels, 2 pixels packed per byte (low nibble =
even column, high nibble = odd column), 192 byte-columns x 256 rows,
stored column-major: byte-column `c`, row `y` is at
`buffer[c*256 + y]`.

The full buffer (98,304 nibbles = 49,152 bytes) doesn't fit in one
contiguous region alongside everything else this project needs, so
it's split:

- **`upic_buffer`** (columns `UPIC_RELOC_COLS`..191, `UPIC_MAIN_BYTES`
  = 47,104 bytes): `$1800`-`$CFFF`. Ordinary RAM, always accessible.
- **`upic_buffer_reloc`** (columns 0..`UPIC_RELOC_COLS`-1,
  `UPIC_RELOC_BYTES` = 2,048 bytes, `UPIC_RELOC_COLS` = 8): `$E000`-
  `$E7FF`. Requires ROM banked out (`rombank_out()`/`MMAP_NO_ROM`) to
  read/write correctly, since it overlaps where the KERNAL ROM would
  otherwise be mapped.

`render_line_pixels()`'s own hardcoded per-column addresses
(`$E000,y`, `$E100,y`, ... for the first 8 columns, then `$1800,y`
onward for the rest) must stay in sync with this split -- both are
generated from the same `UPIC_RELOC_COLS` constant, not independently
maintained.

## Nybble lookup table

`render_line_pixels()` needs each packed byte's HIGH nibble shifted
down to become a plain 0-15 value for the second pixel's border-color
write. Rather than a runtime shift in the hot per-scanline loop,
`nybbles[i] = i >> 4` is a precomputed 256-entry lookup table, built
once (`init_nybbles()`, called lazily on the first `upic_show_frame()`
call) and placed in the otherwise-idle `ovl1` overlay region
(`$0200`-`$0800`) rather than the tight shared code/data/bss pool at
`$E800`-`$FFFF`.

## API

- **`upic_show_frame()`**: call once per frame after the picture buffer
  is filled (or being filled -- generation can call this once per
  column to show live build-up, see `docs/MANDELBROT_ALGORITHM.md`).
  Handles the one-time ROM-bank/nybble-table setup itself, then renders
  exactly one frame. Its return value (whether SPACE is currently held)
  is unused by this project's own control scheme.
- **`upic_restore_display()`**: restores standard `DEN=1` text-mode
  display, default border/background colors, and clears leftover
  KERNAL keyboard-buffer/STOP-key state, for a caller that needs a
  graceful return to BASIC. Not called by this project (see
  `docs/ZOOM_FEATURE.md` for why there's no quit key).

## Memory regions (this project's specific layout)

| Region | Range | Contents |
|---|---|---|
| `startup` | `$0801`-`$0853` | BASIC stub + Oscar64 startup code |
| `main` | `$0853`-`$1800` | Default code/data/bss/heap/stack -- `mandelbrot_generate()`, UCI functions, `main()` itself, `zoom_out_view()`, `upic_select_display_path()`. 0 bytes free: BSS ends exactly where the stack section starts (`$17B8`) |
| `ovl1` | `$0200`-`$0800` | `nybbles[]` lookup table (permanently, not used as a swappable overlay in this project) |
| `upic_buffer` | `$1800`-`$D000` | Picture buffer, columns 8-191 |
| `upic_buffer_reloc` (`picreloc`) | `$E000`-`$E800` | Picture buffer, columns 0-7 |
| `upiccode` (shared pool) | `$E800`-`$10000` | Most of `zoom.c`, `mandelbrot.c`'s `sq_table`/`cy2_table`, `render_frame()`/`render_line_pixels()` |

The `upiccode` pool is shared code+data+bss (not just code, despite the
name) and is the tightest budget in this project -- see
`docs/ZOOM_FEATURE.md`'s own memory-layout section for its current
usage and the silent-linker-wraparound risk near its `$10000` boundary.

## Testing

VICE doesn't emulate the Ultimate's own UCI/turbo hardware this
project depends on, so manual/visual testing on real Ultimate 64
hardware is the only way to confirm how the picture actually looks.

`make test` covers the timing that can be checked without a screen:
it runs the compiled `render_frame()`/`render_line_pixels()` for a
full frame at 64 MHz and (patched) at 48 MHz and checks that every row
stays inside one raster line, rows land on consecutive lines, the
pixel pitch is 1 or 2 dots, and both paths have the same left and
right picture edges. See `tests/README.md`.
