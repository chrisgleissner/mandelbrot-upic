/*****************************************************************
Mandelbrot Upic -- entry point

Banks ROM out, pushes the palette, generates the fractal (at 64 MHz
turbo, see below), displays it via the Upic border-color raster loop
and lets the user browse/zoom forever (zoom.c) -- there is no exit;
see zoom.h's own comment for why.

Requires firmware 3.15+ -- in practice an Ultimate 64 Elite 2 for now,
since the corresponding C64U firmware hasn't been released yet.
******************************************************************/

#include "memmap.h"            // first: sections the libraries are placed in
#include <c64/cia.h>
#include <string.h>
#include "ultimate_turbo_lib.h"
#include "ultimate_common_lib.h"
#include "ultimate_dos_lib.h"
#include "upic_viewer.h"
#include "mandelbrot.h"
#include "rombank.h"
#include "zoom.h"

// 160 bytes of header text, in the $0200 bss region (memmap.h).
#pragma bss(bssovl1)
static char save_text[160];
#pragma bss(bss)

// ---------------------------------------------------------------
// Startup (initcode, memmap.h): runs once, before the first picture;
// the same RAM is picture columns 184-191 afterwards.
// ---------------------------------------------------------------
#pragma code(initcode)
#pragma data(initdata)

// uii_setpalette() can be rejected ("81,INVALID P...") right after a
// fresh device reboot, even on firmware where this exact call is
// otherwise known-working -- working theory: some UCI subsystem beyond
// basic detection isn't fully up yet immediately after boot, even
// though uii_detect() itself already succeeds. Retry a few times with
// a short settle delay rather than accept the first result -- CIA1
// TOD tenths, same timing primitive uii_wait_for_uci() already uses.
static void setpalette_retry(const char *rgb48)
{
	unsigned char attempt;
	for (attempt = 0; attempt < 10; attempt++)
	{
		uii_setpalette(rgb48);
		if (UII_SUCCESS)
			return;

		cia1.todt = 0;
		while (cia1.todt < 2)  // ~0.2s
			;
	}
}

// The constant lines of the .upic text (Upic v1.3: four ASCII lines of
// 40, space-filled): line 1 program and version, line 4 "Created with"
// as Aleksi Eeben suggested for it. Built once into save_text here, from
// strings in the startup-only data, so they cost no runtime memory.
static const char save_line1[] = "MANDELBROT UPIC " VERSION;
static const char save_line4[] = "Created with Xander's Mandelbrot Upic";

static void save_text_init(void)
{
	memset(save_text, 0x20, 160);
	memcpy(save_text, save_line1, sizeof(save_line1) - 1 < 40 ? sizeof(save_line1) - 1 : 40);
	memcpy(save_text + 120, save_line4, sizeof(save_line4) - 1);
}

// Returns whether the UCI answered (palette pushed).
__noinline static unsigned char program_startup(void)
{
	unsigned char uci_ready = uii_wait_for_uci(5);

	mandel_tables_init();       // palettes/color table into RAM (initdata copies)
	save_text_init();           // constant lines of the .upic text

	// Palette pushed before generation -- mandelbrot_generate() shows
	// the picture LIVE as it builds (see its own comment), which needs
	// the fractal's own palette active from the start to look right.
	// A plain-text welcome/progress screen was tried in between (see
	// git history) but removed once live rendering made it redundant
	// -- the user explicitly preferred watching the picture build over
	// a progress bar, flicker and all.
	if (uci_ready)
		setpalette_retry(mandelbrot_palette);

	// Turbo on BEFORE generating, not just before displaying -- the
	// whole point of doing this on-device is the 64x speedup on the
	// escape-time iteration itself, which is by far the slow part.
	uii_turbo_fast();

	// One PRG for both turbo ceilings: an Elite II / C64U reaches 64 MHz
	// at speed index 15, an Ultimate 64 / Elite I only 48 MHz. The
	// library measures which and builds the matching Upic renderer (the
	// 48 MHz one shows 3 of every 4 pixels) -- see
	// upic_select_display_path() in upic_viewer.c. `make force48` builds a
	// test-only PRG that always takes the 48 MHz path (at 48 MHz), so it
	// can be checked on a 64 MHz machine.
	upic_select_display_path();

	return uci_ready;
}

#pragma code(code)
#pragma data(data)

// ---------------------------------------------------------------
// F1: save the picture
// ---------------------------------------------------------------
// Saves MANDEL01.UPIC, MANDEL02.UPIC, ... (first free number) in the
// current UCI directory as a Upic v1.3 file (ultimate_upic_lib): the
// bitmap, the current gradient's palette, and two text lines with this
// program's version and the view (mandel_x0/y0/dx/dy, 16-bit hex, Q5.11
// fixed point). If the directory can't take the file (after a reset the
// UCI's current directory can be the virtual root "/"), it tries once more
// in the UCI home directory. The screen stays black while the file is
// written; on an error the picture blinks three times.


// In the $E800 pool (memmap.h): "main" has no room left for it.
#pragma code(upiccode)
#pragma data(moddata)

