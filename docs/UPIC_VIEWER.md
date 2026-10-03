# Upic picture viewer

How this program shows its 384x256, 16-color picture with the Upic
border-color raster technique -- no bitmap mode, no sprites, just the
VIC-II border color changed at the right moment on every scanline.

The technique is Aleksi Eeben's (Upic, <https://csdb.dk/release/?id=263889>).
Since v1.2.0 the display itself is the Upic module of the
ultimate-uci-oscar64 library (`lib/ultimate-uci-oscar64`, see its
`docs/UPIC_MANUAL.md` for the renderer in full); `include/upic_viewer.c`
is a thin layer over it. See `CREDITS.md` for attribution.

## The technique

With the VIC-II's display enable bit (`DEN`, `$D011` bit 4) held at 0,
the whole visible area becomes "border", and the CPU changes the border
color (`$D020`) once per pixel, timed against the raster beam. Each
packed picture byte holds two pixels: its low nybble is written to
`$D020` directly (the register uses 4 bits), its high nybble through a
256-byte lookup table (`nyb[i] = i >> 4`). The line routine is fully
unrolled and cycle-exact. In browse mode it runs polled with interrupts
masked; while a picture is computed in the Bar or Full live view it runs
from the library's raster interrupt instead.

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

A phi2 cycle is 8 dots; a `$D020` write shows up at the next dot
boundary. Reading a VIC register costs the CPU one extra sub-slot;
writing one does not. That includes the dummy read an NMOS 6502 makes
of an indexed store's target (`sta $d020,x`), which is why the renderer
makes no indexed accesses to I/O. The CPU also runs at 1 MHz for a few
seconds after every reset, whatever `$D031` says. `tests/machine.py`
models all of this.

## Exact pixel pitch (v1.2.0)

At 64 MHz the CPU gets 63 cycles per 8 dots, so a pixel written every
8 cycles is 64/63 of a dot wide. Until v1.1.1 this program's own port
of Upic did exactly that: the 384 pixels spanned about 390 dots, the
picture started 8 dots in, and pixels 370-383 fell beyond the right edge.
Aleksi's Upic v1.3 renderer avoids it by loading every 8th pixel pair
from an immediate operand that a short routine patches with the current
row's byte before each line: 2 cycles less per 16 pixels, so 16 pixels
take 126 cycles, exactly 16 dots. The original port had left that patch
step out deliberately ("barely affects the frame's cycle budget") --
it is what makes the pitch exact.

The 48 MHz path (Christian Gleissner, v1.1.0) shows 3 of every 4 pixels:
per two byte columns `lda A,y / sta $d020 / ldx B,y / stx $d020 /
lda nyb,x / sta $d020`, 24 cycles for pixels 4m (two dots wide, covering
the skipped 4m+1), 4m+2 and 4m+3. At 47 cycles per phi2 that is 4.085
dots per group; the library patches every 4th group the same way
(`lda #imm`), so 16 pixels take 94 cycles, exactly 16 dots. Inside a
16-pixel span a pixel can still land one dot off its ideal position.

Both paths now put pixel 0 on the first visible dot and fill all 384
dots; every 24-pixel color-bar edge falls on the same dot on both
(measured from the VIC video stream on an Ultimate 64 Elite II and an
Ultimate 64 Elite, firmware 3.15a, 2026-10-02).

Two smaller details from that work, both in the library: the line ends
with two `NOP`s before the "rest of the line black" store (without them
the last pixel was overwritten before it showed), and the renderer is
generated at startup from the column addresses, so it works with this
program's split picture buffer.

## Choosing the path

`upic_select_display_path()` runs once at startup and calls
`uii_upic_init(UII_UPIC_AUTO)`. That measures the CPU's top speed with
`uii_turbo_probe_max()` -- Christian Gleissner's raster-timed probe,
first written in this file for v1.1.0 and moved to the library after
v1.1.1 -- and generates the 48 or 64 MHz renderer. The probe times a
64,764-cycle loop against `$D012` (16.3 lines at 64 MHz, 21.9 at
48 MHz), rejects loops run during the forced 1 MHz window, accepts a
result only when two loops agree, rewrites `$D031` on every retry, and
gives up after 256 loops (about 20 s at 1 MHz, e.g. started without the
`.cfg`), keeping the 64 MHz renderer. The result is stored in
`upic_probe_class` (0 = 48 MHz, 1 = 64 MHz, 2 = none), which the tests
and `make e2e` read; on the 48 MHz path `upic_frame_quarters` becomes 3
(see `docs/MANDELBROT_ALGORITHM.md`, live build-up).

`make force48` builds a test-only PRG that always takes the 48 MHz path
at speed index 14 (`$8E`), which is 48 MHz on an Elite II / C64U. How
those cycles are spread within each phi2 differs from a real 48 MHz
machine, so it exercises the path's code, not its exact geometry.

### History of the 48 MHz path

