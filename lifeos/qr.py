"""A small QR code maker (byte mode, error correction level M, versions 1 to 10) drawn as SVG.

Used for "Sign in a phone" on the menu bar's household key page: the phone's camera reads a one-time sign-in link.
Written here rather than added as a package so an update file can carry it (updates can't add packages).
"""
from __future__ import annotations

# version: (data codewords per block groups [(blocks, data_cw)], ec codewords per block), level M
_BLOCKS = {
    1: ([(1, 16)], 10), 2: ([(1, 28)], 16), 3: ([(1, 44)], 26), 4: ([(2, 32)], 18), 5: ([(2, 43)], 24),
    6: ([(4, 27)], 16), 7: ([(4, 31)], 18), 8: ([(2, 38), (2, 39)], 22), 9: ([(3, 36), (2, 37)], 22),
    10: ([(4, 43), (1, 44)], 26),
}
_ALIGN = {1: [], 2: [6, 18], 3: [6, 22], 4: [6, 26], 5: [6, 30], 6: [6, 34], 7: [6, 22, 38], 8: [6, 24, 42],
          9: [6, 26, 46], 10: [6, 28, 50]}

# GF(256) with the QR polynomial x^8 + x^4 + x^3 + x^2 + 1
_EXP = [0] * 512
_LOG = [0] * 256
_x = 1
for _i in range(255):
    _EXP[_i] = _x
    _LOG[_x] = _i
    _x <<= 1
    if _x & 0x100:
        _x ^= 0x11D
for _i in range(255, 512):
    _EXP[_i] = _EXP[_i - 255]


def _mul(a: int, b: int) -> int:
    return 0 if a == 0 or b == 0 else _EXP[_LOG[a] + _LOG[b]]


def _generator(n: int) -> list[int]:
    g = [1]
    for i in range(n):
        g = [a ^ b for a, b in zip(g + [0], [0] + [_mul(c, _EXP[i]) for c in g], strict=True)]
    return g


def _ec(data: list[int], n: int) -> list[int]:
    gen = _generator(n)
    rem = list(data) + [0] * n
    for i in range(len(data)):
        c = rem[i]
        if c:
            for j in range(1, len(gen)):
                rem[i + j] ^= _mul(gen[j], c)
    return rem[len(data):]


def _capacity(version: int) -> int:
    groups, _ = _BLOCKS[version]
    return sum(b * d for b, d in groups)


def _codewords(data: bytes, version: int) -> list[int]:
    count_bits = 8 if version < 10 else 16
    bits = "0100" + format(len(data), f"0{count_bits}b") + "".join(format(b, "08b") for b in data)
    cap = _capacity(version) * 8
    bits += "0" * min(4, cap - len(bits))
    bits += "0" * (-len(bits) % 8)
    words = [int(bits[i:i + 8], 2) for i in range(0, len(bits), 8)]
    pads = 0
    while len(words) < _capacity(version):
        words.append((0xEC, 0x11)[pads % 2])
        pads += 1
    groups, ec_n = _BLOCKS[version]
    blocks, pos = [], 0
    for count, size in groups:
        for _ in range(count):
            blocks.append(words[pos:pos + size])
            pos += size
    ecs = [_ec(b, ec_n) for b in blocks]
    out = []
    for i in range(max(len(b) for b in blocks)):
        out += [b[i] for b in blocks if i < len(b)]
    for i in range(ec_n):
        out += [e[i] for e in ecs]
    return out


def _bch(value: int, poly: int, bits: int) -> int:
    v = value << bits
    top = poly.bit_length()
    while v.bit_length() >= top:
        v ^= poly << (v.bit_length() - top)
    return (value << bits) | v


