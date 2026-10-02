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

Reading a VIC register costs the CPU one extra sub-slot; writing one
does not. This includes the read an NMOS 6502 makes of an indexed
store's target address before it writes it (the "dummy read"), so
`sta $d020,x` takes one sub-slot longer than `sta $d020`. An indexed
load makes the same dummy read only when the index crosses a page.

`render_frame()`'s per-line `sta $d031 (#$80)` / `stx $d031 (#$8f)`
drops to index 0 for the `stx`'s last three cycles. Each of those
finishes at the end of a phi2 cycle, so turbo resumes at the start of
phi2 cycle 4 of every line, whatever the polling jitter was. The next
line's `lda $d012` must finish before the line ends; a VIC register
read costs one extra sub-slot. At 64 MHz that leaves 60 x 63 = 3,780
cycles, and the loop uses 3,778 of them with the delay value `$87`.
That 2-sub-slot margin is consistent with the hardware bisection in
`render_frame()`'s comment, where the next value tried, `$A5`, skews.

At 64 MHz each pixel takes 8 CPU cycles. A phi2 cycle is 8 dots long
and gives the CPU 63 cycles, so a pixel is 64/63 of a dot wide: about
one pixel in 64 is 2 dots wide instead of 1. The 384 pixels span about
390 dots. The picture
starts 8 dots in from the left edge of the 384-dot visible area, so
pixels 370-383 fall beyond its right edge. Aleksi Eeben's original
viewer (CSDb releases 263889, 263980 and 264448) shows exactly one
pixel per dot, using per-line self-modifying code that this port does
not have. This port keeps the geometry it has had since v1.0.3.

## 48 MHz path (original Ultimate 64 / Elite I)

At 48 MHz, 8 cycles are 1.36 dots. The full 384-pixel line would need
384 x 8 = 3,072 cycles of pixel writes, 522 dots wide, against the
2,820 cycles a line leaves after the resync (budget below). A load
followed by a store to `$D020` (8 cycles) is the cheapest way to show
one pixel, so across the 64 MHz picture's width a 48 MHz line has time
for about 3 pixels in 4. The 48 MHz path shows exactly that: pixels
4m, 4m+2 and 4m+3 of every 4, 288 of the 384 pixels per line.

At startup `upic_select_display_path()` measures the speed and, on a
48 MHz machine, rebuilds `render_line_pixels()` in place. Each 18-byte
group shows 3 pixels from a pair of byte columns A = 2m and B = 2m+1:

