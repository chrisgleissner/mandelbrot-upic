/*****************************************************************
Land of Ice and Fire -- Upic picture viewer implementation

Based on Aleksi Eeben's Upic v1.1 border-color raster technique
(aleksi.eeben@me.com, Source/upic.s in the local reference package,
https://csdb.dk/release/?id=263980) -- see docs/UPIC_VIEWER.md for
the full technical background this port is based on.

Deliberate differences from the original (see docs/UPIC_VIEWER.md for
the reasoning behind each):
  - The self-modifying "8th pixel pair" optimization and its PatchLine
    routine are dropped. They saved ~2 cycles per 16-pixel chunk (a
    few dozen cycles total per line) at the cost of self-modifying
    code and hardcoded absolute addresses tied to the original's own
    fixed $E000 placement -- not worth the risk/complexity here, and
    barely affects the frame's cycle budget.
  - The per-line pixel rendering is its own subroutine (render_line_
    pixels), called via jsr from render_frame's line loop -- inlining
    it directly (an earlier draft of this file) put the loop-back
    branch (bne line) more than 127 bytes from its target, which
    6502 relative branches cannot reach; factoring it out mirrors how
    the original's own Frame/RenderLine split works for the same
    underlying reason (Frame's `jsr RenderLine` keeps its own loop-back
    branch short regardless of how big RenderLine itself is).
  - PAL only -- no NTSC row-count crop yet (see upic.s's `ntsc_` patch
    for what that would need).
  - Renders exactly one frame per call (upic_show_frame()) instead of
    an infinite loop, so the caller can poll for the exit key between
    frames without needing to interrupt cycle-exact code mid-line.

CONFIRMED WORKING ON REAL ULTIMATE 64 HARDWARE (2026-09-09) -- both the
synthetic test pattern and real converted photos. Full validation
writeup, including the 4 real render/exit bugs found and fixed via
live hardware memory read/write (no emulator automation exists for
this project -- see CLAUDE.md's Testing section, and see
docs/UPIC_VIEWER.md for why VICE specifically can't validate this
module), is in docs/UPIC_VIEWER.md's "Real-hardware validation" section.
******************************************************************/

#include <c64/keyboard.h>
#include "rombank.h"
#include "upic_viewer.h"

// ---------------------------------------------------------------
// Picture buffer, split across two physical locations (2026-09-09,
// Aleksi Eeben's own suggestion -- see upic_viewer.h's doc comment for
// the full rationale and the render_line_pixels() addressing this must
// stay in sync with).
//
// upic_buffer (columns UPIC_RELOC_COLS..191): $1000+UPIC_RELOC_BYTES
// through $CFFF (UPIC_MAIN_BYTES bytes -- see upic_viewer.h; currently
// $1500-$CFFF, 47,872 bytes, for UPIC_RELOC_COLS=5). $1000-$9FFF is
// always plain RAM; $A000-$CFFF only reads correctly once MMAP_NO_ROM
// is active (see upic_show_frame()) -- but *writes* always land in
// the underlying RAM regardless of banking, so filling this part of
// the buffer (e.g. via a UCI file load) works at any time, banked or
// not.
//
// upic_buffer_reloc (columns 0..UPIC_RELOC_COLS-1): $E000 through
// $E000+UPIC_RELOC_BYTES (currently $E000-$E4FF, 1,280 bytes) -- see
// its own region declaration further down, alongside upiccode, since
// it shares that $E000-$FFFF space and (unlike upic_buffer above)
// genuinely does need MMAP_NO_ROM active even to write to it
// correctly.
//
// This overlaps Oscar64's own default "main" region ($0a00-$a000), so
// that region is explicitly shrunk below ($0a00-$1000, then extended
// back up to $1000+UPIC_RELOC_BYTES now that the relocation frees that
// range -- see the #pragma region(main, ...) near the end of this
// file, and upic_viewer.h's own comment on why UPIC_RELOC_COLS is a
// single value shared by all four targets rather than per-target.
// ---------------------------------------------------------------
#pragma section(upicbuf, 0)
#pragma region(upicbuf, 0x1800, 0xd000, , , {upicbuf})
#ifdef UPIC_EMBED_DRAGON
// Test-only: bake a real reference picture in at compile time instead of
// leaving upic_buffer uninitialized bss, so real hardware can be checked
// against actual photographic content, not just the synthetic diagonal
// test pattern. include/dragon3.upic is Aleksi Eeben's own reference
// sample (see docs/UPIC_VIEWER.md), copied in from the local upic
// package -- not the real demo's picture-loading path (that's still UCI
// file I/O, not done yet). Only enabled by the upicdragon Makefile
// target's -dUPIC_EMBED_DRAGON; the normal upic_buffer stays plain bss
// (the real API contract: caller fills it, e.g. via UCI file load).
//
// __export is required here, not decorative: upic_buffer is never
// accessed through this C array symbol at all -- render_line_pixels()'s
// __asm block reads it via hardcoded literal addresses ($2000, $2100,
// ...), invisible to Oscar64's dataflow tracing. Without __export, the
// optimizer sees an "unreferenced" initialized array and silently drops
// the embedded content entirely (confirmed: region ended up 0 bytes in
// the .map, and the dragon3.upic byte signature was nowhere in the
// compiled .prg at all, even though the build succeeded with no warning).
//
// #embed's offset+length form slices the same 49,152-byte .upic file
// (column-major, so the first UPIC_RELOC_BYTES bytes ARE exactly
// columns 0..UPIC_RELOC_COLS-1) into the two physical arrays -- see
// upic_buffer_reloc's own #embed further down for the first slice.
#pragma data(upicbuf)
__export volatile char upic_buffer[UPIC_MAIN_BYTES] = {
    #embed 47104 2048 "dragon3.upic"
};
#pragma data(data)
#elif defined(UPIC_EMBED_ICELAND)
// Same mechanism as UPIC_EMBED_DRAGON above, but with a real converted
// project photo (tools/upic_convert.py's output) instead of the
// reference sample -- first end-to-end test of the actual conversion
// pipeline, not just the viewer. See src/upic_iceland_test.c.
#pragma data(upicbuf)
__export volatile char upic_buffer[UPIC_MAIN_BYTES] = {
    #embed 47104 2048 "diamond_beach.upic"
};
#pragma data(data)
#else
#pragma bss(upicbuf)
volatile char upic_buffer[UPIC_MAIN_BYTES];
#pragma bss(bss)
#endif

