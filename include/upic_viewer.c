/*****************************************************************
Mandelbrot Upic -- Upic picture display (implementation)

The display itself is ultimate_upic_lib (ultimate-uci-oscar64 1.3.0):
Aleksi Eeben's Upic border-color raster technique
(https://csdb.dk/release/?id=263889), with the exact one-dot pixel pitch
of his Upic v1.3 line renderer (every 8th pixel pair from an immediate
operand patched before each line) and Christian Gleissner's 48 MHz path
(mandelbrot-upic PR #2), given the same exact pitch in the library.

History: v1.0.0-v1.1.1 had this project's own port of Upic v1.1 here,
without the per-line patch step, so a pixel was 64/63 of a dot wide at
64 MHz and pixels 370-383 fell beyond the right edge; v1.1.0 added
Christian's 48 MHz patcher to that code. See docs/UPIC_VIEWER.md.
******************************************************************/

#include <c64/keyboard.h>
#include "rombank.h"
#include "ultimate_turbo_lib.h"
#include <string.h>
#include "upic_viewer.h"

unsigned char upic_frame_quarters = 4;

#ifndef UPIC_FORCE_48MHZ
// Probe result: 0 = 48 MHz, 1 = 64 MHz, 2 = no result (the probe gave
// up after 256 loops; the 64 MHz renderer is used then). volatile: only
// written here, so Oscar64 would otherwise drop the variable -- but
// tests/ and tests/e2e read it from memory.
static volatile unsigned char upic_probe_class = 2;
#endif

// ---------------------------------------------------------------
// Startup: choose the display path (initcode, see memmap.h)
// ---------------------------------------------------------------
#pragma code(initcode)
#pragma data(initdata)

void upic_select_display_path(void)
{
#ifdef UPIC_FORCE_48MHZ
	// Test-only build (`make force48`): always the 48 MHz path, at speed
	// index 14 -- 48 MHz on an Elite II / C64 Ultimate -- so the path can
	// be checked on a 64 MHz machine.
	uii_upic_turbo = TURBO_SPEED_48MHZ | TURBO_BADLINES_OFF;
	uii_upic_init(UII_UPIC_48MHZ);
	upic_frame_quarters = 3;
#else
	// uii_upic_init(UII_UPIC_AUTO) runs uii_turbo_probe_max(): it times a
	// loop against the raster counter, rejects loops run in the forced
	// 1 MHz window after a reset, needs two agreeing loops, and gives up
	// after 256 (about 20 s at 1 MHz, e.g. started without the .cfg).
	char path = uii_upic_init(UII_UPIC_AUTO);
	upic_probe_class = path == UII_UPIC_48MHZ ? 0
	                 : path == UII_UPIC_64MHZ ? 1
	                 : 2;
	if (path == UII_UPIC_48MHZ)
		upic_frame_quarters = 3;
#endif
}

#pragma code(code)
#pragma data(data)

// ---------------------------------------------------------------
// One frame
// ---------------------------------------------------------------

char upic_show_frame(void)
{
	// ROM banking is set up once for the whole program (rombank.h); the
	// call is idempotent. Columns 0-7 and the generated renderer are
	// under the KERNAL ROM area.
	rombank_out();
	uii_upic_show_frame();
	keyb_poll();
	return key_pressed(KSCAN_SPACE);
}

// ---------------------------------------------------------------
// Live view during generation (see upic_viewer.h)
// ---------------------------------------------------------------
// Bar and Full run the library's raster-IRQ viewer (Aleksi Eeben's Upic
// v1.3 viewer): interrupts are enabled only while generating, with the
// ROMs banked out, our own vectors at $FFFE/$FFFA and CIA1 timer
// interrupts off, so the KERNAL/JiffyDOS handler that made main.c mask
// interrupts is never reached. Masked again before browse mode.

#define BAND_FIRST 124                   // rows 124-131: through the main axis
#define BAND_ROWS  8

unsigned char upic_live_mode = UPIC_LIVE_BAR;

// Set by upic_live_hook on a V press (edge), cleared by the generator.
static volatile char live_request;
static char v_down;

// V key, by the raster-IRQ viewer at the end of every frame (Bar/Full)
// or by the generator once per column (Classic). Assembly, touching
// only A and the two flags: in an interrupt it must not use the zero
// page the C code is working with. V is keyboard matrix row 3 ($DC00 =
// $F7), column 7 ($DC01 bit 7 low when pressed).
__asm upic_live_hook
{
		lda #$f7
		sta $dc00
		lda $dc01
		pha
		lda #$ff
		sta $dc00
		pla
		and #$80
		bne up
		lda v_down
		bne done                 // still held from before
		lda #$01
		sta v_down
		sta live_request
		rts
	up:
		lda #$00
		sta v_down
	done:
		rts
}

// Display for the current mode while generating; columns from bytecol
// on are not computed yet.
static void live_apply(char bytecol)
{
	if (upic_live_mode == UPIC_LIVE_CLASSIC)
	{
		uii_upic_irq_stop();
		uii_upic_set_window(0, 0);
		return;
	}
	if (upic_live_mode == UPIC_LIVE_BAR)
	{
		// Clear the band where nothing is computed yet, so the bar
		// starts cleanly instead of showing the previous picture.
		char c;
		for (c = bytecol; c < UPIC_BYTES / UPIC_HEIGHT; c++)
			memset(upic_column(c) + BAND_FIRST, 0, BAND_ROWS);
		uii_upic_set_window(BAND_FIRST, BAND_ROWS);
	}
	else
		uii_upic_set_window(0, 0);
	uii_upic_irq_hook = upic_live_hook;
	uii_upic_irq_start();
}

void upic_live_begin(void)
{
	live_request = 0;
	live_apply(0);
}

char upic_live_column(char bytecol)
{
	if (upic_live_mode == UPIC_LIVE_CLASSIC)
		__asm { jsr upic_live_hook }
	else if (upic_live_mode == UPIC_LIVE_BAR)
		// Progress cursor: the band rows of the column about to be
		// computed in white (both pixels color 8, white in every
		// gradient), so the bar's front edge shows even where the
		// picture is black. The generator overwrites these rows with
		// the real pixels as it computes the column (it always writes
		// all 256 rows), so the cursor needs no clearing.
		memset(upic_column(bytecol) + BAND_FIRST, 0x88, BAND_ROWS);
	if (live_request)
	{
		live_request = 0;
		upic_live_cycle();
		live_apply(bytecol);
	}
	return upic_live_mode == UPIC_LIVE_CLASSIC;
}

// In the $E800 pool (memmap.h): "main" has no room left.
#pragma code(upiccode)

void upic_live_end(void)
{
	if (upic_live_mode == UPIC_LIVE_BAR)
	{
		// Roll out: the band grows by 8 rows a frame around its middle
		// until it covers the whole picture (about 0.3 s).
		char half;
		for (half = BAND_ROWS / 2 + 4; half < 128; half += 4)
		{
			char f = uii_upic_framecount;
			uii_upic_set_window(128 - half, half * 2);
			while (uii_upic_framecount == f)
				;
		}
	}
	uii_upic_irq_stop();                 // leaves interrupts masked
	uii_upic_set_window(0, 0);
}

void upic_live_cycle(void)
{
	upic_live_mode = upic_live_mode == UPIC_LIVE_FULL ? UPIC_LIVE_BAR : upic_live_mode + 1;
}

#pragma code(code)
