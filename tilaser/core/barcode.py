"""QR-Code (ISO/IEC 18004, Byte-Modus, Version 1–40) und Code 128 (Zeichensatz B) als Modulraster.

Ohne Fremdbibliothek; der Aufbau folgt dem Standard (Reed-Solomon über GF(256), Masken mit
Strafpunkt-Bewertung). Ergebnis: Liste von Liste von bool (True = dunkles Modul) bzw. Strichbreiten.

Danksagung: Ablauf und Tabellenform des QR-Encoders orientieren sich an der QR-Code-Bibliothek von
Project Nayuki (Copyright (c) Project Nayuki, MIT-Lizenz, https://www.nayuki.io/page/qr-code-generator-library).
Die Tabellen (Fehlerkorrektur-Blöcke) sind Werte aus ISO/IEC 18004, die Code-128-Muster aus ISO/IEC 15417.
"""
from __future__ import annotations

# Fehlerkorrektur-Codewörter je Block und Anzahl Blöcke, je Stufe L, M, Q, H und Version 0–40
_ECC_PER_BLOCK = (
    (-1, 7, 10, 15, 20, 26, 18, 20, 24, 30, 18, 20, 24, 26, 30, 22, 24, 28, 30, 28, 28, 28, 28, 30, 30, 26, 28,
     30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30),
    (-1, 10, 16, 26, 18, 24, 16, 18, 22, 22, 26, 30, 22, 22, 24, 24, 28, 28, 26, 26, 26, 26, 28, 28, 28, 28, 28,
     28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28),
    (-1, 13, 22, 18, 26, 18, 24, 18, 22, 20, 24, 28, 26, 24, 20, 30, 24, 28, 28, 26, 30, 28, 30, 30, 30, 30, 28,
     30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30),
    (-1, 17, 28, 22, 16, 22, 28, 26, 26, 24, 28, 24, 28, 22, 24, 24, 30, 28, 28, 26, 28, 30, 24, 30, 30, 30, 30,
     30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30))
_NUM_BLOCKS = (
    (-1, 1, 1, 1, 1, 1, 2, 2, 2, 2, 4, 4, 4, 4, 4, 6, 6, 6, 6, 7, 8, 8, 9, 9, 10, 12, 12, 12, 13, 14, 15, 16, 17,
     18, 19, 19, 20, 21, 22, 24, 25),
    (-1, 1, 1, 1, 2, 2, 4, 4, 4, 5, 5, 5, 8, 9, 9, 10, 10, 11, 13, 14, 16, 17, 17, 18, 20, 21, 23, 25, 26, 28, 29,
     31, 33, 35, 37, 38, 40, 43, 45, 47, 49),
    (-1, 1, 1, 2, 2, 4, 4, 6, 6, 8, 8, 8, 10, 12, 16, 12, 17, 16, 18, 21, 20, 23, 23, 25, 27, 29, 34, 34, 35, 38,
     40, 43, 45, 48, 51, 53, 56, 59, 62, 65, 68),
    (-1, 1, 1, 2, 4, 4, 4, 5, 6, 8, 8, 11, 11, 16, 16, 18, 16, 19, 21, 25, 25, 25, 34, 30, 32, 35, 37, 40, 42, 45,
     48, 51, 54, 57, 60, 63, 66, 70, 74, 77, 81))
_ECL = {"L": (0, 1), "M": (1, 0), "Q": (2, 3), "H": (3, 2)}     # Index, Formatbits


class BarcodeError(ValueError):
    pass


def _raw_modules(ver: int) -> int:
    n = (16 * ver + 128) * ver + 64
    if ver >= 2:
        na = ver // 7 + 2
        n -= (25 * na - 10) * na - 55
        if ver >= 7:
            n -= 36
    return n


def _data_codewords(ver: int, ei: int) -> int:
    return _raw_modules(ver) // 8 - _ECC_PER_BLOCK[ei][ver] * _NUM_BLOCKS[ei][ver]


def _gf_mul(x: int, y: int) -> int:
    z = 0
    for i in range(7, -1, -1):
        z = (z << 1) ^ ((z >> 7) * 0x11D)
        z ^= ((y >> i) & 1) * x
    return z & 0xFF


def _rs_divisor(degree: int) -> list[int]:
    res = [0] * (degree - 1) + [1]
    root = 1
    for _ in range(degree):
        for j in range(degree):
            res[j] = _gf_mul(res[j], root)
            if j + 1 < degree:
                res[j] ^= res[j + 1]
        root = _gf_mul(root, 0x02)
    return res