// Nybble shift table: nybbles[i] = i >> 4 -- looks up the "odd" pixel's
// color from a packed byte (high nibble) without a runtime shift in
// the hot loop. Built once (init_nybbles(), called lazily from
// upic_show_frame()), not embedded as a literal 256-entry table.
//
// Placed in ovl1's window ($0200-$0800, bssovl1 -- see the #pragma
// region(ovl1, ...) near the end of this file), NOT modbss
// ($E800-territory, upiccode's own tight budget -- confirmed too full
// once ultimate_dos_lib.c's uii_open_file/uii_load_reu/uii_close_file,
// which live in modcode by that file's own #pragma placement, are
// counted; 53 bytes short, 2026-09-09) and NOT modlowbss (a PLAIN
// #pragma region below $0801, confirmed to corrupt the whole .prg's
// load-address header when holding any real content -- see modlowbss's
// own now-empty declaration below). ovl1 itself is a genuinely idle
// resource right now: modplay_load() (its only real function user) was
// removed entirely (see modplay.h), so nothing else contends for this
// window across this build's whole lifetime -- safe to give it to
// nybbles permanently instead of using it as an actual swappable
// overlay. Confirmed safe via the SAME #pragma overlay mechanism that
// keeps ovl1/ovl2's real code safe from the modlowbss-style header
// corruption (an #pragma overlay region, unlike a plain one, doesn't
// trigger it) -- verified via `xxd -l2` after this exact change, not
// assumed.
//
// Referenced from render_line_pixels() via the plain symbol name
// `nybbles`, NOT a hardcoded literal address the way the picture-buffer
// columns are, so the linker resolves it correctly wherever it actually
// lives -- moving it again in the future doesn't require touching
// render_line_pixels() at all. __align(256) is required, not
// decorative: render_line_pixels()'s `lda nybbles,x` must never cross a
// page boundary (confirmed earlier this session -- a page-crossing
// access there would add a data-dependent extra cycle, breaking this
// module's cycle-exact timing).
//
// Section names declared here (not just where the region is pragma'd
// further down) since #pragma code/data/bss(name) needs the section
// already known.
#pragma section(modcode, 0)
#pragma section(moddata, 0)
#pragma section(modlowbss, 0)
#pragma section(modbss, 0)
#pragma section(bssovl1, 0)
#pragma overlay(ovl_nybbles, 1)
#pragma bss(bssovl1)
static unsigned char nybbles[256];
#pragma align(nybbles, 256)
#pragma bss(bss)

#pragma code(modcode)
static void init_nybbles(void)
{
    unsigned i;
    for (i = 0; i < 256; i++)
        nybbles[i] = (unsigned char)(i >> 4);
}
#pragma code(code)

// ---------------------------------------------------------------
// Relocated picture columns 0..UPIC_RELOC_COLS-1: $E000-$EFFF exactly
// (see upic_viewer.h's doc comment for the full rationale). Its own
// tight region, separate from upiccode below, since render_line_pixels()
// hardcodes these bytes' addresses as literal `ldx $eXX0,y` operands --
// unlike modcode's other content, this data must land at an EXACT,
// predetermined address, not just "somewhere in $E000-$FFFF".
// ---------------------------------------------------------------
#pragma section(picreloc, 0)
#pragma region(picreloc, 0xe000, 0xe800, , , {picreloc})
#ifdef UPIC_EMBED_DRAGON
#pragma data(picreloc)
__export volatile char upic_buffer_reloc[UPIC_RELOC_BYTES] = {
    #embed 2048 0 "dragon3.upic"
};
#pragma data(data)
#elif defined(UPIC_EMBED_ICELAND)
#pragma data(picreloc)
__export volatile char upic_buffer_reloc[UPIC_RELOC_BYTES] = {
    #embed 2048 0 "diamond_beach.upic"
};
#pragma data(data)
#else
#pragma bss(picreloc)
volatile char upic_buffer_reloc[UPIC_RELOC_BYTES];
#pragma bss(bss)
#endif

// ---------------------------------------------------------------
// Render code: freed KERNAL ROM area, $F000-$FFFF now (shrunk from
// $E000-$FFFF -- the bottom 4KB is picreloc's above). Reachable only
// once MMAP_NO_ROM is active. Separate from "main" purely to leave low
// memory free for the picture buffer above -- see that region's own
// comment.
// ---------------------------------------------------------------
// modcode/moddata (declared here, used by modplay.c) share this same
// region for modplay's tick-processing call tree -- see modplay.c's
// own comment on modplay_tick for why that's safe (in short:
// modplay_irq stays in normal memory and temporarily re-banks to
// MMAP_NO_ROM around just its own `jsr modplay_tick`, rather than
// modplay_tick's whole call tree needing to live somewhere always
// valid regardless of banking). NOT safe for $A000-$BFFF instead --
// that's fully claimed by upic_buffer above, confirmed the hard way
// (silent data corruption, no build error) earlier this session.
// modcode/moddata/modbss already declared above, alongside nybbles[].
#pragma section(upiccode, 0)
#pragma region(upiccode, 0xe800, 0x10000, , , {upiccode, modcode, moddata, modbss})
#pragma code(upiccode)

