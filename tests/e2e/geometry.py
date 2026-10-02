"""Which picture pixel each screen dot shows, and the 48 MHz / 64 MHz cross-check.

The 64 MHz path shows all 384 pixels of a row, exactly one dot each. The
48 MHz path (Ultimate 64 / Elite I) shows pixels 4m, 4m+2 and 4m+3 of all
96 groups: 4m on dots 4m and 4m+1, 4m+2 and 4m+3 on their own dot (since
v1.2.0, ultimate_upic_lib's exact pitch). The two are compared through
measured maps: the "pattern" step of the end-to-end test fills the
picture with a stripe pattern in which every pixel differs from its
neighbours, and the captured frame then says which pixel each dot shows.
expected_map() gives the exact map each path should produce.
"""

COLUMNS = 192
GROUPS_48 = 96          # all 96 groups (ultimate_upic_lib 48 MHz path)


def pattern_byte(column):
    """Byte for one byte column of the stripe pattern: the two pixels of
    a column and of neighbouring columns all differ, and 0 (the border
    colour) is never used."""
    lo = (column % 15) + 1
    hi = ((column + 7) % 15) + 1
    return lo | (hi << 4)


def pattern_colour(pixel):
    b = pattern_byte(pixel // 2)
    return b >> 4 if pixel & 1 else b & 15


def shown_pixels(mode):
    """Picture pixels a row shows, left to right."""
    if mode == "64mhz":
        return list(range(2 * COLUMNS))
    return [4 * m + q for m in range(GROUPS_48) for q in (0, 2, 3)]


def expected_map(mode, first=0, width=384):
    """The exact dot -> pixel map of a path whose pixel 0 starts on dot
    `first` (None for dots outside the picture)."""
    dots = [None] * width
    for x in range(2 * COLUMNS):
        p = x if mode == "64mhz" else (x & ~1 if x % 4 < 2 else x)
        if 0 <= first + x < width:
            dots[first + x] = p
    return dots


def pixel_map(row, mode):
    """Map each dot of a captured pattern row to the picture pixel it shows
    (None for the border). Raises ValueError if the row does not show the
    pixels `mode` should, in that order."""
    shown = shown_pixels(mode)
    runs, x = [], 0
    while x < len(row):
        start, colour = x, row[x]
        while x < len(row) and row[x] == colour:
            x += 1
        runs.append((start, x, colour))
    picture = [r for r in runs if r[2] != 0]
    for first in range(len(shown)):
        if all(pattern_colour(shown[first + i]) == r[2] for i, r in enumerate(picture[:40])):
            break
    else:
        raise ValueError("pattern row does not match the %s path's pixel order" % mode)
    dots = [None] * len(row)
    for i, (start, end, colour) in enumerate(picture):
        j = first + i
        if j >= len(shown) or pattern_colour(shown[j]) != colour:
            raise ValueError("pattern row breaks the %s pixel order at dot %d" % (mode, start))
        for d in range(start, end):
            dots[d] = shown[j]
    return dots


def compare_48_to_64(frame48, frame64, map48, map64):
    """Dots of a 48 MHz frame that differ from what the 64 MHz frame shows
    for the same picture pixel. Dots whose pixel the 64 MHz frame does not
    show (beyond the right edge) are skipped. Returns [(y, x), ...]."""
    where64 = {}
    for x, p in enumerate(map64):
        if p is not None and p not in where64:
            where64[p] = x
    bad = []
    for y, (row48, row64) in enumerate(zip(frame48, frame64)):
        for x, p in enumerate(map48):
            if p is None:
                if map64[x] is None and row48[x] != row64[x]:
                    bad.append((y, x))
            elif p in where64 and row48[x] != row64[where64[p]]:
                bad.append((y, x))
    return bad
