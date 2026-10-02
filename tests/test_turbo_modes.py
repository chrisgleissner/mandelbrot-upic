"""Tests for the 48 MHz / 64 MHz display paths and the speed probe.

These run the compiled code from build/mandelupic.prg (release) and
build/mandelupic-force48.prg (test-only, see Makefile) in a
cycle-counting 6502 emulator attached to a model of the Ultimate 64's
CPU timing (tests/machine.py). Build both first:
`make test` does that.

What is checked:
- The model reproduces two hardware bisections: at 64 MHz the released
  delay $87 fits a PAL line and $A5 does not (render_frame()'s
  comment); on an Ultimate 64 Elite the first 48 MHz layout
  (`sta $d020,x`, 2026-09-21) fits with delay 56 and not with 57,
  because of the indexed store's dummy read of $D020.
- The release PRG's 64 MHz render code is the unmodified v1.0.3
  instruction layout, so Elite II / C64U behaviour is unchanged.
- upic_select_display_path() leaves a 64 MHz machine untouched and
  rebuilds exactly the intended bytes on a 48 MHz one, including when
  the post-reset forced 1 MHz window is still running, when the speed
  register was reset after uii_turbo_fast(), and when the probe gives up
  on a machine that never leaves 1 MHz.
- The 48 MHz path also sets upic_frame_quarters to 3, so a live frame
  is shown every 3/4 column during generation (every column at 64 MHz).
- Rendering a full 256-row frame at 64 MHz and (rebuilt) at 48 MHz:
  every row's pixels stay inside one raster line, rows land on
  consecutive lines, the next line's $D012 poll still finishes in time,
  pixels are 1-2 dots wide, the 48 MHz path shows pixels 4m, 4m+2 and
  4m+3 of every 4, and its delay centres each shown pixel on the dots
  the 64 MHz path shows it on (within 1.5 dots, as measured on
  hardware).
- Negative controls: the unpatched 64 MHz code and the first 48 MHz
  layout at its original delay (96) do not fit a line on a 48 MHz
  machine, so the row checks can fail.
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
DELAY_48 = 99              # UPIC_DELAY_48
GROUPS_48 = 94             # UPIC_GROUPS_48
PIXELS_48 = 3 * GROUPS_48  # pixel writes per row on the 48 MHz path


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


def analyse(m, pixels, line_reads, per_row, constant_start=True):
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
    if constant_start and (len(firsts) != 1 or len(ends) != 1):
        raise RowTimingError('row start %s / end %s not constant'
                             % (sorted(firsts), sorted(ends)))
    return min(firsts), max(ends), sorted(widths), slack


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
    """The render code the 48 MHz path must end up with, written out
    instruction by instruction rather than by the patcher's own copy
    rule, so a wrong copy cannot pass."""
    lo, hi = symbols['render_line_pixels']
    frame = symbols['render_frame'][0]
    nyb = symbols['nybbles'][0]
    expected = bytearray(image)
    code = bytearray()
    for g in range(GROUPS_48):
        a, b = column_page(2 * g), column_page(2 * g + 1)
        code += bytes([0xB9, 0x00, a, 0x8D, 0x20, 0xD0,          # lda A,y / sta $d020
                       0xBE, 0x00, b, 0x8E, 0x20, 0xD0,          # ldx B,y / stx $d020
                       0xBD, nyb & 0xFF, nyb >> 8, 0x8D, 0x20, 0xD0])  # lda nyb,x / sta
    code.append(0x60)                                            # rts
    expected[lo:lo + len(code)] = code
    expected[frame + DLY_OFFSET + 1] = DELAY_48
    expected[frame + TRB_OFFSET + 1] = trb
    # Live frames every 3/4 column instead of every column (upic_viewer.h).
    expected[symbols['upic_frame_quarters'][0]] = 3
    return expected


def first_48mhz_layout(image, symbols, dly):
    """The first 48 MHz layout (2026-09-21), which failed on hardware:
    one pixel per byte column, `lda col,y / sta $d020,x / jmp next`,
    with X = 0 left by render_frame's delay loop."""
    lo, hi = symbols['render_line_pixels']
    mem = bytearray(image)
    for c in range(COLUMNS):
        p = lo + 12 * c
        nxt = p + 12
        mem[p] = 0xB9
        mem[p + 3] = 0x9D
        mem[p + 6:p + 9] = bytes([0x4C, nxt & 0xFF, nxt >> 8])
    mem[symbols['render_frame'][0] + DLY_OFFSET + 1] = dly
    return mem