static char *put_hex(char *p, unsigned v)
{
	char i;
	for (i = 0; i < 4; i++)
	{
		char d = (char)(v >> 12);
		*p++ = d < 10 ? 0x30 + d : 0x37 + d;   // ASCII 0-9, A-F
		v <<= 4;
	}
	return p + 1;                              // one space between fields
}

// __noinline: called once, so Oscar64 -O2 would inline it into main() and
// lose this #pragma code (the "main" region has no room for it).
__noinline static char save_picture(const char *palette)
{
	static char name[] = "MANDEL00.UPIC";
	char home_tried = 0;
	char *p;

	// Lines 1 and 4 are filled once at startup (save_text_init()); only
	// the view on line 2 changes.
	p = put_hex(save_text + 40, (unsigned)mandel_x0);
	p = put_hex(p, (unsigned)mandel_y0);
	p = put_hex(p, (unsigned)mandel_dx);
	put_hex(p, (unsigned)mandel_dy);

	// Number 01-99 kept as two ASCII digits (no division needed).
	name[6] = 0x30;
	name[7] = 0x31;
	for (;;)
	{
		if (uii_upic_save(name, palette, save_text, 0))
			return 1;
		if (uii_status[0] != 'F')                // not "FILE EXISTS"
		{
			if (home_tried)
				return 0;
			uii_change_dir_home();               // retry, same number
			home_tried = 1;
			continue;
		}
		if (++name[7] > 0x39)
		{
			name[7] = 0x30;
			if (++name[6] > 0x39)
				return 0;                        // all 99 in use
		}
	}
}

// Wait n frames with nothing drawn: the display is off, so the screen
// shows the border color (black) meanwhile.
static void wait_frames(char n)
{
	while (n--)
	{
		while (!(*(volatile char *)0xd011 & 0x80))
			;
		while (*(volatile char *)0xd011 & 0x80)
			;
	}
}

#pragma code(code)
#pragma data(data)

int main(void)
{
	unsigned char uci_ready;

	rombank_out();  // must run before mandelbrot_generate(), which writes
	// part of the picture to $E000 (and before anything in the $E800+
	// upiccode pool runs). The UCI and turbo functions now come from the
	// ultimate-uci-oscar64 library and live in the default "main" region.

	// Interrupts masked globally here, for the rest of the program's
	// entire lifetime, and never re-enabled (2026-09-11) -- root-caused
	// a real-hardware "any key press drops to a JiffyDOS text screen"
	// crash (confirmed present even in the completely unmodified,
	// pre-zoom-feature baseline, so it predates the interactive zoom
	// work entirely) to mmap_trampoline() (rombank.c) chaining a
	// same-tick hardware interrupt into real KERNAL/JiffyDOS ROM code
	// while this program's own direct-CIA keyboard polling is active --
	// confirmed by permanently masking IRQ here, which eliminated the
	// crash entirely on real hardware (tested extensively: C key, Q key,
	// no more drops to text mode). This program never genuinely needs a
	// real interrupt for anything -- no music, no raster-IRQ effects,
	// every wait loop in this codebase (the Upic frame loop's own raster
	// sync included) is plain busy-polled -- so permanently masking IRQ
	// costs nothing functionally. NMI (RESTORE key) still isn't masked
	// by this (SEI can't touch it) but isn't part of this bug family.
	//
	// v1.2.0 exception: the Bar and Full live views (upic_viewer.c) run
	// the library's raster interrupt WHILE a picture is computed. That
	// is safe from the bug above: it installs its own $FFFE/$FFFA vectors
	// with the ROMs banked out and switches CIA1's timer interrupts off,
	// so no interrupt can reach KERNAL/JiffyDOS code, and it masks
	// interrupts again before browse mode (keyboard polling) resumes.
	__asm { sei }

	uci_ready = program_startup();

	// Startup is over: the initcode area ($C800-$CFFF, memmap.h) is
	// picture columns 184-191 from now on. Clear it so the first live
	// frames show black there, not the startup code as pixels.
	memset(upic_column(184), 0, 8 * 256);

	mandelbrot_generate();

	// Let the user pick a zoom target on the completed picture
	// (zoom.c) -> generate again at the new bounds -> repeat, forever
	// -- there is no way out of this loop, see zoom.h's own comment on
	// why there's no quit key. uii_setpalette() for a 'C' press is
	// pushed HERE, from main()'s own context, not from inside
	// zoom_select() itself -- see zoom_pending_palette's own comment
	// in zoom.h for why.
	for (;;)
	{
		unsigned char zr = zoom_select();

		if (zr == ZOOM_PALETTE_CHANGED)
		{
			if (uci_ready)
				uii_setpalette(zoom_pending_palette);
			continue;  // same view, no regenerate -- straight back to zoom_select()
		}

		if (zr == ZOOM_SAVE)
		{
			if (!uci_ready || !save_picture(zoom_pending_palette))
			{
				char i;
				for (i = 0; i < 3; i++)        // error: blink three times
				{
					wait_frames(10);
					for (zr = 0; zr < 10; zr++)
						upic_show_frame();
				}
			}
			continue;  // same view, back to zoom_select()
		}

		// ZOOM_CONFIRMED
		mandelbrot_generate();
	}
}