| Bytes | Instruction | Cycles | Pixel shown |
|---|---|---|---|
| 0-2 | `lda A,y` | 4 | |
| 3-5 | `sta $d020` | 4 | 4m (A's low nibble) |
| 6-8 | `ldx B,y` | 4 | |
| 9-11 | `stx $d020` | 4 | 4m+2 (B's low nibble) |
| 12-14 | `lda nybbles,x` | 4 | |
| 15-17 | `sta $d020` | 4 | 4m+3 (B's high nibble) |

That is 24 cycles per 4 picture pixels, 4.09 dots, against 4.06 dots
at 64 MHz. Each shown pixel is 1.36 dots wide, so 1 or 2 dots on
screen. Pixel 4m+1 is the one left out because, once the group is
stretched this way, its neighbours overlap its dots the most. No
instruction in the group indexes into I/O space, so no dummy read of a
VIC register adds a sub-slot.

The as-built code for one byte column is 12 bytes: `ldx col,y / stx
$d020 / lda nybbles,x / sta $d020`. A group's last 12 bytes are column
B's code, unchanged, and its bytes 3-5 are the last instruction of
column A. The patcher therefore builds each group from A's bytes 0-2
(`ldx` changed to `lda`), A's bytes 9-11 and all of B, and ends the
routine with an `rts`. A group is 18 bytes and replaces 24, so the
routine is rebuilt front to back without overwriting anything it still
has to read. This needs no second copy of the renderer in the
`upiccode` pool, which has 18 bytes free.

Only 94 of the 96 groups are drawn (`UPIC_GROUPS_48`). The last two
(pixels 376-383) would start 94 x 24 = 2,256 cycles, about 384 dots,
after the first pixel. That is beyond the right edge of the 384-dot
visible area, where the 64 MHz path's pixels 370-383 are not visible
either. Leaving them out frees 48 cycles, and that time is what lets
the delay before the first pixel place the picture where the 64 MHz
picture is.

Line budget from the resync to the end of the line, with D the delay
loop passes (5 cycles each):

```
64 MHz: 2 + (5*135-1) + 6 + 3072 + 6 + 13 + 5 = 3778 of 3780
48 MHz: 2 + (5*D-1)   + 6 + 2256 + 6 + 13 + 5 = 5*D + 2287 of 2820
```

The delay (`render_frame.dly`) is patched from 135 loop passes to
`UPIC_DELAY_48` = 99. The line then uses 2,782 of its 2,820 cycles,
leaving 38 to spare. On an Ultimate 64 Elite the picture stayed intact
up to D = 108.

D = 100 would put the first pixel on the 64 MHz path's first dot. The
48 MHz pitch is 0.5% wider, which adds up to about 2 dots across the
line, so with D = 100 the position error grows from the left edge
towards the right. Measured on an Ultimate 64 Elite against a C64
Ultimate with a stripe pattern, D = 100 left the pixels 0.9 dot right
of their 64 MHz position on average and up to 2.5 dots at the right.
D = 99 moves the picture one delay pass (0.85 dot) left, which centres
the error: every shown pixel lies within 1.5 dots of where the 64 MHz
path shows the same pixel, 0.5 dot away on average, and the picture
starts 1 dot left of the 64 MHz picture.

The picture buffer and `zoom.c` are unchanged. The corner markers are
2 pixels wide and only one pixel in 4 is left out, so each marker
covers at least one shown pixel and stays visible. The generator is
unchanged except for how often it shows a live frame:
`upic_select_display_path()` also sets `upic_frame_quarters` to 3
(see `docs/MANDELBROT_ALGORITHM.md`'s live build-up section).

### First version of this path (not released)

The first version (2026-09-21) showed one pixel per byte column with
`lda col,y / sta $d020,x / jmp next`, 12 cycles per column on paper,
and a delay of 96. On a real Ultimate 64 Elite every row landed two
raster lines apart and the picture alternated between two frames:
double height, black lines between rows, heavy flicker. The cause was
the dummy read of `sta $d020,x` described above. It made each column
13 cycles instead of 12, so a row overran its line. The largest delay
that worked on hardware was 56, not the 97 the budget predicted. The
host-side emulator did not model the dummy read at the time;
`tests/mos6502.py` now does, and with it the timing model reproduces
the hardware bisection exactly (56 fits, 57 does not).

### Speed probe

`upic_select_display_path()` calls `uii_turbo_probe_max()` from the
ultimate-uci-oscar64 library (`ultimate_turbo_lib`), a port of the
probe Christian Gleissner originally wrote in this file for v1.1.0. It
times a fixed 64,764-cycle loop against `$D012`, which advances once
per real PAL line at any CPU speed: 16.3 lines at 64 MHz, 21.9 lines at
48 MHz. Every loop-back is an absolute `jmp`, so the cycle count does
not depend on where the linker places the function. The result is
stored in `upic_probe_class` (0 = 48 MHz, 1 = 64 MHz, 2 = no result),
which the tests and `make e2e` read.

The Ultimate 64 runs the CPU at 1 MHz for a few seconds after every
CPU reset, whatever `$D031` says, and briefly after IEC bus activity.
When the program is started from the Ultimate menu the probe runs
inside the first of those windows. A whole loop at 1 MHz
spans 1,028 lines and always ends at `$7C`, which is rejected as
invalid. A loop during which the window ends can end on any line, so
a result is only accepted once two loops in a row give the same
answer. The 48 MHz answer rebuilds the renderer; the 64 MHz answer
changes nothing.

Each retry writes `$8F` (top speed index, badlines off) to `$D031`
again, and the library restores `$D030`/`$D031` afterwards
(`render_frame()` rewrites `$D031` on every line anyway). If the speed register was reset after `uii_turbo_fast()`, the
probe therefore still reaches full speed instead of looping at 1 MHz.
If no result is accepted within 256 loops (about 20 s at 1 MHz, for
example because turbo stays off when the program is started without
its `.cfg`), the probe gives up and keeps the unpatched 64 MHz path
instead of waiting forever with a black screen. The program then
generates and displays at 1 MHz with a garbled picture, as v1.0.3 did
without turbo (checked on a C64 Ultimate with `Turbo Control` set to
manual 1 MHz: the 256 loops took 21 s, with the in-project version of
the probe). The probe gives up to the 64 MHz path even when the last
loop read 48 MHz, because a single reading is never accepted on its
own. When the turbo registers aren't present at all (`$D031` reads
`$FF`), the library returns at once instead of looping.

### Checking the 48 MHz path on a 64 MHz machine

`make force48` builds a test-only PRG that skips the probe, always
rebuilds the renderer, and also patches the per-line turbo byte
(`render_frame.trb`) from `$8F` to `$8E`. Index 14 is 48 MHz on Elite
II / C64U. How its 48 cycles per phi2 are spread within each phi2 may
differ from a U64's top speed, so this build checks the 48 MHz path
closely but not identically. On a real C64 Ultimate every row of the
force48 build started on the same dot (50 frames checked,
2026-09-28). On an original Ultimate 64 the release PRG keeps `$8F`,
since index 15 is already 48 MHz there.

### Status

The 48 MHz path was checked on an Ultimate 64 Elite (firmware 3.15)
against a C64 Ultimate (firmware 1.2RC) running the same PRG on
2026-09-28. The pixel positions above were measured with a stripe
pattern. The end-to-end test (`tests/e2e/`) captured 7 test pictures
from both machines' video streams and, comparing them through the dot
maps measured from that pattern, found no differing dots.

`make test` (`tests/`) checks the rebuild, the line budget, the probe
and its forced-1 MHz, lost-speed and give-up handling by running the
compiled code against a model of the timing behaviour above. The
model reproduces the `$87` / `$A5` bisection at 64 MHz, the 56 / 57
bisection of the first 48 MHz layout, and the position errors measured
for delays 98, 99 and 100.

The shipped `.cfg` (`config/MandelbrotUpic.cfg`) contains both
`Turbo Control=U64 Turbo Registers` and `Turbo Control=C64U Turbo
Registers`: each product's firmware skips the value name it doesn't
know and applies the other, and the rest of the file still loads.
Verified on an Ultimate 64 Elite II (firmware 3.15a): starting from
`Turbo Control=Off`, auto-loading the file set `U64 Turbo Registers`
with nothing shown on screen. The C64 Ultimate side is not yet
verified. See the installation notes in `README.md`.

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
  is filled (or being filled -- generation calls this once per column
  on the 64 MHz path and once per 3/4 column on the 48 MHz path to
  show live build-up, see `docs/MANDELBROT_ALGORITHM.md`).
  Handles the one-time ROM-bank/nybble-table setup itself, then renders
  exactly one frame. Its return value (whether SPACE is currently held)
  is unused by this project's own control scheme.
- **`upic_select_display_path()`**: call once at startup, after
  `rombank_out()` and `uii_turbo_fast()`, with interrupts masked, before
  the first `upic_show_frame()`. Runs the speed probe and, on a 48 MHz
  machine, rebuilds the renderer (see the 48 MHz path section above).
- **`upic_frame_quarters`**: how much of a column the generator
  computes between two live frames, in quarters. 4 on the 64 MHz path;
  `upic_select_display_path()` sets it to 3 on the 48 MHz path.
- **`upic_restore_display()`**: restores standard `DEN=1` text-mode
  display, default border/background colors, and clears leftover
  KERNAL keyboard-buffer/STOP-key state, for a caller that needs a
  graceful return to BASIC. Not called by this project (see
  `docs/ZOOM_FEATURE.md` for why there's no quit key).

## Memory regions (this project's specific layout)

| Region | Range | Contents |
|---|---|---|
| `startup` | `$0801`-`$0853` | BASIC stub + Oscar64 startup code |
| `main` | `$0853`-`$1800` | Default code/data/bss/heap/stack -- `mandelbrot_generate()`, UCI functions, `main()` itself, `zoom_out_view()`, `upic_select_display_path()`. 323 bytes free in the current build: BSS ends at `$166D`, the stack section (`stacksize` 80) starts at `$17B0` |
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
project depends on, so the picture itself can only be checked on real
Ultimate hardware: by eye, or with `make e2e`, which runs the release
PRG on one or more devices, captures the picture from the Ultimate's
VIC video stream and compares it with golden images. See
`tests/e2e/README.md`.

`make test` covers the timing that can be checked without a screen:
it runs the compiled `render_frame()`/`render_line_pixels()` for a
full frame at 64 MHz and (rebuilt) at 48 MHz and checks that every row
stays inside one raster line, rows land on consecutive lines, pixels
are 1 or 2 dots wide, the 48 MHz path shows pixels 4m, 4m+2 and 4m+3,
and its delay keeps every shown pixel within 1.5 dots of where the
64 MHz path shows it. See `tests/README.md`.
