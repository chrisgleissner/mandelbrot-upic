"""Tests for the 48 MHz / 64 MHz display paths and the speed probe.

These run the compiled code from build/mandelupic.prg (release) and
build/mandelupic-force48.prg (test-only, see Makefile) in a
cycle-counting 6502 emulator attached to a model of the Ultimate 64's
CPU timing (tests/machine.py). Build both first:
`make test` does that.

What is checked:
- The model reproduces the hardware bisection recorded in
  render_frame(): the released delay $87 fits a PAL line at 64 MHz,
  $A5 does not.
- The release PRG's 64 MHz render code is the unmodified v1.0.3
  instruction layout, so Elite II / C64U behaviour is unchanged.
- upic_select_display_path() leaves a 64 MHz machine untouched and
  patches exactly the intended bytes on a 48 MHz one, including when
  the FPGA's post-reset forced 1 MHz window is still running.
- Rendering a full 256-row frame at 64 MHz and (patched) at 48 MHz:
  every row's pixels stay inside one raster line, rows land on
  consecutive lines, the next line's $D012 poll still finishes in time,
  pixels are 1-2 dots (64 MHz) or 2-3 dots (48 MHz) wide, and the two
  paths cover nearly the same dots.
- A negative control: the unpatched 64 MHz code on a 48 MHz machine
  does not fit, so the row checks can fail.
"""

import unittest

from machine import (DOTS_PER_LINE, ELITE2, LINES_PER_FRAME, PHI2_PER_LINE,
                     U64, Machine, load_build)

COLUMNS = 192
ROWS = 256
RELOC_COLUMNS = 8          # UPIC_RELOC_COLS in include/upic_viewer.h

# Offsets inside render_frame (include/upic_viewer.c), checked against
# the opcodes found there by test_render_frame_labels.
LINE_OFFSET = 24           # `line: lda $d012`
TRB_OFFSET = 34            # `trb: ldx #$8f`
DLY_OFFSET = 42            # `dly: ldx #$87`
DELAY_64 = 0x87
DELAY_48 = 96              # UPIC_DELAY_48


def column_page(c):
    return 0xE0 + c if c < RELOC_COLUMNS else 0x18 + (c - RELOC_COLUMNS)


def pixel_byte(c, y):
    return (c * 37 + y * 11 + 5) & 0xFF


class RowTimingError(AssertionError):
    pass


def render_frame(image, symbols, ratio, d031=0x8F):
    """Run render_frame() once on a fresh machine; return its trace."""
    m = Machine(image, ratio, d031=d031, start_line=250)
    nybbles = symbols['nybbles'][0]
    for i in range(256):
        m.mem[nybbles + i] = i >> 4
    for c in range(COLUMNS):
        base = column_page(c) << 8
        for y in range(ROWS):
            m.mem[base + y] = pixel_byte(c, y)
    frame = symbols['render_frame'][0]
    m.call(frame)
    lo, hi = symbols['render_line_pixels']
    pixels = [(t, v) for t, v, pc in m.d020_writes if lo <= pc < hi]
    line_reads = [t for t, pc in m.d012_reads if pc == frame + LINE_OFFSET]
    return m, pixels, line_reads


def analyse(m, pixels, line_reads, per_row):
    """Check row placement.

    Returns (first dot, end dot, pixel widths, slack sub-slots): dots are
    counted from the start of the raster line, the end dot is where the
    last pixel ends (assuming it is as wide as the narrowest pixel), and
    slack is how many sub-slots before the end of the line the next row's
    `lda $d012` finished (the smallest over all rows).
    """
    if len(pixels) != per_row * ROWS:
        raise RowTimingError('%d pixel writes, expected %d'
                             % (len(pixels), per_row * ROWS))
    firsts, ends, widths = set(), set(), set()
    first_line = m.line_of(pixels[0][0])
    slack = None
    for k in range(ROWS):
        row = pixels[k * per_row:(k + 1) * per_row]
        line = m.line_of(row[0][0])
        if line != first_line + k:
            raise RowTimingError('row %d painted on line %d, expected %d'
                                 % (k, line, first_line + k))
        if m.line_of(row[-1][0]) != line:
            raise RowTimingError('row %d runs into the next raster line' % k)
        dots = [m.dot_of(t) - line * DOTS_PER_LINE for t, _ in row]
        row_widths = {b - a for a, b in zip(dots, dots[1:])}
        widths |= row_widths
        firsts.add(dots[0])
        ends.add(dots[-1] + min(row_widths))
        if k + 1 < len(line_reads):
            left = (line + 1) * m.ticks_per_line - 1 - line_reads[k + 1]
            if left < 0:
                raise RowTimingError('row %d: next $D012 poll is late' % k)
            slack = left if slack is None else min(slack, left)
    if len(firsts) != 1 or len(ends) != 1:
        raise RowTimingError('row start %s / end %s not constant'
                             % (sorted(firsts), sorted(ends)))
    return firsts.pop(), ends.pop(), sorted(widths), slack