def cycles_48mhz(cycles, ratio):
    """Sub-slots taken by `cycles` CPU cycles at 48 MHz (47 cycles per
    phi2 on both boards), for comparing with analyse()'s slack."""
    return cycles * 8 * ratio // 47


def row_dots(m, pixels, per_row, k=0):
    """Dot (from the start of its raster line) of each pixel write in row k."""
    row = pixels[k * per_row:(k + 1) * per_row]
    line = m.line_of(row[0][0])
    return [m.dot_of(t) - line * DOTS_PER_LINE for t, _ in row]


def shown_48mhz(values_of_byte):
    """Pixel values the 48 MHz path writes for one row: per pair of
    byte columns A, B the low nibble of A, then both nibbles of B."""
    out = []
    for g in range(GROUPS_48):
        a, b = values_of_byte(2 * g), values_of_byte(2 * g + 1)
        out += [a & 15, b & 15, b >> 4]
    return out


def code_diffs(after, expected, symbols):
    """Addresses that differ, ignoring zero page, the stack page and
    data the call itself writes (the probe's result and retry count)."""
    skip = set()
    for name in ('upic_probe_class', 'upic_probe_tries'):
        if name in symbols:
            a, b = symbols[name]
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

    def test_model_matches_u64_hardware_bisection(self):
        # Measured on an Ultimate 64 Elite (2026-09-28) with the first
        # 48 MHz layout: delay 56 renders cleanly, 57 puts every row two
        # raster lines apart. The model gets this only because it charges
        # the VIC read wait for `sta $d020,x`'s dummy read.
        for dly, fits in ((56, True), (57, False), (DELAY_48 - 4, False)):
            mem = first_48mhz_layout(self.image, self.symbols, dly)
            m, pixels, reads = render_frame(mem, self.symbols, U64)
            if fits:
                analyse(m, pixels, reads, COLUMNS)
            else:
                with self.assertRaises(RowTimingError, msg='delay %d' % dly):
                    analyse(m, pixels, reads, COLUMNS)

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

    def test_probe_restores_a_lost_speed_setting(self):
        # The speed register reads back 1 MHz (index 0) when the probe
        # starts, e.g. because something reset it after uii_turbo_fast().
        # The probe writes it again on every retry, so both boards still
        # end up on their own path.
        expected_48 = expected_48mhz_patch(self.image, self.symbols)
        for ratio, expected in ((ELITE2, self.image), (U64, expected_48)):
            m = select(self.image, self.symbols, ratio, d031=0x80)
            self.assertEqual(code_diffs(m.mem, expected, self.symbols), [],
                             'ratio %d' % ratio)

    def test_probe_gives_up_at_1mhz(self):
        # Turbo never comes on (e.g. no .cfg, Turbo Control off): the
        # probe must stop after its retry budget and keep the 64 MHz
        # path, not loop forever. The budget is cut from 256 loops to 3
        # here to keep the test fast; the retry logic is the same.
        image = bytearray(self.image)
        image[self.symbols['upic_probe_tries'][0]] = 3
        for ratio in (ELITE2, U64):
            m = select(image, self.symbols, ratio, slow_until=10**12)
            self.assertEqual(code_diffs(m.mem, image, self.symbols), [],
                             'ratio %d' % ratio)
        # Giving up always means the 64 MHz path, even when the last
        # reading said 48 MHz: with a budget of one loop, a U64 at full
        # speed reads 48 MHz once, has nothing to confirm it with, and
        # must not take the 48 MHz path on that alone.
        image[self.symbols['upic_probe_tries'][0]] = 1
        m = select(image, self.symbols, U64)
        self.assertEqual(code_diffs(m.mem, image, self.symbols), [])

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
        first, end, widths, slack = analyse(m, pixels, reads, PIXELS_48)
        first64, end64, _, slack64 = self.geometry_64
        # 8 cycles per pixel at 47 per phi2: 1.36 dots, so 1 or 2.
        self.assertEqual(widths, [1, 2])
        # The picture starts one dot left of the 64 MHz one (see
        # test_48mhz_delay_centres_the_pixels)...
        self.assertEqual(first, first64 - 1)
        # ...and the 94 groups reach past the 384-dot visible area
        # (the 64 MHz path's pixel 369, the last one visible, ends 376
        # dots after its first).
        self.assertGreaterEqual(end - first64, 376)
        # Well clear of the end of the line: the budget in upic_viewer.c
        # leaves 38 cycles.
        self.assertGreaterEqual(slack, cycles_48mhz(25, U64))
        # Pixels 4m, 4m+2 and 4m+3 of every 4 are shown.
        for k in (0, 1, 128, 255):
            row = pixels[k * PIXELS_48:(k + 1) * PIXELS_48]
            self.assertEqual([v & 15 for _, v in row],
                             shown_48mhz(lambda c: pixel_byte(c, k)))

    def position_errors(self, dly):
        """For each pixel the 48 MHz path shows with this delay, how far
        (in dots) its centre lies from the centre of the same pixel on
        the 64 MHz path, over the 376 dots the 64 MHz path shows."""
        m, pixels, _ = self.trace_64
        d64 = row_dots(m, pixels, 2 * COLUMNS)
        centre64 = {p: (d64[p] + d64[p + 1]) / 2 for p in range(2 * COLUMNS - 1)}
        mem = select(self.image, self.symbols, U64).mem
        mem[self.frame + DLY_OFFSET + 1] = dly
        m, pixels, _ = render_frame(mem, self.symbols, U64)
        d48 = row_dots(m, pixels, PIXELS_48)
        shown = [4 * g + q for g in range(GROUPS_48) for q in (0, 2, 3)]
        return [(d48[j] + d48[j + 1]) / 2 - centre64[shown[j]]
                for j in range(PIXELS_48 - 1) if d48[j + 1] <= d64[0] + 376]

    def test_48mhz_delay_centres_the_pixels(self):
        # The 48 MHz pitch is 0.5% wider, so no delay puts every pixel on
        # its 64 MHz dots; DELAY_48 centres the error. Measured on an
        # Ultimate 64 Elite against a C64 Ultimate (2026-09-28), and
        # reproduced by the model: mean -0.80 / +0.05 / +0.90 dots and
        # largest 2.5 / 1.5 / 2.5 dots for delays 98 / 99 / 100.
        errors = {d: self.position_errors(d) for d in (DELAY_48 - 1, DELAY_48, DELAY_48 + 1)}
        worst = {d: max(abs(e) for e in errors[d]) for d in errors}
        mean = sum(errors[DELAY_48]) / len(errors[DELAY_48])
        self.assertLessEqual(worst[DELAY_48], 1.5)
        self.assertLess(abs(mean), 0.25)
        self.assertLess(worst[DELAY_48], worst[DELAY_48 - 1])
        self.assertLess(worst[DELAY_48], worst[DELAY_48 + 1])

    def test_first_48mhz_layout_does_not_fit_on_u64(self):
        # Negative control: the layout that failed on hardware, at the
        # delay it shipped with, overruns the line in the model too.
        mem = first_48mhz_layout(self.image, self.symbols, 96)
        m, pixels, reads = render_frame(mem, self.symbols, U64)
        with self.assertRaises(RowTimingError):
            analyse(m, pixels, reads, COLUMNS)

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
        # uii_turbo_fast() leaves index 15 (64 MHz); the first line's resync
        # write switches to the patched index 14 (48 MHz on Elite II).
        mem = select(self.image, self.symbols, ELITE2).mem
        m, pixels, reads = render_frame(mem, self.symbols, ELITE2)
        # How index 14's 48 cycles are spread within each phi2 on an
        # Elite II is modelled only approximately (evenly). With that
        # spread every other row starts one phi2 later in the model; on
        # a real C64 Ultimate every row of the force48 build starts on
        # the same dot (checked 2026-09-28, 50 frames). So this checks
        # only what does not depend on the spread: every row fits its
        # line with room to spare, pixels are 1-2 dots wide, and the
        # shown pixels are the ones the U64 path shows.
        first, end, widths, slack = analyse(m, pixels, reads, PIXELS_48,
                                            constant_start=False)
        self.assertLessEqual(set(widths), {1, 2})
        self.assertGreaterEqual(slack, cycles_48mhz(25, ELITE2))
        row = pixels[:PIXELS_48]
        self.assertEqual([v & 15 for _, v in row],
                         shown_48mhz(lambda c: pixel_byte(c, 0)))


if __name__ == '__main__':
    unittest.main()