class _Matrix:
    def __init__(self, version: int):
        self.v, self.n = version, 17 + 4 * version
        self.m = [[None] * self.n for _ in range(self.n)]  # None = not set; True/False = dark/light
        self.fixed = [[False] * self.n for _ in range(self.n)]

    def set(self, r: int, c: int, dark: bool, fixed: bool = True) -> None:
        self.m[r][c] = dark
        if fixed:
            self.fixed[r][c] = True

    def finder(self, r0: int, c0: int) -> None:
        for r in range(-1, 8):
            for c in range(-1, 8):
                rr, cc = r0 + r, c0 + c
                if 0 <= rr < self.n and 0 <= cc < self.n:
                    dark = 0 <= r <= 6 and 0 <= c <= 6 and (r in (0, 6) or c in (0, 6) or (2 <= r <= 4 and 2 <= c <= 4))
                    self.set(rr, cc, dark)

    def build(self, data: list[int], mask: int) -> list[list[bool]]:
        n = self.n
        self.finder(0, 0)
        self.finder(0, n - 7)
        self.finder(n - 7, 0)
        for i in range(8, n - 8):
            self.set(6, i, i % 2 == 0)
            self.set(i, 6, i % 2 == 0)
        pos = _ALIGN[self.v]
        last = len(pos) - 1
        for i, r in enumerate(pos):
            for j, c in enumerate(pos):
                if (i, j) in ((0, 0), (0, last), (last, 0)):  # those three sit on the finders
                    continue
                for dr in range(-2, 3):
                    for dc in range(-2, 3):
                        self.set(r + dr, c + dc, max(abs(dr), abs(dc)) != 1)
        self.set(n - 8, 8, True)  # the dark module
        for i in range(9):  # reserve the format areas
            if not self.fixed[8][i]:
                self.set(8, i, False)
            if not self.fixed[i][8]:
                self.set(i, 8, False)
        for i in range(8):
            self.set(8, n - 1 - i, False)
            self.set(n - 1 - i, 8, False)
        if self.v >= 7:
            vbits = _bch(self.v, 0x1F25, 12)
            for i in range(18):
                dark = bool((vbits >> i) & 1)
                self.set(i // 3, n - 11 + i % 3, dark)
                self.set(n - 11 + i % 3, i // 3, dark)
        # data, in the zigzag
        bits = "".join(format(w, "08b") for w in data)
        idx, up = 0, True
        c = n - 1
        while c > 0:
            if c == 6:
                c -= 1
            rows = range(n - 1, -1, -1) if up else range(n)
            for r in rows:
                for cc in (c, c - 1):
                    if self.fixed[r][cc]:
                        continue
                    dark = idx < len(bits) and bits[idx] == "1"
                    idx += 1
                    if _MASKS[mask](r, cc):
                        dark = not dark
                    self.m[r][cc] = dark
            up = not up
            c -= 2
        # format information (level M = 00)
        fbits = _bch((0b00 << 3) | mask, 0x537, 10) ^ 0x5412
        for i in range(15):
            dark = bool((fbits >> i) & 1)
            # around the top-left finder
            if i < 6:
                self.m[i][8] = dark
            elif i < 8:
                self.m[i + 1][8] = dark
            else:
                self.m[n - 15 + i][8] = dark
            # along the top and bottom-left
            if i < 8:
                self.m[8][n - 1 - i] = dark
            elif i < 9:
                self.m[8][15 - i] = dark
            else:
                self.m[8][14 - i] = dark
        self.m[n - 8][8] = True
        return [[bool(x) for x in row] for row in self.m]


_MASKS = [
    lambda r, c: (r + c) % 2 == 0,
    lambda r, c: r % 2 == 0,
    lambda r, c: c % 3 == 0,
    lambda r, c: (r + c) % 3 == 0,
    lambda r, c: (r // 2 + c // 3) % 2 == 0,
    lambda r, c: (r * c) % 2 + (r * c) % 3 == 0,
    lambda r, c: ((r * c) % 2 + (r * c) % 3) % 2 == 0,
    lambda r, c: ((r + c) % 2 + (r * c) % 3) % 2 == 0,
]


def _penalty(m: list[list[bool]]) -> int:
    n, score = len(m), 0
    for grid in (m, [list(col) for col in zip(*m, strict=True)]):
        for row in grid:
            run, last = 0, None
            for x in row:
                if x == last:
                    run += 1
                else:
                    if run >= 5:
                        score += run - 2
                    run, last = 1, x
            if run >= 5:
                score += run - 2
            s = "".join("1" if x else "0" for x in row)
            score += 40 * (s.count("10111010000") + s.count("00001011101"))
    for r in range(n - 1):
        for c in range(n - 1):
            if m[r][c] == m[r][c + 1] == m[r + 1][c] == m[r + 1][c + 1]:
                score += 3
    dark = sum(sum(row) for row in m)
    score += 10 * (abs(dark * 100 // (n * n) - 50) // 5)
    return score


def matrix(text: str) -> list[list[bool]]:
    data = text.encode("utf-8")
    for version in range(1, 11):
        header = 4 + (8 if version < 10 else 16)
        if header + 8 * len(data) <= _capacity(version) * 8:
            break
    else:
        raise ValueError("That's too long for a QR code here.")
    words = _codewords(data, version)
    best = min(range(8), key=lambda k: _penalty(_Matrix(version).build(words, k)))
    return _Matrix(version).build(words, best)


def svg(text: str, size: int = 280, label: str = "") -> str:
    """The QR code as an <svg> with a 4-module quiet zone, dark on white whatever the page's colours."""
    import html
    label = html.escape(label or "", quote=True)
    m = matrix(text)
    n = len(m) + 8
    path = "".join(f"M{c + 4},{r + 4}h1v1h-1z" for r, row in enumerate(m) for c, dark in enumerate(row) if dark)
    title = f"<title>{label}</title>" if label else ""
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {n} {n}" width="{size}" height="{size}" '
            f'role="img" shape-rendering="crispEdges"{" aria-label=" + chr(34) + label + chr(34) if label else ""}>'
            f'{title}<rect width="{n}" height="{n}" fill="#fff"/><path d="{path}" fill="#000"/></svg>')