The first version (2026-09-21) showed one pixel per byte column with
`lda col,y / sta $d020,x`, 12 cycles per column on paper. On a real
Ultimate 64 Elite every row landed two raster lines apart and the
picture alternated between two frames: the dummy read of `sta $d020,x`
made each column 13 cycles. `tests/mos6502.py` has modelled that dummy
read since. The released v1.1.0 version used the 3-of-4 layout above,
at a pitch 0.5% wider than the 64 MHz one (within 1.5 dots of the
64 MHz position); v1.2.0 made both exact.

## Picture buffer layout

The picture is 192 byte columns of 256 bytes: byte column `c` holds
pixels `2c` (low nybble) and `2c + 1` (high nybble), rows 0-255,
column-major -- the same layout as Aleksi's `.upic` files. Column `c` is
at `$1000 + c * 256`, except columns 0-7 at `$E000 + c * 256`, because
this program's main code occupies `$0853`-`$17FF`. Use `upic_column(c)`
(the library's `uii_upic_column()`) for addresses; the library is built
with `-dUII_UPIC_RELOC_COLS=8 -dUII_UPIC_RELOC_BASE=0xE000`.

Columns 0-7 and the generated renderer are under the KERNAL ROM, which
stays banked out for the whole program (`rombank.h`).

## API

- **`upic_select_display_path()`**: once at startup, after
  `rombank_out()` and `uii_turbo_fast()`, with interrupts masked. Lives
  in the startup-only `initcode` region (see below).
- **`upic_show_frame()`**: draws one frame (`uii_upic_show_frame()`),
  then polls the keyboard matrix; returns nonzero while SPACE is down
  (unused by this program's controls). The generator calls it during
  generation for the live build-up, `zoom.c` between key checks.
- **`upic_frame_quarters`**: 4 on the 64 MHz path, 3 on the 48 MHz path.
- **`upic_column(c)`**: byte column address.

- **`upic_live_begin()` / `upic_live_column()` / `upic_live_end()` /
  `upic_live_cycle()`**: the live view while generating (Bar, Classic,
  Full; `V`), built on the library's raster-IRQ viewer and display
  window. See `docs/MANDELBROT_ALGORITHM.md`.

Saving (F1) uses the library's `uii_upic_save()`; see `main.c` and the
README.

## Memory regions

All sections and regions are declared in `include/memmap.h`, which
`main.c` includes first (the libraries are separate translation units
placed in these sections through `-d` flags in the Makefile).

| Region | Range | Contents |
|---|---|---|
| `startup` | `$0801`-`$0853` | BASIC stub + Oscar64 startup code |
| `main` | `$0853`-`$1800` | Default code/data/bss/stack: the generator, UCI and file functions, `main()`, `zoom_out_view()`, the live view's per-column and IRQ code. 30 bytes free (stack 80 at `$17B0`) |
| `upicbuf` | `$1800`-`$C800` | Picture columns 8-183 (reserved) |
| `initcode` | `$C800`-`$D000` | Startup-only code and data: the renderer generator, the speed probe, the turbo module, `program_startup()`, the gradients, the color table and the constant `.upic` text lines (copied to `ovl1` at startup); afterwards picture columns 184-191 (cleared by `main()` once startup is done) |
| `picreloc` | `$E000`-`$E800` | Picture columns 0-7 |
| `upiccode` | `$E800`-`$10000` | The generated renderer (`upicgen`, 2440 bytes, first so it starts page-aligned at `$E800`), `zoom.c`, `sq_table`, `save_picture()`, `upic_live_end()`/`upic_live_cycle()`, the library's display and file code; 82 bytes free |
| `ovl1` | `$0200`-`$0800` | bss only: the nybble table (`$0300`), `cy2_table`, the UCI buffers (command buffer shrunk to 64 bytes with `-dUII_COMMAND_MAX=64`), the save text, the RAM copies of the gradients and the color table |

`ovl1` is declared as an Oscar64 overlay region on purpose: a plain
region below `$0801` moved the `.prg`'s load address to `$0002`
(2026-09-09). Check `xxd -l2 build/mandelupic.prg` (must start `01 08`)
after changing it. The `upiccode` pool is the tightest budget; Oscar64
can wrap an object past `$10000` instead of reporting an error, so check
the `.map` after any change there.

## Testing

VICE doesn't emulate the Ultimate's UCI and turbo hardware, so the
picture itself can only be checked on real hardware: by eye, or with
`make e2e`, which runs the release PRG on one or more devices, captures
the VIC video stream and compares every step with golden images, and
checks the exact geometry of both paths from a stripe pattern. See
`tests/e2e/README.md`.

`make test` runs the compiled code in a cycle-counting model of the U64
(`tests/machine.py`): the probe's choice (including the forced 1 MHz
window, a lost speed setting and giving up), the generated renderers'
pixel positions on both paths, and the line budget. See
`tests/README.md`.