def patched_image(image, symbols, dly):
    m = Machine(image, ELITE2)
    m.mem[symbols['render_frame'][0] + DLY_OFFSET + 1] = dly
    return m.mem


def select(image, symbols, ratio, **kw):
    """Run upic_select_display_path(); return the machine afterwards."""
    m = Machine(image, ratio, **kw)
    m.call(symbols['upic_select_display_path'][0], max_cycles=10**9)
    return m


def expected_48mhz_patch(image, symbols, trb=0x8F):
    lo, hi = symbols['render_line_pixels']
    frame = symbols['render_frame'][0]
    expected = bytearray(image)
    for c in range(COLUMNS):
        p = lo + 12 * c
        nxt = p + 12
        expected[p] = 0xB9
        expected[p + 3] = 0x9D
        expected[p + 6:p + 9] = bytes([0x4C, nxt & 0xFF, nxt >> 8])
    expected[frame + DLY_OFFSET + 1] = DELAY_48
    expected[frame + TRB_OFFSET + 1] = trb
    return expected


def code_diffs(after, expected, symbols):
    """Addresses that differ, ignoring zero page, the stack page and
    data the call itself writes (the probe's result byte)."""
    skip = set()
    if 'upic_probe_class' in symbols:
        a, b = symbols['upic_probe_class']
        skip.update(range(a, b))
    return [a for a in range(0x200, 0x10000)
            if a not in skip and after[a] != expected[a]]


