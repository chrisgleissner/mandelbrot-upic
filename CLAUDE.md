# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

**Mandelbrot Upic** — an Ultimate 64 demo that generates a
Mandelbrot fractal on-device at 64 MHz turbo (48 MHz on an original
Ultimate 64 / Elite I, where the display shows 3 of every 4 pixels,
auto-detected at startup -- see `docs/UPIC_VIEWER.md`'s 48 MHz
section), packs it directly into Upic format (a 16-color, 384x256 border-color raster picture
technique), displays it live as it renders, and lets the user
interactively pan and zoom into any region of the result. Targets
**Ultimate firmware 3.15 or newer only** (no fallback path for older
firmware). Confirmed on an Ultimate 64 Elite II (64 MHz path) and an
Ultimate 64 Elite (48 MHz path), both firmware 3.15a. The C64 Ultimate
needs its palette-control firmware release (expected 1.2, not yet out
as of v1.1.1); PR #2's author ran the PRG on a 1.2 release candidate.

**Status**: v1.2.0, feature-complete. See `README.md` for controls and
installation, `docs/ARCHITECTURE.md` for the project layout,
`docs/MANDELBROT_ALGORITHM.md` for the fractal generator's design,
`docs/UPIC_VIEWER.md` for the display technique, and
`docs/ZOOM_FEATURE.md` for the interactive pan/zoom control scheme.
See `CREDITS.md` for full attribution.

## Toolchain (summary — see `docs/ARCHITECTURE.md` and the global
`~/.claude/CLAUDE.md`'s Oscar64 section for detail)

**Oscar64**, a C99/C++ cross-compiler targeting 6502/C64 — see
`docs/OSCAR64_MANUAL.md` (project copy of the canonical manual) before re-researching compiler
behavior.

- `make` / `make all` — compiles to `build/mandelupic.prg`,
  regenerates `README.pdf`, builds the release ZIP
- `make deploy` — FTP the compiled `.prg` to the Ultimate device set in
  `.env` (copy from `.env.example`, sets `ULTIP1`)
- `make docs` — regenerates `README.pdf` via pandoc
- `make test` — builds the release and `force48` PRGs and runs the
  host-side tests in `tests/` (Python 3 standard library only)
- `make force48` — test-only PRG that always uses the 48 MHz display
  path, for checking it on an Elite II / C64U
- `make e2e` / `make e2e-update` — end-to-end test on the real devices
  in `.env`'s `E2E_DEVICES`: compares (or rewrites) the golden images
  in `tests/e2e/golden/`. Resets the devices it runs on. See
  `tests/e2e/README.md`

## Firmware 3.15+ features this demo is built around

- **UCI cartridge-side auto-enable**: `uii_wait_for_uci()` in the
  library's `ultimate_common_lib.c` sends the unlock sequence when the
  UCI isn't already mapped (sending it while the UCI is mapped caused
  a start-up hang -- see the library's `docs/UCILIB_MANUAL.md`). On an Ultimate 64 Elite
  (firmware 3.15) the unlock did not bring the UCI up with "Command
  Interface" disabled, so the palette is not pushed there;
  `config/MandelbrotUpic.cfg` enables the interface, alongside the
  turbo registers. It lists `Turbo Control` twice (`U64 Turbo
  Registers` and `C64U Turbo Registers`): each firmware skips the value
  name it doesn't know, so one `.cfg` serves both product lines --
  don't "clean up" the duplicate line.
- **Palette control**: `uii_getpalette()`/`uii_setpalette()`/
  `uii_setpalettecolor()`/`uii_resetpalette()`, wrapping UCI control
  commands `$51`-`$54` (`GET_PALETTE`/`SET_PALETTE`/
  `SET_PALETTE_COLOR`/`RESET_PALETTE`) — this is how the generated
  fractal's palette gets pushed to real hardware colors.

## Ultimate libraries (git submodule)

The UCI and turbo libraries come from
https://github.com/xahmol/ultimate-uci-oscar64, included as the git
submodule `lib/ultimate-uci-oscar64` and pinned to a release tag
(v1.2.0). Clone with `--recursive` or run `git submodule update --init`.
**Never edit files inside `lib/`**: fix the library in its own
repository, release a new tag, then check that tag out in the
submodule and commit. This project uses `ultimate_common_lib` and
`ultimate_turbo_lib` (`uii_turbo_fast()`, and `uii_turbo_probe_max()`
for the 48/64 MHz speed probe in `upic_select_display_path()` -- a
port of the probe Christian Gleissner first wrote here).
`upic_probe_class` must stay `volatile`: `make test` and `make e2e`
read it from memory, and Oscar64 would otherwise keep it in a
register and drop the variable.