def _rs_remainder(data: list[int], div: list[int]) -> list[int]:
    res = [0] * len(div)
    for b in data:
        f = b ^ res.pop(0)
        res.append(0)
        for i, c in enumerate(div):
            res[i] ^= _gf_mul(c, f)
    return res


def _alignment_positions(ver: int, size: int) -> list[int]:
    if ver == 1:
        return []
    na = ver // 7 + 2
    step = (ver * 8 + na * 3 + 5) // (na * 4 - 4) * 2
    return list(reversed([size - 7 - i * step for i in range(na - 1)] + [6]))


def qr_matrix(text: str, ecl: str = "M") -> list[list[bool]]:
    """QR-Code für 'text' (UTF-8, Byte-Modus). ecl: L, M, Q oder H."""
    ei, fbits = _ECL[ecl.upper()]
    data = text.encode("utf-8")
    for ver in range(1, 41):
        cc_bits = 8 if ver <= 9 else 16
        need = 4 + cc_bits + 8 * len(data)
        cap = _data_codewords(ver, ei) * 8
        if need <= cap:
            break
    else:
        raise BarcodeError("Text zu lang für einen QR-Code.")
    # Bitstrom
    bits: list[int] = []

    def put(val, n):
        bits.extend((val >> i) & 1 for i in range(n - 1, -1, -1))
    put(0b0100, 4)
    put(len(data), cc_bits)
    for b in data:
        put(b, 8)
    put(0, min(4, cap - len(bits)))
    put(0, (-len(bits)) % 8)
    pad = 0xEC
    while len(bits) < cap:
        put(pad, 8)
        pad ^= 0xEC ^ 0x11
    words = [int("".join(map(str, bits[i:i + 8])), 2) for i in range(0, len(bits), 8)]
    # Fehlerkorrektur und Verschachtelung
    nblocks, ecclen = _NUM_BLOCKS[ei][ver], _ECC_PER_BLOCK[ei][ver]
    raw = _raw_modules(ver) // 8
    nshort = nblocks - raw % nblocks
    shortlen = raw // nblocks
    div = _rs_divisor(ecclen)
    blocks, k = [], 0
    for i in range(nblocks):
        dat = words[k:k + shortlen - ecclen + (0 if i < nshort else 1)]
        k += len(dat)
        ecc = _rs_remainder(dat, div)
        if i < nshort:
            dat = dat + [0]
        blocks.append(dat + ecc)
    final = []
    for i in range(len(blocks[0])):
        for j, blk in enumerate(blocks):
            if i != shortlen - ecclen or j >= nshort:
                final.append(blk[i])
    # Funktionsmuster
    size = ver * 4 + 17
    mod = [[False] * size for _ in range(size)]
    fn = [[False] * size for _ in range(size)]

    def setf(x, y, dark):
        mod[y][x] = dark
        fn[y][x] = True
    for i in range(size):
        setf(6, i, i % 2 == 0)
        setf(i, 6, i % 2 == 0)
    for cx, cy in ((3, 3), (size - 4, 3), (3, size - 4)):
        for dy in range(-4, 5):
            for dx in range(-4, 5):
                x, y = cx + dx, cy + dy
                if 0 <= x < size and 0 <= y < size:
                    setf(x, y, max(abs(dx), abs(dy)) not in (2, 4))
    al = _alignment_positions(ver, size)
    n = len(al)
    for i in range(n):
        for j in range(n):
            if (i == 0 and j == 0) or (i == 0 and j == n - 1) or (i == n - 1 and j == 0):
                continue
            for dy in range(-2, 3):
                for dx in range(-2, 3):
                    setf(al[i] + dx, al[j] + dy, max(abs(dx), abs(dy)) != 1)

    def draw_format(mask):
        d = fbits << 3 | mask
        rem = d
        for _ in range(10):
            rem = (rem << 1) ^ ((rem >> 9) * 0x537)
        b = (d << 10 | rem) ^ 0x5412

        def bit(i):
            return (b >> i) & 1 == 1
        for i in range(6):
            setf(8, i, bit(i))
        setf(8, 7, bit(6))
        setf(8, 8, bit(7))
        setf(7, 8, bit(8))
        for i in range(9, 15):
            setf(14 - i, 8, bit(i))
        for i in range(8):
            setf(size - 1 - i, 8, bit(i))
        for i in range(8, 15):
            setf(8, size - 15 + i, bit(i))
        setf(8, size - 8, True)
    draw_format(0)
    if ver >= 7:
        rem = ver
        for _ in range(12):
            rem = (rem << 1) ^ ((rem >> 11) * 0x1F25)
        b = ver << 12 | rem
        for i in range(18):
            bt = (b >> i) & 1 == 1
            a, c = size - 11 + i % 3, i // 3
            setf(a, c, bt)
            setf(c, a, bt)
    # Daten im Zickzack
    i = 0
    right = size - 1
    while right >= 1:
        if right == 6:
            right = 5
        for vert in range(size):
            for j in range(2):
                x = right - j
                up = ((right + 1) & 2) == 0
                y = size - 1 - vert if up else vert
                if not fn[y][x] and i < len(final) * 8:
                    mod[y][x] = (final[i >> 3] >> (7 - (i & 7))) & 1 == 1
                    i += 1
        right -= 2
    # Maske mit den wenigsten Strafpunkten
    best, best_pen = None, None
    for m in range(8):
        cand = [row[:] for row in mod]
        for y in range(size):
            for x in range(size):
                if not fn[y][x] and _mask(m, x, y):
                    cand[y][x] = not cand[y][x]
        mod_backup = [row[:] for row in mod]
        mod[:] = cand
        draw_format(m)
        pen = _penalty(mod)
        if best_pen is None or pen < best_pen:
            best, best_pen = [row[:] for row in mod], pen
        mod[:] = mod_backup
    return best