class ReleaseBuild(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.image, cls.symbols = load_build('mandelupic')
        cls.frame = cls.symbols['render_frame'][0]
        cls.nybbles = cls.symbols['nybbles'][0]
        cls.trace_64 = render_frame(cls.image, cls.symbols, ELITE2)
        cls.geometry_64 = analyse(*cls.trace_64, 2 * COLUMNS)

    # -- the model against known hardware behaviour ----------------------
    def test_model_matches_hardware_bisection(self):
        # render_frame()'s comment: $87 renders cleanly on an Elite II,
        # $A5 skews. The model must agree before its 48 MHz numbers mean
        # anything.
        self.assertIsNotNone(self.geometry_64[3])
        m, pixels, reads = render_frame(
            patched_image(self.image, self.symbols, 0xA5), self.symbols, ELITE2)
        with self.assertRaises(RowTimingError):
            analyse(m, pixels, reads, 2 * COLUMNS)

    # -- the 64 MHz path is unchanged ------------------------------------
    def test_64mhz_render_code_is_the_v103_layout(self):
        expected = bytearray()
        for c in range(COLUMNS):
            expected += bytes([0xBE, 0x00, column_page(c), 0x8E, 0x20, 0xD0,
                               0xBD, self.nybbles & 0xFF, self.nybbles >> 8,
                               0x8D, 0x20, 0xD0])
        expected.append(0x60)
        lo, hi = self.symbols['render_line_pixels']
        self.assertEqual(hi - lo, len(expected))
        self.assertEqual(bytes(self.image[lo:hi]), bytes(expected))

    def test_render_frame_labels(self):
        f, img = self.frame, self.image
        self.assertEqual(img[f + LINE_OFFSET:f + LINE_OFFSET + 3],
                         bytes([0xAD, 0x12, 0xD0]))                   # lda $d012
        self.assertEqual(img[f + TRB_OFFSET:f + TRB_OFFSET + 2],
                         bytes([0xA2, 0x8F]))                         # ldx #$8f
        self.assertEqual(img[f + DLY_OFFSET:f + DLY_OFFSET + 5],
                         bytes([0xA2, DELAY_64, 0xCA, 0xD0, 0xFD]))   # delay loop

    def test_timing_critical_code_does_not_depend_on_placement(self):
        # A taken branch that crosses a page costs an extra cycle, which
        # would move every pixel after render_frame's delay loop.
        bne = self.frame + DLY_OFFSET + 3
        self.assertEqual((bne + 2) >> 8, (bne - 1) >> 8,
                         'render_frame delay loop crosses a page')
        # `lda nybbles,x` must never cross a page either.
        self.assertEqual(self.nybbles & 0xFF, 0)

    def test_64mhz_path_on_elite2(self):
        first, end, widths, slack = self.geometry_64
        self.assertEqual(widths, [1, 2])
        self.assertGreaterEqual(slack, 0)
        _, pixels, _ = self.trace_64
        values = [v for _, v in pixels[:2 * COLUMNS]]
        for c in range(COLUMNS):
            b = pixel_byte(c, 0)
            self.assertEqual((values[2 * c] & 15, values[2 * c + 1]),
                             (b & 15, b >> 4))

    # -- display path selection -------------------------------------------
    def test_elite2_keeps_the_64mhz_path(self):
        for start in (0, 31, 255, 300):
            m = select(self.image, self.symbols, ELITE2, start_line=start)
            self.assertEqual(code_diffs(m.mem, self.image, self.symbols), [],
                             'start line %d' % start)

    def test_u64_gets_the_48mhz_path(self):
        expected = expected_48mhz_patch(self.image, self.symbols)
        for start in (0, 31, 255, 300):
            m = select(self.image, self.symbols, U64, start_line=start)
            self.assertEqual(code_diffs(m.mem, expected, self.symbols), [],
                             'start line %d' % start)

    def test_selection_survives_forced_1mhz_after_reset(self):
        # The U64 runs the CPU at 1 MHz for a few seconds after a reset; the
        # probe usually starts inside that window. End the window at
        # several points, including in the middle of a probe loop.
        # One probe loop at 1 MHz: 64764 phi2 of burn plus up to two
        # frames of raster sync.
        loop_phi2 = 64764 + 2 * PHI2_PER_LINE * LINES_PER_FRAME
        expected_48 = expected_48mhz_patch(self.image, self.symbols)
        for ratio, expected in ((ELITE2, self.image), (U64, expected_48)):
            for fraction in (0.0, 0.3, 0.6, 0.95, 1.4):
                end = int(fraction * loop_phi2) * 8 * ratio
                m = select(self.image, self.symbols, ratio,
                           slow_until=end)
                self.assertEqual(code_diffs(m.mem, expected, self.symbols), [],
                                 'ratio %d, force ends at %.2f' % (ratio, fraction))

    # -- the 48 MHz path's timing ----------------------------------------
    def test_48mhz_path_on_u64(self):
        mem = select(self.image, self.symbols, U64).mem
        m, pixels, reads = render_frame(mem, self.symbols, U64)
        first, end, widths, slack = analyse(m, pixels, reads, COLUMNS)
        first64, end64, _, slack64 = self.geometry_64
        self.assertEqual(widths, [2, 3])
        # At least as much per-line slack as the hardware-proven path.
        self.assertGreaterEqual(slack, slack64)
        # Nearly the same screen area (see upic_viewer.c for the trade
        # between the left edge and the slack).
        self.assertLessEqual(abs(first - first64), 3)
        self.assertLessEqual(abs(end - end64), 1)
        # Each byte's low nibble (the even pixel) is what gets shown.
        for k in (0, 1, 128, 255):
            row = pixels[k * COLUMNS:(k + 1) * COLUMNS]
            self.assertEqual([v & 15 for _, v in row],
                             [pixel_byte(c, k) & 15 for c in range(COLUMNS)])

    def test_48mhz_delay_is_the_largest_that_keeps_that_slack(self):
        # One more delay pass (5 cycles) must lose the margin, otherwise
        # the picture could sit further right than it does.
        mem = select(self.image, self.symbols, U64).mem
        mem[self.frame + DLY_OFFSET + 1] = DELAY_48 + 1
        m, pixels, reads = render_frame(mem, self.symbols, U64)
        slack = analyse(m, pixels, reads, COLUMNS)[3]
        self.assertLess(slack, self.geometry_64[3] + 5)

    def test_unpatched_path_does_not_fit_on_u64(self):
        # Negative control for the row checks.
        m, pixels, reads = render_frame(self.image, self.symbols, U64)
        with self.assertRaises(RowTimingError):
            analyse(m, pixels, reads, 2 * COLUMNS)


class Force48Build(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.image, cls.symbols = load_build('mandelupic-force48')

    def test_probe_is_not_linked(self):
        self.assertNotIn('upic_probe_class', self.symbols)

    def test_always_patches_with_index_14(self):
        m = select(self.image, self.symbols, ELITE2)
        expected = expected_48mhz_patch(self.image, self.symbols, trb=0x8E)
        self.assertEqual(code_diffs(m.mem, expected, self.symbols), [])

    def test_48mhz_path_on_elite2(self):
        # turbo_fast() leaves index 15 (64 MHz); the first line's resync
        # write switches to the patched index 14 (48 MHz on Elite II).
        mem = select(self.image, self.symbols, ELITE2).mem
        m, pixels, reads = render_frame(mem, self.symbols, ELITE2)
        first, end, widths, slack = analyse(m, pixels, reads, COLUMNS)
        # How index 14's 48 cycles are spread within each phi2 on an
        # Elite II is modelled only approximately (evenly), so accept
        # either pixel width the U64 path shows.
        self.assertLessEqual(set(widths), {2, 3})
        self.assertGreaterEqual(slack, 2)


if __name__ == '__main__':
    unittest.main()