Full protocol reference: `lib/ultimate-uci-oscar64/docs/UCILIB_MANUAL.md`. Since this demo requires
firmware 3.15+ unconditionally, there's no need to guard these calls
behind a version/capability check.

## Memory layout

All sections and regions are declared in `include/memmap.h` (included
first by `main.c`; the libraries are placed in them through the `-d`
flags in the Makefile's `UPICFLAGS`). See `docs/UPIC_VIEWER.md`'s
"Memory regions" table for what lives where.

The shared `upiccode`/`moddata`/`modbss`/`upicgen` pool
(`$E800`-`$FFFF`) holds the generated Upic renderer (first, so it starts
page-aligned), zoom code, `sq_table`, the save code and part of the
live view; 82 bytes free. Oscar64's linker can, in rare cases, silently wrap an object's
address past `$10000` back down near `$0000` instead of raising a
placement error. Always verify actual object placement via the
build's own `.map` file after changing anything in this pool -- a
clean build alone is not sufficient evidence of correct placement this
close to the boundary.

The default `main` region (`$0853`-`$1800`) has 30 bytes free in the
current build: BSS ends at `$1792` and the stack section starts at
`$17B0`, with `stacksize` 80 (the compiler's minimum is 68). Room for
the live view (v1.2.0) came from copying the gradients and color table
out of startup-only data (`initdata`) into `ovl1`, 16-bit view
arithmetic and a `zoom_udiv16()` without a variable shift. A 32-bit
division or multiply, a plain 8-bit `/` or `%` (`divmod`, ~140 bytes),
or a shift by a variable amount (`bitshift` table, 56 bytes) anywhere in
the program links a runtime in. Check the `.map`
after adding anything there; the linker reports "Cannot place stack
section" when it overflows. When a build fails, the old `.map` stays:
to measure an overflow, build once with the region temporarily
enlarged.

Startup-only code goes in `initcode` (`$C800`-`$CFFF`, the last 8
picture columns, overwritten by the first generation): the Upic
renderer generator, the speed probe, the turbo module,
`program_startup()` and the `initdata` copies of the gradients, color
table and constant `.upic` text. Nothing there may be called after startup. Oscar64
inlines a static function called once into its caller, losing its
`#pragma code` -- keep such functions `__noinline`.

Turbo CPU timing (sub-slots per phi2, the VIC's share, the 1 MHz
window after reset) is summarised in `docs/UPIC_VIEWER.md` and
implemented in `tests/machine.py`; read those before reasoning about
cycle budgets.

The Upic display is the library's (`ultimate_upic_lib`, v1.3.0): an
exact one-dot pixel pitch on both the 64 MHz and the 48 MHz path, with
a renderer generated at startup. `tests/test_turbo_modes.py` checks its
geometry and line budget in the timing model; `make e2e` checks it on
hardware.

Interrupts are masked globally for the program's entire lifetime (see
`main.c`'s own comment) -- this program has no functional need for a
real interrupt, and this avoids a real class of bug where a same-tick
hardware interrupt chains into genuine KERNAL/JiffyDOS ROM code while
this program's own direct-CIA keyboard polling is active.

## Testing

No emulator automation exists for the whole program -- VICE specifically
doesn't emulate the Ultimate's own UCI/turbo hardware this project
depends on. Graphics- or control-affecting changes have to be
confirmed on real Ultimate hardware: by eye, or with `make e2e`
(`tests/e2e/`), which captures the picture from the VIC video stream
and compares it with golden images.

`make test` runs host-side unit tests (`tests/`): the compiled
renderer, 48 MHz patcher and speed probe run in a small cycle-counting
6502 emulator against a model of the U64's turbo CPU timing, and the
committed e2e goldens are checked for consistency. Run it after any
change to `upic_viewer.c`, the library version or region layout.

## Code conventions

This project uses generously detailed comments throughout, not the
terse default style -- explaining WHY a design choice was made (a
hardware constraint, a memory-budget fight, a non-obvious interaction)
is valued here, since this codebase pushes close to several real
hardware and toolchain limits where that reasoning matters for future
changes.

## License

GPLv3 (see `LICENSE`). Any code added to this repository should be
compatible with GPLv3 licensing. Note: the fixed-point algorithm
*design* this project implements is informed by 0x444454/mandelbr8
(CC BY 4.0, see `CREDITS.md`) but no code from that project is used —
this is a from-scratch C reimplementation, so no license conflict.