// Renders all 192 pixel-pairs of a single scanline (Y = current row,
// set by render_frame before each call). Its own subroutine so
// render_frame's loop-back branch stays short -- see this file's own
// doc comment above.
static void render_line_pixels(void)
{
    __asm {
        ldx $e000,y
        stx $d020
        lda nybbles,x
        sta $d020
        ldx $e100,y
        stx $d020
        lda nybbles,x
        sta $d020
        ldx $e200,y
        stx $d020
        lda nybbles,x
        sta $d020
        ldx $e300,y
        stx $d020
        lda nybbles,x
        sta $d020
        ldx $e400,y
        stx $d020
        lda nybbles,x
        sta $d020
        ldx $e500,y
        stx $d020
        lda nybbles,x
        sta $d020
        ldx $e600,y
        stx $d020
        lda nybbles,x
        sta $d020
        ldx $e700,y
        stx $d020
        lda nybbles,x
        sta $d020
        ldx $1800,y
        stx $d020
        lda nybbles,x
        sta $d020
        ldx $1900,y
        stx $d020
        lda nybbles,x
        sta $d020
        ldx $1a00,y
        stx $d020
        lda nybbles,x
        sta $d020
        ldx $1b00,y
        stx $d020
        lda nybbles,x
        sta $d020
        ldx $1c00,y
        stx $d020
        lda nybbles,x
        sta $d020
        ldx $1d00,y
        stx $d020
        lda nybbles,x
        sta $d020
        ldx $1e00,y
        stx $d020
        lda nybbles,x
        sta $d020
        ldx $1f00,y
        stx $d020
        lda nybbles,x
        sta $d020
        ldx $2000,y
        stx $d020
        lda nybbles,x
        sta $d020
        ldx $2100,y
        stx $d020
        lda nybbles,x
        sta $d020
        ldx $2200,y
        stx $d020
        lda nybbles,x
        sta $d020
        ldx $2300,y
        stx $d020
        lda nybbles,x
        sta $d020
        ldx $2400,y
        stx $d020
        lda nybbles,x
        sta $d020
        ldx $2500,y
        stx $d020
        lda nybbles,x
        sta $d020
        ldx $2600,y
        stx $d020
        lda nybbles,x
        sta $d020
        ldx $2700,y
        stx $d020
        lda nybbles,x
        sta $d020
        ldx $2800,y
        stx $d020
        lda nybbles,x
        sta $d020
        ldx $2900,y
        stx $d020
        lda nybbles,x
        sta $d020
        ldx $2a00,y
        stx $d020
        lda nybbles,x
        sta $d020
        ldx $2b00,y
        stx $d020
        lda nybbles,x
        sta $d020
        ldx $2c00,y
        stx $d020
        lda nybbles,x
        sta $d020
        ldx $2d00,y
        stx $d020
        lda nybbles,x
        sta $d020
        ldx $2e00,y
        stx $d020
        lda nybbles,x
        sta $d020
        ldx $2f00,y
        stx $d020
        lda nybbles,x
        sta $d020
        ldx $3000,y
        stx $d020
        lda nybbles,x
        sta $d020
        ldx $3100,y
        stx $d020
        lda nybbles,x
        sta $d020
        ldx $3200,y
        stx $d020
        lda nybbles,x
        sta $d020
        ldx $3300,y
        stx $d020
        lda nybbles,x
        sta $d020
        ldx $3400,y
        stx $d020
        lda nybbles,x
        sta $d020
        ldx $3500,y
        stx $d020
        lda nybbles,x
        sta $d020
        ldx $3600,y
        stx $d020
        lda nybbles,x
        sta $d020
        ldx $3700,y
        stx $d020
        lda nybbles,x
        sta $d020
        ldx $3800,y
        stx $d020
        lda nybbles,x
        sta $d020
        ldx $3900,y
        stx $d020
        lda nybbles,x
        sta $d020
        ldx $3a00,y
        stx $d020
        lda nybbles,x
        sta $d020
        ldx $3b00,y
        stx $d020
        lda nybbles,x
        sta $d020
        ldx $3c00,y
        stx $d020
        lda nybbles,x
        sta $d020
        ldx $3d00,y
        stx $d020
        lda nybbles,x
        sta $d020
        ldx $3e00,y
        stx $d020
        lda nybbles,x
        sta $d020
        ldx $3f00,y
        stx $d020
        lda nybbles,x
        sta $d020
        ldx $4000,y
        stx $d020
        lda nybbles,x
        sta $d020
        ldx $4100,y
        stx $d020
        lda nybbles,x
        sta $d020
        ldx $4200,y
        stx $d020
        lda nybbles,x
        sta $d020
        ldx $4300,y
        stx $d020
        lda nybbles,x
        sta $d020
        ldx $4400,y
        stx $d020
        lda nybbles,x
        sta $d020
        ldx $4500,y
        stx $d020
        lda nybbles,x
        sta $d020
        ldx $4600,y
        stx $d020
        lda nybbles,x
        sta $d020
        ldx $4700,y
        stx $d020
        lda nybbles,x
        sta $d020
        ldx $4800,y
        stx $d020
        lda nybbles,x
        sta $d020
        ldx $4900,y
        stx $d020
        lda nybbles,x
        sta $d020
        ldx $4a00,y
        stx $d020
        lda nybbles,x
        sta $d020
        ldx $4b00,y
        stx $d020
        lda nybbles,x
        sta $d020
        ldx $4c00,y
        stx $d020
        lda nybbles,x
        sta $d020
        ldx $4d00,y
        stx $d020
        lda nybbles,x
        sta $d020
        ldx $4e00,y
        stx $d020
        lda nybbles,x
        sta $d020
        ldx $4f00,y
        stx $d020
        lda nybbles,x
        sta $d020
        ldx $5000,y
        stx $d020
        lda nybbles,x
        sta $d020
        ldx $5100,y
        stx $d020
        lda nybbles,x
        sta $d020
        ldx $5200,y
        stx $d020
        lda nybbles,x
        sta $d020
        ldx $5300,y
        stx $d020
        lda nybbles,x
        sta $d020
        ldx $5400,y
        stx $d020
        lda nybbles,x
        sta $d020
        ldx $5500,y
        stx $d020
        lda nybbles,x
        sta $d020
        ldx $5600,y
        stx $d020
        lda nybbles,x
        sta $d020
        ldx $5700,y
        stx $d020
        lda nybbles,x
        sta $d020
        ldx $5800,y
        stx $d020
        lda nybbles,x
        sta $d020
        ldx $5900,y
        stx $d020
        lda nybbles,x
        sta $d020
        ldx $5a00,y
        stx $d020
        lda nybbles,x
        sta $d020
        ldx $5b00,y
        stx $d020
        lda nybbles,x
        sta $d020
        ldx $5c00,y
        stx $d020
        lda nybbles,x
        sta $d020
        ldx $5d00,y
        stx $d020
        lda nybbles,x
        sta $d020
        ldx $5e00,y
        stx $d020
        lda nybbles,x
        sta $d020
        ldx $5f00,y
        stx $d020
        lda nybbles,x
        sta $d020
        ldx $6000,y
        stx $d020
        lda nybbles,x
        sta $d020
        ldx $6100,y
        stx $d020
        lda nybbles,x
        sta $d020
        ldx $6200,y
        stx $d020
        lda nybbles,x
        sta $d020
        ldx $6300,y
        stx $d020
        lda nybbles,x
        sta $d020
        ldx $6400,y
        stx $d020
        lda nybbles,x
        sta $d020
        ldx $6500,y
        stx $d020
        lda nybbles,x
        sta $d020
        ldx $6600,y
        stx $d020
        lda nybbles,x
        sta $d020
        ldx $6700,y
        stx $d020
        lda nybbles,x
        sta $d020
        ldx $6800,y
        stx $d020
        lda nybbles,x
        sta $d020
        ldx $6900,y
        stx $d020
        lda nybbles,x
        sta $d020
        ldx $6a00,y
        stx $d020
        lda nybbles,x
        sta $d020
        ldx $6b00,y
        stx $d020
        lda nybbles,x
        sta $d020
        ldx $6c00,y
        stx $d020
        lda nybbles,x
        sta $d020
        ldx $6d00,y
        stx $d020
        lda nybbles,x
        sta $d020
        ldx $6e00,y
        stx $d020
        lda nybbles,x
        sta $d020
        ldx $6f00,y
        stx $d020
        lda nybbles,x
        sta $d020
        ldx $7000,y
        stx $d020
        lda nybbles,x
        sta $d020
        ldx $7100,y
        stx $d020
        lda nybbles,x
        sta $d020
        ldx $7200,y
        stx $d020
        lda nybbles,x
        sta $d020
        ldx $7300,y
        stx $d020
        lda nybbles,x
        sta $d020
        ldx $7400,y
        stx $d020
        lda nybbles,x
        sta $d020
        ldx $7500,y
        stx $d020
        lda nybbles,x
        sta $d020
        ldx $7600,y
        stx $d020
        lda nybbles,x
        sta $d020
        ldx $7700,y
        stx $d020
        lda nybbles,x
        sta $d020
        ldx $7800,y
        stx $d020
        lda nybbles,x
        sta $d020
        ldx $7900,y
        stx $d020
        lda nybbles,x
        sta $d020
        ldx $7a00,y
        stx $d020
        lda nybbles,x
        sta $d020
        ldx $7b00,y
        stx $d020
        lda nybbles,x
        sta $d020
        ldx $7c00,y
        stx $d020
        lda nybbles,x
        sta $d020
        ldx $7d00,y
        stx $d020
        lda nybbles,x
        sta $d020
        ldx $7e00,y
        stx $d020
        lda nybbles,x
        sta $d020
        ldx $7f00,y
        stx $d020
        lda nybbles,x
        sta $d020
        ldx $8000,y
        stx $d020
        lda nybbles,x
        sta $d020
        ldx $8100,y
        stx $d020
        lda nybbles,x
        sta $d020
        ldx $8200,y
        stx $d020
        lda nybbles,x
        sta $d020
        ldx $8300,y
        stx $d020
        lda nybbles,x
        sta $d020
        ldx $8400,y
        stx $d020
        lda nybbles,x
        sta $d020
        ldx $8500,y
        stx $d020
        lda nybbles,x
        sta $d020
        ldx $8600,y
        stx $d020
        lda nybbles,x
        sta $d020
        ldx $8700,y
        stx $d020
        lda nybbles,x
        sta $d020
        ldx $8800,y
        stx $d020
        lda nybbles,x
        sta $d020
        ldx $8900,y
        stx $d020
        lda nybbles,x
        sta $d020
        ldx $8a00,y
        stx $d020
        lda nybbles,x
        sta $d020
        ldx $8b00,y
        stx $d020
        lda nybbles,x
        sta $d020
        ldx $8c00,y
        stx $d020
        lda nybbles,x
        sta $d020
        ldx $8d00,y
        stx $d020
        lda nybbles,x
        sta $d020
        ldx $8e00,y
        stx $d020
        lda nybbles,x
        sta $d020
        ldx $8f00,y
        stx $d020
        lda nybbles,x
        sta $d020
        ldx $9000,y
        stx $d020
        lda nybbles,x
        sta $d020
        ldx $9100,y
        stx $d020
        lda nybbles,x
        sta $d020
        ldx $9200,y
        stx $d020
        lda nybbles,x
        sta $d020
        ldx $9300,y
        stx $d020
        lda nybbles,x
        sta $d020
        ldx $9400,y
        stx $d020
        lda nybbles,x
        sta $d020
        ldx $9500,y
        stx $d020
        lda nybbles,x
        sta $d020
        ldx $9600,y
        stx $d020
        lda nybbles,x
        sta $d020
        ldx $9700,y
        stx $d020
        lda nybbles,x
        sta $d020
        ldx $9800,y
        stx $d020
        lda nybbles,x
        sta $d020
        ldx $9900,y
        stx $d020
        lda nybbles,x
        sta $d020
        ldx $9a00,y
        stx $d020
        lda nybbles,x
        sta $d020
        ldx $9b00,y
        stx $d020
        lda nybbles,x
        sta $d020
        ldx $9c00,y
        stx $d020
        lda nybbles,x
        sta $d020
        ldx $9d00,y
        stx $d020
        lda nybbles,x
        sta $d020
        ldx $9e00,y
        stx $d020
        lda nybbles,x
        sta $d020
        ldx $9f00,y
        stx $d020
        lda nybbles,x
        sta $d020
        ldx $a000,y
        stx $d020
        lda nybbles,x
        sta $d020
        ldx $a100,y
        stx $d020
        lda nybbles,x
        sta $d020
        ldx $a200,y
        stx $d020
        lda nybbles,x
        sta $d020
        ldx $a300,y
        stx $d020
        lda nybbles,x
        sta $d020
        ldx $a400,y
        stx $d020
        lda nybbles,x
        sta $d020
        ldx $a500,y
        stx $d020
        lda nybbles,x
        sta $d020
        ldx $a600,y
        stx $d020
        lda nybbles,x
        sta $d020
        ldx $a700,y
        stx $d020
        lda nybbles,x
        sta $d020
        ldx $a800,y
        stx $d020
        lda nybbles,x
        sta $d020
        ldx $a900,y
        stx $d020
        lda nybbles,x
        sta $d020
        ldx $aa00,y
        stx $d020
        lda nybbles,x
        sta $d020
        ldx $ab00,y
        stx $d020
        lda nybbles,x
        sta $d020
        ldx $ac00,y
        stx $d020
        lda nybbles,x
        sta $d020
        ldx $ad00,y
        stx $d020
        lda nybbles,x
        sta $d020
        ldx $ae00,y
        stx $d020
        lda nybbles,x
        sta $d020
        ldx $af00,y
        stx $d020
        lda nybbles,x
        sta $d020
        ldx $b000,y
        stx $d020
        lda nybbles,x
        sta $d020
        ldx $b100,y
        stx $d020
        lda nybbles,x
        sta $d020
        ldx $b200,y
        stx $d020
        lda nybbles,x
        sta $d020
        ldx $b300,y
        stx $d020
        lda nybbles,x
        sta $d020
        ldx $b400,y
        stx $d020
        lda nybbles,x
        sta $d020
        ldx $b500,y
        stx $d020
        lda nybbles,x
        sta $d020
        ldx $b600,y
        stx $d020
        lda nybbles,x
        sta $d020
        ldx $b700,y
        stx $d020
        lda nybbles,x
        sta $d020
        ldx $b800,y
        stx $d020
        lda nybbles,x
        sta $d020
        ldx $b900,y
        stx $d020
        lda nybbles,x
        sta $d020
        ldx $ba00,y
        stx $d020
        lda nybbles,x
        sta $d020
        ldx $bb00,y
        stx $d020
        lda nybbles,x
        sta $d020
        ldx $bc00,y
        stx $d020
        lda nybbles,x
        sta $d020
        ldx $bd00,y
        stx $d020
        lda nybbles,x
        sta $d020
        ldx $be00,y
        stx $d020
        lda nybbles,x
        sta $d020
        ldx $bf00,y
        stx $d020
        lda nybbles,x
        sta $d020
        ldx $c000,y
        stx $d020
        lda nybbles,x
        sta $d020
        ldx $c100,y
        stx $d020
        lda nybbles,x
        sta $d020
        ldx $c200,y
        stx $d020
        lda nybbles,x
        sta $d020
        ldx $c300,y
        stx $d020
        lda nybbles,x
        sta $d020
        ldx $c400,y
        stx $d020
        lda nybbles,x
        sta $d020
        ldx $c500,y
        stx $d020
        lda nybbles,x
        sta $d020
        ldx $c600,y
        stx $d020
        lda nybbles,x
        sta $d020
        ldx $c700,y
        stx $d020
        lda nybbles,x
        sta $d020
        ldx $c800,y
        stx $d020
        lda nybbles,x
        sta $d020
        ldx $c900,y
        stx $d020
        lda nybbles,x
        sta $d020
        ldx $ca00,y
        stx $d020
        lda nybbles,x
        sta $d020
        ldx $cb00,y
        stx $d020
        lda nybbles,x
        sta $d020
        ldx $cc00,y
        stx $d020
        lda nybbles,x
        sta $d020
        ldx $cd00,y
        stx $d020
        lda nybbles,x
        sta $d020
        ldx $ce00,y
        stx $d020
        lda nybbles,x
        sta $d020
        ldx $cf00,y
        stx $d020
        lda nybbles,x
        sta $d020
    }
}