def _mask(m, x, y) -> bool:
    return (((x + y) % 2 == 0), (y % 2 == 0), (x % 3 == 0), ((x + y) % 3 == 0), ((x // 3 + y // 2) % 2 == 0),
            (x * y % 2 + x * y % 3 == 0), ((x * y % 2 + x * y % 3) % 2 == 0),
            (((x + y) % 2 + x * y % 3) % 2 == 0))[m]


def _penalty(mod) -> int:
    size = len(mod)
    pen = 0
    lines = [row for row in mod] + [[mod[y][x] for y in range(size)] for x in range(size)]
    pat1, pat2 = "10111010000", "00001011101"
    for line in lines:
        run, prev = 0, None
        for v in line:
            if v == prev:
                run += 1
            else:
                if run >= 5:
                    pen += 3 + run - 5
                run, prev = 1, v
        if run >= 5:
            pen += 3 + run - 5
        s = "".join("1" if v else "0" for v in line)
        pen += 40 * (s.count(pat1) + s.count(pat2))
    for y in range(size - 1):
        for x in range(size - 1):
            v = mod[y][x]
            if v == mod[y][x + 1] == mod[y + 1][x] == mod[y + 1][x + 1]:
                pen += 3
    dark = sum(v for row in mod for v in row)
    total = size * size
    k = (abs(dark * 20 - total * 10) + total - 1) // total - 1
    return pen + max(0, k) * 10


# --------------------------------------------------------------------------- Code 128
_C128 = ("212222 222122 222221 121223 121322 131222 122213 122312 132212 221213 221312 231212 112232 122132 "
         "122231 113222 123122 123221 223211 221132 221231 213212 223112 312131 311222 321122 321221 312212 "
         "322112 322211 212123 212321 232121 111323 131123 131321 112313 132113 132311 211313 231113 231311 "
         "112133 112331 132131 113123 113321 133121 313121 211331 231131 213113 213311 213131 311123 311321 "
         "331121 312113 312311 332111 314111 221411 431111 111224 111422 121124 121421 141122 141221 112214 "
         "112412 122114 122411 142112 142211 241211 221114 413111 241112 134111 111242 121142 121241 114212 "
         "124112 124211 411212 421112 421211 212141 214121 412121 111143 111341 131141 114113 114311 411113 "
         "411311 113141 114131 311141 411131 211412 211214 211232 2331112").split()


def code128_widths(text: str) -> list[int]:
    """Strich-/Lückenbreiten (in Modulen, beginnend mit Strich) für Code 128 B inkl. Prüfzeichen und Stopp."""
    if not text:
        raise BarcodeError("Kein Inhalt für den Barcode.")
    vals = []
    for ch in text:
        o = ord(ch)
        if not 32 <= o <= 126:
            raise BarcodeError(f"Zeichen „{ch}“ ist in Code 128 B nicht erlaubt (nur ASCII 32–126).")
        vals.append(o - 32)
    codes = [104] + vals
    check = (104 + sum((i + 1) * v for i, v in enumerate(vals))) % 103
    codes += [check, 106]
    out = []
    for c in codes:
        out += [int(ch) for ch in _C128[c]]
    return out