// Renders one full PAL frame (256 scanlines) of upic_buffer via the
// border-color ($D020) raster trick. Must only be called while
// MMAP_NO_ROM is active (see upic_show_frame()) and turbo is enabled
// (see turbo_fast(), include/turbo.h) -- at 1 MHz this loop cannot
// keep up with the raster beam at all.
//
// A named __asm block rather than a C function wrapping inline asm
// (2026-09-21) so upic_select_display_path() below can reach its two
// patchable immediate operands by label (render_frame.trb/.dly, the
// same `block.label` addressing crt.c's own runtime uses). The emitted
// bytes are unchanged from the C-function version: that version had
// no prologue/epilogue beyond the trailing rts added here by hand.
__asm render_frame
{
        lda #$00                 // let VIC-II rest: DEN=0 widens the
        sta $d011                // "border" to the whole visible area
                                  // (also zeroes the raster-IRQ compare
                                  // MSB; doesn't affect the read-back
                                  // MSB used by the sync waits below)

    f1:
        lda $d011
        bpl f1
    f2:
        lda $d011
        bmi f2

        lda #$18                // top of image (raster line 24)
    topwait:
        cmp $d012
        bne topwait

        ldy #$00
    line:
        lda $d012
    linewait:
        cmp $d012                // wait for the raster line to tick
        beq linewait

        lda #$80                 // resync: rewrite turbo control regs
    trb:                         // operand patched by upic_select_display_path()
        ldx #$8f
        sta $d031
        stx $d031

        // Widened 2026-09-12 from the original $51: the leftmost 1-2
        // native columns were painting just before the visible raster
        // window opened, invisible on a real screen even though a
        // direct memory read confirmed they were correctly written
        // into the packed buffer -- most visible as the box-mode
        // corner markers' LEFT pair going missing on real hardware,
        // even at moderate box sizes (not a zoom.c math bug -- that was
        // checked and ruled out separately, see zoom.c's own history).
        //
        // Bisected against real hardware, not guessed (right-side
        // marker position + picture's own right-edge content bound,
        // measured in an OBS capture; buffer contents confirmed
        // unchanged throughout via direct memory reads -- only the
        // picture's SCREEN position moves, never the underlying data):
        //   $51 (+0):  safe, but left markers invisible.
        //   $59 (+8):  safe (+21px), still not enough.
        //   $69 (+24): safe (+58px), still not enough.
        //   $A5 (+84): left markers finally visible, but this exceeds
        //              the render loop's real per-line cycle budget --
        //              each line's paint falls a little further behind
        //              the raster beam than the line before (the
        //              per-line raster-sync wait can only catch the
        //              NEXT available line tick, not claw back time
        //              already lost mid-line), producing a visible
        //              diagonal skew that worsens down the frame and
        //              cuts the picture off before the bottom.
        //   $87 (+54): bisecting +24/+84 -- confirmed both safe (clean
        //              rectangular picture, no skew, full 256 rows) AND
        //              sufficient (all 4 corner markers visible,
        //              confirmed via screenshot with all 4 corner-
        //              marker blobs measured, not eyeballed). This is
        //              the value in use.
        //
        // This is the 64 MHz value. upic_select_display_path() patches
        // the operand to UPIC_DELAY_48 on a 48 MHz machine -- see that
        // function's comment for the per-line cycle budget, which also
        // shows why $87 has only 2 sub-slots to spare.
    dly:
        ldx #$87
    delay:
        dex
        bne delay

        jsr render_line_pixels

        lda #$00
        sta $d020

        iny
        cpy #$00                 // 256 rows -- Y wraps 255->0 to exit
        bne line
        rts
}

#pragma code(code)

// ---------------------------------------------------------------
// 48 MHz display path (2026-09-21, reworked 2026-09-28 after the first
// run on a real Ultimate 64 Elite) -- Ultimate 64 / Elite I support.
//
// Ultimate 64 turbo CPU timing: each phi2 cycle offers as many CPU
// sub-slots as the board's top speed (64 on Elite II / C64U, 48 on
// U64 / Elite I), and the $D031 speed index selects how many of them
// the CPU may use -- all of them at the top index, one per phi2 at
// index 0 (1 MHz). The VIC-II uses one sub-slot of every phi2 for its
// own memory access, so the top index gives 63 CPU cycles per phi2 on
// Elite II / C64U and 47 on U64 / Elite I. A PAL line is 63 phi2 and
// 504 dots. Reading a VIC register costs the CPU one extra sub-slot;
// writing one does not.
//
// Everything above was tuned for 64 MHz: 8 cycles per border-colour
// write is about one dot. At 48 MHz 8 cycles are 1.36 dots, so the
// full 384-pixel line (3072 cycles) would be 522 dots wide and would
// not fit the 2820 cycles a line leaves (budget below). A load plus a
// store to $D020 (8 cycles) is the cheapest way to show a pixel, so
// at the 64 MHz path's picture width a 48 MHz line has time for about
// 3 pixels in 4. The 48 MHz path shows exactly that: for every 4
// pixels (byte columns A = 2m and B = 2m+1) it shows 3, 8 cycles each:
//
//   B9 al ah   lda A,y          pixel 4m     (A's low nibble)
//   8D 20 D0   sta $d020
//   BE bl bh   ldx B,y          pixel 4m+2   (B's low nibble)
//   8E 20 D0   stx $d020
//   BD nl nh   lda nybbles,x    pixel 4m+3   (B's high nibble)
//   8D 20 D0   sta $d020
//
// 24 cycles per 4 source pixels is 4.09 dots, against 4.06 at 64 MHz,
// so each shown pixel is 1.36 dots wide (1 or 2 dots on screen) and 288
// of the 384 pixels are shown. Pixel 4m+1 is the one left out because
// it is the pixel whose dots its neighbours overlap most once the
// group is stretched this way. The 0.5% difference in pitch adds up to
// about 2 dots across the line, so the delay below centres the error
// rather than pinning the left edge: measured on an Ultimate 64 Elite
// against a C64 Ultimate, every shown pixel lies within 1.5 dots of
// where the 64 MHz path shows the same pixel (0.5 dot on average, -0.8
// at the left edge to +0.9 at the right), and the picture starts one
// dot left of the 64 MHz one. With the left edges aligned (delay 100)
// the pixels were +0.9 dot off on average and up to 2.5 at the right.
//
// The last 12 bytes of a group are byte column B's own code, unchanged,
// and `sta $d020` is the last instruction of column A. So the patch
// below builds each 18-byte group from the as-built code -- `lda A,y`,
// then A's bytes 9-11, then all of B -- and a group is shorter than the
// two 12-byte columns it replaces, so the routine is rebuilt in place,
// front to back, without overwriting anything it has yet to read.
//
// Only UPIC_GROUPS_48 of the 96 groups are drawn. The last two (pixels
// 376-383) would start about 384 dots right of the picture's first
// pixel, past the right edge of the 384-dot visible area, where the
// 64 MHz path's pixels 370-383 aren't visible either. Leaving them out frees the 48 cycles
// that let the picture sit where the 64 MHz one does (see the delay
// below).
//
// Nothing here indexes into I/O space. The first version of this path
// (2026-09-21) used `lda A,y / sta $d020,x / jmp next`, 12 cycles per
// byte column on paper, and on a real Ultimate 64 Elite showed every
// row two raster lines apart, the picture alternating between two
// frames. An NMOS 6502 reads the target of an indexed store once before
// writing it (the "dummy read"); for `sta $d020,x` that is a read of a
// VIC register, which costs the extra sub-slot. At 13 cycles a column a
// row overran its line: the largest delay that worked on hardware was
// 56, not 97. tests/mos6502.py now models the dummy read, and the tests
// fail for that version.
//
// Line budget. render_frame's `sta $d031 (#$80) / stx $d031 (#$8f)`
// drops to index 0 for the stx's last three cycles, which then finish
// at the end of three successive phi2 cycles; turbo resumes at the
// start of phi2 cycle 4 on every line, whatever the polling jitter
// was. From there to the end of the line are 60 phi2 cycles:
// 60 * 63 = 3780 CPU cycles at 64 MHz, 60 * 47 = 2820 at 48 MHz. The
// next line's `lda $d012` (a VIC register read costs one extra
// sub-slot) must finish before the line ends:
//
//   64 MHz: 2 + (5*135-1) + 6 + 3072 + 6 + 13 + 5 = 3778 of 3780
//   48 MHz: 2 + (5*D-1)   + 6 + 2256 + 6 + 13 + 5 = 5*D + 2287 of 2820
//
// The 64 MHz value $87 therefore has 2 sub-slots to spare, which
// matches the hardware bisection in render_frame (the next tested
// value, $A5, skews). At 48 MHz D = 100 would put the first pixel on
// the 64 MHz path's first dot ((5*100-1) + 16 cycles at 47 per phi2
// against (5*135-1) + 16 at 63); D = 99 moves the picture one delay
// pass (0.85 dot) left, which centres the position error described
// above, and leaves 38 cycles to spare. On an Ultimate 64 Elite the
// picture stayed intact up to D = 108.
//
// render_frame operands patched:
//   - dly: the delay before the first pixel, $87 -> UPIC_DELAY_48.
//   - trb: the turbo control byte rewritten on every line. Speed index
//     15 ($8F, as built) is already 48 MHz on an Ultimate 64 / Elite I,
//     so the release build leaves it alone. The test-only
//     UPIC_FORCE_48MHZ build (`make force48`) patches it to $8E --
//     index 14, which is 48 MHz on Elite II / C64U -- so this path can
//     be checked on a 64 MHz machine.
//
// Speed probe. Times a fixed 64764-cycle loop against the raster
// counter, which advances at the real PAL line rate whatever the CPU
// speed. Every loop-back is an absolute jmp (always 3 cycles), so the
// count does not depend on where the linker places this function:
//
//   64 MHz: 64764 / (63 * 63) = 16.3 lines -> $D012 ends at $30
//   48 MHz: 64764 / (63 * 47) = 21.9 lines -> $D012 ends at $35/$36
//
// The Ultimate 64 runs the CPU at 1 MHz for a few seconds after every
// CPU reset, whatever $D031 says, and briefly after IEC bus activity
// -- this probe runs well inside the first window when the program is
// started from the Ultimate menu. A whole loop at 1 MHz
// spans 1028 lines and deterministically ends at $7C, which is
// rejected. A loop during which the forced window ends can end on any
// line, so a result is only accepted once two consecutive loops agree
// on the same class; at most one loop per window can be affected.
//
// Each retry writes the turbo control byte again, so a machine whose
// speed register was reset after turbo_fast() still gets there. If no
// class is accepted within 256 loops (about 20 s at 1 MHz: turbo is
// off, e.g. the program was started without its .cfg), the probe gives
// up and keeps the unpatched 64 MHz path, as v1.0.3 did, rather than
// waiting forever with nothing on screen.
//
// One-way: called once at startup, before the first frame, and never
// undone. Lives in "main" (not upiccode) since it runs only once.
// tests/test_turbo_modes.py runs this exact compiled code against a
// model of the U64's turbo CPU timing (tests/machine.py).
// ---------------------------------------------------------------
#define UPIC_DELAY_48       99
#define UPIC_GROUPS_48      94
#define UPIC_PROBE_START    0x20
#define UPIC_PROBE_64MHZ    (UPIC_PROBE_START + 19)   // below: 64 MHz
#define UPIC_PROBE_VALID    (UPIC_PROBE_START + 26)   // below: 48 MHz; else retry

unsigned char upic_frame_quarters = 4;

#ifndef UPIC_FORCE_48MHZ
// Probe result: 1 = 64 MHz, 0 = 48 MHz. The whole retry loop is one
// asm block writing a file-scope static, not C control flow around a
// shorter asm block: written that way Oscar64 duplicated the asm body
// three times and read the result before the loop (checked in the
// -g .asm listing), the inline-asm hazard docs/OSCAR64_MANUAL.md describes.
// Starts at 2 (no valid result yet) as initialized data rather than
// an asm store, which saved 5 bytes of the "main" region.
static unsigned char upic_probe_class = 2;
// Loops left before the probe gives up: 0 wraps to 256 on the first
// decrement.
static unsigned char upic_probe_tries = 0;
#endif

void upic_select_display_path(void)
{
#ifndef UPIC_FORCE_48MHZ
    __asm {
    retry:
        lda #$8f                 // TURBO_SPEED_MAX | TURBO_BADLINES_OFF,
        sta $d031                // again: see the comment above
    f1:
        lda $d011                // wait for the bottom of the frame...
        bpl f1
    f2:
        lda $d011                // ...then for the raster to wrap to 0
        bmi f2
        lda #UPIC_PROBE_START
    w:
        cmp $d012
        bne w

        ldy #36                  // 36 * 1799 = 64764 cycles
    o:
        ldx #0
    i:
        dex
        beq id
        jmp i
    id:
        dey
        beq od
        jmp o
    od:
        lda $d012
        ldx #2
        cmp #UPIC_PROBE_VALID
        bcs store                // forced slow-down: class 2, measure again
        dex
        cmp #UPIC_PROBE_64MHZ
        bcc same                 // class 1: 64 MHz
        dex                      // class 0: 48 MHz
    same:
        cpx upic_probe_class
        beq done                 // two loops in a row agree
    store:
        stx upic_probe_class
        dec upic_probe_tries
        bne retry
        inc upic_probe_class     // gave up: any nonzero class keeps 64 MHz
    done:
    }
    if (upic_probe_class)
        return;
#endif

    // Rebuild render_line_pixels() as UPIC_GROUPS_48 18-byte groups
    // (see the comment above): group byte i comes from as-built byte i
    // for i < 3 and byte i + 6 after that, then `ldx A,y` becomes
    // `lda A,y`. Reads always run ahead of writes.
    char *src = (char *)(unsigned)render_line_pixels;
    char *dst = src;
    unsigned char g, i;

    for (g = 0; g < UPIC_GROUPS_48; g++)
    {
        for (i = 0; i < 18; i++)
            dst[i] = src[i < 3 ? i : i + 6];
        dst[0] = 0xb9;           // ldx abs,y -> lda abs,y
        dst += 18;
        src += 24;
    }
    *dst = 0x60;                 // rts

    upic_frame_quarters = 3;     // see upic_viewer.h

    __asm {
        lda #UPIC_DELAY_48
        sta render_frame.dly + 1
#ifdef UPIC_FORCE_48MHZ
        lda #$8e                 // TURBO_SPEED_48MHZ | TURBO_BADLINES_OFF
        sta render_frame.trb + 1
#endif
    }
}

// ---------------------------------------------------------------
// C-level wrapper: stays in the normal, always-executable default
// region (below $A000) so it can safely perform the memory-map switch
// itself. See upic_viewer.h for the calling convention.
// ---------------------------------------------------------------
static char nybbles_ready = 0;

char upic_show_frame(void)
{
    // ROM banking is shared, demo-wide setup now -- see rombank.h.
    // Idempotent: whichever module (this one, modplay.c, ...) calls it
    // first does the real work, safe to call every frame. $E000-$FFF9
    // becomes ordinary, always-executable RAM for the picture-viewing
    // session as a result -- not toggled per frame like the old design.
    // Must happen BEFORE init_nybbles() below: nybbles[] itself now
    // lives at $E000 too (see its own declaration), only valid RAM
    // once ROM is actually banked out.
    rombank_out();

    if (!nybbles_ready) {
        init_nybbles();
        nybbles_ready = 1;
    }

    // No local SEI/CLI around the render itself (2026-09-11, was
    // removed here): interrupts are masked globally and permanently
    // from main()'s own top-level SEI now, root-causing a real-hardware
    // "any key press drops to text mode" crash -- see main.c's own
    // comment for the full story. A local re-enable here would have
    // undone that for the gap between frames.
    __asm { jsr render_frame }

    keyb_poll();
    return key_pressed(KSCAN_SPACE);
}

void upic_restore_display(void)
{
    // Wait for the exit key to be physically RELEASED before doing
    // anything else -- upic_show_frame() exits its loop the instant it's
    // detected PRESSED, but the user's finger is still on the key for
    // some short but nonzero time after that; the KERNAL's real,
    // interrupt-driven keyboard scan could otherwise see it still held
    // as a fresh event and (re-)act on it before we've finished
    // restoring anything. keyb_poll()/key_pressed() do direct CIA
    // matrix scanning (see c64/keyboard.c), so this works regardless of
    // ROM banking or interrupt state.
    //
    // SPACE, not RUN/STOP, deliberately: RUN/STOP was the original exit
    // key, but real-hardware testing showed a persistent "BREAK IN 10"
    // on exit that survived every targeted fix tried -- clearing the
    // keyboard buffer ($C6/NDX), making this whole restore sequence
    // atomic (SEI/CLI), this release-wait, and resetting STKEY ($91,
    // the KERNAL's separate RUN/STOP-specific latch, read by the $FFE1
    // STOP-check routine BASIC calls between statements). Diagnosed via
    // a controlled swap: building with SPACE as the exit key instead
    // showed no break and no leftover character at all, proving the
    // issue is specific to RUN/STOP's own extra KERNAL-level handling
    // (it does something beyond both the buffer and STKEY that wasn't
    // identified), not a generic timing race. Pragmatic decision: use
    // SPACE, which works cleanly, rather than keep chasing RUN/STOP's
    // exact mechanism. Revisit only if RUN/STOP specifically becomes a
    // hard requirement later.
    do {
        keyb_poll();
    } while (key_pressed(KSCAN_SPACE));

    // Whole restore sequence is SEI'd -- keeps it atomic on top of the
    // release-wait above (a real interrupt landing mid-sequence could
    // otherwise re-populate the keyboard buffer after our clear below).
    __asm { sei }

    // Deliberately does NOT touch ROM banking. Earlier versions of this
    // function restored ROM here (mmap_set(MMAP_ROM)), on the
    // assumption a picture scene was the only thing needing it banked
    // out. Now that ROM stays banked out for the WHOLE demo's lifetime
    // (see rombank.h) -- required so there's enough RAM for real effect
    // code alongside the picture buffer and modplay.c -- restoring it
    // here would break every OTHER scene that also depends on
    // $A000-$BFFF/$E000-$FFF9 being usable. If a caller genuinely needs
    // to return to BASIC (a standalone test harness, not the real demo),
    // call rombank_restore() explicitly itself, separately from this
    // function -- see src/upic_test.c and friends.

    *(volatile char *)0xd011 = 0x1b;  // standard default: DEN=1, RSEL=1, YSCROLL=3
    // $D020 itself (not just DEN) needs restoring too -- the render loop
    // leaves it holding whatever raw index the last line's clear wrote
    // (0/black), which stays black under any palette until something
    // explicitly writes a normal border index back in. Confirmed on real
    // hardware: DEN restore alone still showed a black border over an
    // otherwise-correct, un-hung BASIC screen.
    *(volatile char *)0xd020 = 0x0e;  // standard default border: light blue

    // Clear the KERNAL keyboard buffer count ($C6/NDX) -- required, not
    // decorative. upic_show_frame() detects the exit key via keyb_poll()'s
    // direct CIA matrix scan, completely bypassing the KERNAL's own
    // keyboard-buffer feeding (which only runs from the KERNAL's IRQ-
    // driven scan, masked throughout the render loop). The KERNAL never
    // gets a chance to "consume" that keypress while we're reading it
    // ourselves -- so once interrupts resume and its own scan restarts,
    // it could otherwise see the still-fresh keypress and react to it.
    // $C6 is NDX, the count of characters currently queued in the
    // keyboard buffer ($0277-$0280) -- zeroing it discards anything
    // queued so nothing replays once normal KERNAL processing resumes.
    // (This was originally written against RUN/STOP as the exit key,
    // where it was necessary but not sufficient -- see the exit-key
    // doc comment above. Kept for SPACE too: harmless, still correct
    // general hygiene against whatever got queued during the session.)
    *(volatile char *)0xc6 = 0;

    // Also reset STKEY ($91) to $7F (not-pressed) -- a separate latch
    // from the keyboard buffer above, set by the KERNAL's own scan when
    // it sees RUN/STOP held alone, read by the KERNAL's STOP-check
    // routine ($FFE1) that BASIC calls between statements. Added while
    // chasing the RUN/STOP "BREAK IN 10" issue -- did NOT fully fix it
    // (see the exit-key doc comment above for what actually resolved
    // it: switching to SPACE). Kept anyway as harmless defensive
    // cleanup of stray KERNAL state, in case RUN/STOP was also pressed
    // by the user at some point during the session even though it's no
    // longer the exit key.
    *(volatile char *)0x91 = 0x7f;

    __asm { cli }
}
#pragma code(code)

// ---------------------------------------------------------------
// Shrink Oscar64's own default "main" region so it stops at $1000,
// leaving $1000-$d000 free for upic_buffer above. This is the hard
// constraint discovered while designing this module: everything else
// this project links in (UCI library calls actually reached, turbo
// control, this file's own wrapper code, stack) must fit in
// $0a00-$1000 -- under 1.5 KB. No heap section: this module doesn't
// use malloc(), and heap was the first thing to not fit. See
// docs/UPIC_VIEWER.md's "Memory budget" section for what that does
// and doesn't leave room for.
// ---------------------------------------------------------------
// Starts at $0853, not Oscar64's usual $0a00 default -- $0801-$0852 is
// the actual BASIC-stub/startup region's real usage (confirmed via
// .map: "0801 - 0880 : 0853, 0052, startup", i.e. the startup region's
// own declared bound only ever fills to $0853 of the $0880 it claims,
// and everything from $0880-$09FF was otherwise completely unclaimed
// address space, not part of any region at all). Reclaims 429 bytes
// total vs. the $0a00 default. Confirmed byte-exact against a known-
// good build (no placement conflict, no silent overlap corruption --
// see the modplayregion incident in docs/UPIC_VIEWER.md for why that
// check matters here).
// heap: uii_change_dir()/uii_open_file() (include/ultimate_dos_lib.c)
// no longer use malloc() as of 2026-09-09 -- switched to a shared
// static command buffer specifically because crt_malloc/crt_free's own
// code (~359 bytes, confirmed via the .map) was the difference between
// upicmodplay fitting at "main"'s natural region bounds and not, once
// modplay_load()/modplay_init() moved out to #pragma overlay functions
// (see modplay.h and upic_viewer.h's own notes) freed up everything
// ELSE it was possible to free. Every OTHER malloc-using function in
// ultimate_dos_lib.c/ultimate_common_lib.c is untouched (still mallocs
// normally if a future build actually calls one) -- heapsize/heap are
// kept declared here, tiny, as a defensive default for that case. Must
// stay in this main region's own section list or malloc() silently
// returns NULL (see docs/OSCAR64_MANUAL.md's heap-placement gotcha) -- this
// project already has its own custom #pragma region(main, ...) below,
// so this is the safe case that gotcha describes, not the risky one.
// Shrunk 32 -> 8 (2026-09-11): "main" ran short by ~11 bytes once
// zoom.c's zoom_out_view() moved in (see zoom.c's own comment) --
// nothing in this project's own call graph actually reaches
// uii_add_partition() (the only remaining malloc-using function,
// ultimate_common_lib.c), so this reservation was pure defensive
// margin for a function that's dead-code-eliminated here regardless.
// Shrunk 8 -> 0 (2026-09-12): mandelbrot.c's fixed_sqr()/fixed_mul()
// needed +15 bytes each in "main" (saturating-overflow fix, see their
// own comments) and this region had exactly zero bytes of slack left
// even before that -- reclaiming this reservation is the same
// "provably dead code, pure defensive margin" case the paragraph above
// already documents (uii_add_partition() is unreachable from this
// build's own call graph), just taken one step further now that every
// byte here is contested. If a future build actually reaches a
// malloc()-using function, this will need to grow back (and MUST stay
// in this region's own section list when it does -- see the
// heap-placement gotcha noted above).
// Upper bound extended $1000 -> $2000 (2026-09-09): the picture-buffer
// relocation above (see upic_viewer.h) frees $1000-$1FFF for ordinary
// low-memory use. This single extension replaces the ENTIRE "lowmem"
// compressed-inlay mechanism this session built and then abandoned --
// see git history / docs/UPIC_VIEWER.md for that mechanism's own
// story if it's ever needed again for a build where this relocation
// alone isn't enough (upicmodplay, most likely -- its modplay/dos_lib
// content still needs reassessing against this new budget). Ordinary
// region extension, not a compressed inlay: $1000 is safely ABOVE the
// BASIC-stub/startup region ($0801-$0853), so widening "main" upward
// into it does NOT touch the file's own lowest-address content the
// way widening down to $0200 did -- confirmed no $0801 load-address
// regression (see the picreloc/upiccode split above, and rebuild+
// verify `xxd -l2` on any test target after touching these bounds).
// Shrunk 210 -> 80 (2026-09-12, same overflow this heapsize comment
// describes): unlike heapsize, this one isn't a judgment call --
// Oscar64 statically computes the program's real worst-case stack
// depth and hard-errors ("Static stack usage exceeds stack segment")
// if the declared size is ever insufficient, confirmed by bisecting
// down to the exact minimum (68) before picking 80 for a little
// margin. 210 was never a measured requirement, just an unexamined
// round-number default -- nothing here needed anywhere near that much.
// Shrunk 80 -> 72 (2026-09-21): upic_select_display_path() (48 MHz
// support) needed 8 more bytes in "main" than were left. The minimum
// was re-bisected on that build and is still 68 (66 fails with the
// error above), so 72 keeps 4 bytes over it. Region now has 0 bytes
// free: BSS ends exactly where the stack section starts.
// Back to 80 (2026-09-28): mandelbrot_generate() no longer links the
// 32-bit division runtime (see its symmetry check), which freed about
// 400 bytes of "main" -- more than the reworked 48 MHz patcher added.
#pragma heapsize(0)
#pragma stacksize(80)
#pragma region(main, 0x0853, 0x1800, , , {code, data, bss, heap, stack})

// modlowbss: $0200-$0800, BSS ONLY -- confirmed by direct test (2026-09-09)
// that an uninitialized-only region below $0801 does NOT shift the
// compiled .prg's own load address the way lowmem's CODE/DATA did:
// BSS has no real content to store in the file at all (Oscar64's
// startup code just zeroes it at runtime), so there's nothing there to
// become the file's "lowest loaded address" in the first place. CODE
// or initialized DATA placed this low WOULD still shift it -- see the
// abandoned "lowmem" section above for why, and never place code/data
// here without first confirming the resulting .prg still loads at
// $0801 (`xxd -l2`) AND still runs via the Ultimate's own PRG-run
// mechanism (a clean compile is not sufficient evidence, as lowmem's
// own story shows). $0100-$01FF still excluded regardless of content
// type -- real 6502 hardware stack, not something any region pragma
// can safely claim. Used for modplay.c's largest pure-BSS globals
// (the struct + scratch buffers), freeing that space from competing
// with modcode/upiccode's own tight $F000-$FFFF budget -- see
// modplay.c's own comments on what's moved here and why.
//
// FINDING (2026-09-09): a plain #pragma region below $0801 holding real
// content -- modlowbss, exactly as declared here -- corrupts
// upicmodplay.prg's own load-address header ($0002 instead of $0801).
// Reproduced the SAME class of bug the earlier nybbles-in-modlowbss
// regression hit, but this time for the whole modplay struct,
// independent of main's width or mod_hdr_buf. Isolated via direct
// bisection against two alternatives that both stayed correct: (a)
// the same content in plain default `bss` inside "main" itself (no
// separate low region at all), and (b) the same low address range
// declared as an Oscar64-native #pragma overlay region instead (see
// ovl1 below) -- only the plain-#pragma-region-below-$0801 case
// breaks. modlowbss abandoned entirely as a strategy; section kept
// declared (empty, unused) only because modplay.c's own comments still
// reference it pending a docs cleanup pass -- do not section anything
// into it again without re-verifying `xxd -l2` on the result.
#pragma section(modlowbss, 0)

// ovl1: NOT a swappable overlay in this build -- permanently holds
// nybbles[] instead (see its own declaration above for why). Was
// modplay_load()'s overlay before that function was removed entirely
// (2026-09-09) in favor of calling rombank.h's overlay_stage()
// directly -- see modplay.h's own note. If a real swappable overlay is
// ever needed again alongside nybbles, it needs its OWN window (nybbles
// must stay resident, never overwritten) -- do not reuse ovl1 for both.
#pragma section(codeovl1, 0)
#pragma section(dataovl1, 0)
#pragma section(bssovl1, 0)
#pragma region(ovl1, 0x0200, 0x0800, , 1, { codeovl1, dataovl1, bssovl1 })

// ovl2: second overlay sharing the SAME address window as ovl1 (same
// pattern as ~/VDCScreenEditor2's vdcseovl1/vdcseovl3, which also share
// one address at different ids) -- for modplay_init(), one-time,
// non-resident code.
#pragma section(codeovl2, 0)
#pragma section(dataovl2, 0)
#pragma section(bssovl2, 0)
#pragma region(ovl2, 0x0200, 0x0800, , 2, { codeovl2, dataovl2, bssovl2 })
