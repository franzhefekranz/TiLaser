"""PDF- und AI-Import (Vektorpfade) ohne externe Bibliotheken.

Unterstützt: unkomprimierte und Flate-komprimierte Inhaltsströme, Objektströme (PDF 1.5+),
Formular-XObjects (verschachtelt), Transformationen (cm), Pfade (m l c v y h re), Füll- und
Konturfarben (RGB, Grau, CMYK). Adobe Illustrator ab Version 9 speichert PDF-kompatibel.
Text wird mit der eingebetteten Schrift (TrueType, CFF, OpenType) in Konturen umgewandelt, sonst mit einer
Systemschrift ersetzt. Nicht unterstützt: Type3-Schriften, eingebettete Bilder, verschlüsselte PDFs.
"""
from __future__ import annotations

import re
import zlib

import numpy as np

from . import geometry as g
from .model import ImportedPath

PT = 25.4 / 72.0

_TOKEN = re.compile(rb"""
    (?P<ws>[\s\x00]+)
  | (?P<comment>%[^\r\n]*)
  | (?P<num>[-+]?(?:\d+\.?\d*|\.\d+))
  | (?P<name>/[^\s/\[\]()<>{}%]*)
  | (?P<str>\()
  | (?P<hex><[0-9A-Fa-f\s]*>)
  | (?P<dict><<|>>)
  | (?P<arr>[\[\]])
  | (?P<op>[A-Za-z'"*]+[0-9]?\*?)
  | (?P<other>.)
""", re.X | re.S)


class PdfError(ValueError):
    pass


class PdfStr(bytes):
    """Zeichenkette aus dem Inhaltsstrom (zur Unterscheidung von Namen)."""


def _value(objs, body: bytes, key: bytes) -> bytes | None:
    """Rohwert eines Schlüssels: Array-/Dictionary-Text oder Inhalt des referenzierten Objekts."""
    m = re.search(rb"/" + key + rb"\s*(\[|<<|(\d+)\s+\d+\s+R)", body)
    if not m:
        return None
    if m.group(1) == b"<<":
        return _balanced(body, m.start(1))
    if m.group(1) == b"[":
        depth, i = 0, m.start(1)
        while i < len(body):
            if body[i:i + 1] == b"[":
                depth += 1
            elif body[i:i + 1] == b"]":
                depth -= 1
                if depth == 0:
                    return body[m.start(1):i + 1]
            i += 1
        return body[m.start(1):]
    return objs.get(int(m.group(2)), b"")


# --------------------------------------------------------------------------- Objekte
def _split_stream(body: bytes):
    k = body.find(b"stream")
    if k < 0:
        return body, None
    head = body[:k]
    start = k + 6
    if body[start:start + 2] == b"\r\n":
        start += 2
    elif body[start:start + 1] in (b"\n", b"\r"):
        start += 1
    m = re.search(rb"/Length\s+(\d+)\b(?!\s+\d+\s+R)", head)
    if m:
        data = body[start:start + int(m.group(1))]
    else:
        end = body.rfind(b"endstream")
        data = body[start:end].rstrip(b"\r\n")
    return head, data


def _decode(head: bytes, data: bytes) -> bytes | None:
    filters = re.findall(rb"/(\w+Decode|Fl|AHx|A85)\b", head.split(b"/DecodeParms")[0])
    for f in filters:
        if f in (b"FlateDecode", b"Fl"):
            try:
                data = zlib.decompress(data)
            except zlib.error:
                d = zlib.decompressobj()
                data = d.decompress(data)
        elif f in (b"ASCIIHexDecode", b"AHx"):
            data = bytes.fromhex(re.sub(rb"[^0-9A-Fa-f]", b"", data.split(b">")[0]).decode())
        else:
            return None                      # DCT, LZW, A85 … nicht für Inhaltsströme benötigt
    return data


def _read_objects(raw: bytes) -> dict[int, bytes]:
    objs: dict[int, bytes] = {}
    pos = 0
    pat = re.compile(rb"(\d+)\s+(\d+)\s+obj\b")
    while True:
        m = pat.search(raw, pos)
        if not m:
            break
        num = int(m.group(1))
        start = m.end()
        s = raw.find(b"stream", start)
        e = raw.find(b"endobj", start)
        if e < 0:
            break
        if 0 <= s < e:                               # Stream: bis endstream überspringen
            es = raw.find(b"endstream", s)
            e = raw.find(b"endobj", es if es > 0 else s)
        objs[num] = raw[start:e]
        pos = e + 6
    # Objektströme (komprimierte Objekte)
    for num, body in list(objs.items()):
        if re.search(rb"/Type\s*/ObjStm", body):
            head, data = _split_stream(body)
            data = _decode(head, data or b"")
            if not data:
                continue
            n = int(re.search(rb"/N\s+(\d+)", head).group(1))
            first = int(re.search(rb"/First\s+(\d+)", head).group(1))
            nums = [int(x) for x in data[:first].split()]
            pairs = list(zip(nums[0::2], nums[1::2]))[:n]
            for k, (onum, off) in enumerate(pairs):
                end = first + pairs[k + 1][1] if k + 1 < len(pairs) else len(data)
                objs.setdefault(onum, data[first + off:end])
    return objs


def _ref(body: bytes, key: bytes):
    m = re.search(rb"/" + key + rb"\s+(\d+)\s+\d+\s+R", body)
    return int(m.group(1)) if m else None


def _balanced(body: bytes, start: int) -> bytes:
    """Text eines Dictionaries ab '<<' bis zum passenden '>>'."""
    depth, i = 0, start
    while i < len(body) - 1:
        two = body[i:i + 2]
        if two == b"<<":
            depth += 1
            i += 2
            continue
        if two == b">>":
            depth -= 1
            i += 2
            if depth == 0:
                return body[start:i]
            continue
        i += 1
    return body[start:]


def _dict_entry(objs, body: bytes, key: bytes) -> bytes | None:
    """Wert eines Schlüssels als Dictionary-Text (direkt oder über Referenz)."""
    m = re.search(rb"/" + key + rb"\s*(<<|\d+\s+\d+\s+R)", body)
    if not m:
        return None
    if m.group(1) == b"<<":
        return _balanced(body, m.start(1))
    num = int(m.group(1).split()[0])
    return objs.get(num, b"")


def _pages(objs) -> list[int]:
    root = None
    for num, body in objs.items():
        if re.search(rb"/Type\s*/Catalog", body):
            root = _ref(body, b"Pages")
            break
    out = []

    def walk(n, depth=0):
        body = objs.get(n, b"")
        if depth > 50:
            return
        if re.search(rb"/Type\s*/Pages", body):
            m = re.search(rb"/Kids\s*\[([^\]]*)\]", body)
            for k in re.findall(rb"(\d+)\s+\d+\s+R", m.group(1) if m else b""):
                walk(int(k), depth + 1)
        elif re.search(rb"/Type\s*/Page\b", body):
            out.append(n)

    if root is not None:
        walk(root)
    if not out:                                   # Fallback: alle Seitenobjekte
        out = [n for n, b in sorted(objs.items()) if re.search(rb"/Type\s*/Page\b", b)]
    return out


def _inherited(objs, page_body: bytes, key: bytes):
    body, depth = page_body, 0
    while body is not None and depth < 30:
        if re.search(rb"/" + key + rb"\b", body):
            return body
        parent = _ref(body, b"Parent")
        body = objs.get(parent) if parent is not None else None
        depth += 1
    return None


# --------------------------------------------------------------------------- Inhalt
def _tokens(data: bytes):
    i, n = 0, len(data)
    while i < n:
        m = _TOKEN.match(data, i)
        if not m:
            i += 1
            continue
        kind = m.lastgroup
        if kind == "str":                         # (Text) mit Escapes und Klammer-Verschachtelung
            depth, j = 1, m.end()
            buf = bytearray()
            while j < n and depth:
                c = data[j]
                if c == 0x5C:                     # Backslash
                    nx = data[j + 1:j + 2]
                    esc = {b"n": 10, b"r": 13, b"t": 9, b"b": 8, b"f": 12, b"(": 40, b")": 41, b"\\": 92}
                    if nx in esc:
                        buf.append(esc[nx])
                        j += 2
                    elif nx.isdigit():
                        oct_ = re.match(rb"[0-7]{1,3}", data[j + 1:j + 4]).group(0)
                        buf.append(int(oct_, 8) & 0xFF)
                        j += 1 + len(oct_)
                    elif nx in (b"\r", b"\n"):  # Zeilenfortsetzung
                        j += 2
                        if nx == b"\r" and data[j:j + 1] == b"\n":
                            j += 1
                    else:
                        j += 1
                    continue
                if c == 40:
                    depth += 1
                elif c == 41:
                    depth -= 1
                    if depth == 0:
                        j += 1
                        break
                buf.append(c)
                j += 1
            yield "str", PdfStr(bytes(buf))
            i = j
            continue
        i = m.end()
        if kind in ("ws", "comment"):
            continue
        val = m.group(kind)
        if kind == "num":
            yield "num", float(val)
        elif kind == "hex":
            h = re.sub(rb"[^0-9A-Fa-f]", b"", val[1:-1])
            if len(h) % 2:
                h += b"0"
            yield "str", PdfStr(bytes.fromhex(h.decode()))
        elif kind == "op" and val == b"BI":       # Inline-Bild bis EI überspringen
            k = data.find(b"EI", i)
            i = k + 2 if k >= 0 else n
        else:
            yield kind, val


def _cmyk(c, m, y, k):
    return "#%02x%02x%02x" % tuple(int(round(255 * (1 - min(1, v + k)))) for v in (c, m, y))


def _color(nums):
    if len(nums) >= 4:
        return _cmyk(*nums[-4:])
    if len(nums) == 3:
        return "#%02x%02x%02x" % tuple(int(round(255 * max(0, min(1, v)))) for v in nums)
    if len(nums) >= 1:
        v = int(round(255 * max(0, min(1, nums[-1]))))
        return "#%02x%02x%02x" % (v, v, v)
    return "#000000"


class _Interp:
    def __init__(self, objs, base_m):
        self.objs = objs
        self.out: list[ImportedPath] = []
        self.warn: set[str] = set()
        self.base = base_m
        self.fonts: dict[int, object] = {}

    def _stream(self, num: int):
        head, data = _split_stream(self.objs.get(num, b""))
        return _decode(head, data or b"")

    def _font(self, resources, name: bytes):
        from .pdf_fonts import PdfFont
        if resources is None:
            return None
        fd = _dict_entry(self.objs, resources, b"Font")
        if not fd:
            return None
        m = re.search(rb"/" + re.escape(name) + rb"\s+(\d+)\s+\d+\s+R", fd)
        if not m:
            return None
        num = int(m.group(1))
        if num not in self.fonts:
            body = self.objs.get(num, b"")
            if re.search(rb"/Subtype\s*/Type3", body):
                self.warn.add("Type3-Schriften in der PDF werden nicht unterstützt.")
                self.fonts[num] = None
            else:
                try:
                    f = PdfFont(self.objs, body, lambda b, k, raw=False: _value(self.objs, b, k), self._stream)
                    self.warn.update(f.warnings)
                    self.fonts[num] = f
                except Exception as e:
                    self.warn.add(f"Schrift konnte nicht gelesen werden: {e}")
                    self.fonts[num] = None
        return self.fonts[num]

    def run(self, data: bytes, resources: bytes | None, ctm: np.ndarray, depth=0):
        if depth > 12:
            return
        stack = []
        state = {"ctm": ctm, "stroke": "#000000", "fill": "#000000"}
        operands: list = []
        bb = g.BezBuilder()
        cur = start = None

        def T(x, y):
            p = state["ctm"] @ np.array([x, y, 1.0])
            return self.base @ p

        def emit(stroke: bool, fill: bool):
            bb.finish(False)
            for p in bb.out:
                q = p
                if fill and not stroke:
                    if not q.closed and len(q.pts) > 2:
                        q = g.Polyline.from_bez(g.BezPath(q.bez.anchors, q.bez.hin, q.bez.hout, True))
                    self.out.append(ImportedPath(q, state["fill"], state["fill"], True))
                elif stroke:
                    self.out.append(ImportedPath(q, state["stroke"], state["stroke"], False))
            bb.out = []

        ts = {"font": None, "size": 12.0, "Tc": 0.0, "Tw": 0.0, "Th": 1.0, "TL": 0.0, "Ts": 0.0, "Tr": 0,
              "Tm": np.eye(3), "Tlm": np.eye(3)}
        arr_stack: list[list] = []

        def show(items):
            f = ts["font"]
            if f is None:
                return
            for it in items:
                if isinstance(it, float):
                    ts["Tm"] = ts["Tm"] @ g.translate(-it / 1000.0 * ts["size"] * ts["Th"], 0)
                    continue
                for code in f.codes(it):
                    polys, _adv = f.glyph(code)
                    if polys and ts["Tr"] not in (3, 7):
                        trm = (state["ctm"] @ ts["Tm"] @
                               np.array([[ts["size"] * ts["Th"], 0, 0], [0, ts["size"], ts["Ts"]], [0, 0, 1.0]]) @
                               g.scale(1.0 / f.upm))
                        M = self.base @ trm
                        stroke_only = ts["Tr"] in (1, 5)
                        col = state["stroke"] if stroke_only else state["fill"]
                        for q in polys:
                            self.out.append(ImportedPath(q.transformed(M), col, col, not stroke_only))
                    w0 = f.width(code) / 1000.0
                    tx = (w0 * ts["size"] + ts["Tc"] + (ts["Tw"] if (code == 32 and not f.two_byte) else 0.0)) * ts["Th"]
                    ts["Tm"] = ts["Tm"] @ g.translate(tx, 0)

        for kind, val in _tokens(data):
            if kind == "arr":
                if val == b"[":
                    arr_stack.append([])
                elif arr_stack:
                    lst = arr_stack.pop()
                    (arr_stack[-1] if arr_stack else operands).append(lst)
                continue
            if kind != "op":
                target = arr_stack[-1] if arr_stack else operands
                if kind in ("num", "name", "str"):
                    target.append(val)
                continue
            op = val.decode("latin-1")
            nums = [v for v in operands if isinstance(v, float)]
            strs = [v for v in operands if isinstance(v, PdfStr)]
            if op == "BT":
                ts["Tm"], ts["Tlm"] = np.eye(3), np.eye(3)
            elif op == "ET":
                pass
            elif op == "Tf" and operands:
                name = next((v for v in operands if isinstance(v, bytes) and not isinstance(v, PdfStr)), None)
                if name:
                    ts["font"] = self._font(resources, name[1:])
                if nums:
                    ts["size"] = nums[-1]
            elif op == "Tc" and nums:
                ts["Tc"] = nums[-1]
            elif op == "Tw" and nums:
                ts["Tw"] = nums[-1]
            elif op == "Tz" and nums:
                ts["Th"] = nums[-1] / 100.0
            elif op == "TL" and nums:
                ts["TL"] = nums[-1]
            elif op == "Ts" and nums:
                ts["Ts"] = nums[-1]
            elif op == "Tr" and nums:
                ts["Tr"] = int(nums[-1])
            elif op in ("Td", "TD") and len(nums) >= 2:
                tx, ty = nums[-2:]
                if op == "TD":
                    ts["TL"] = -ty
                ts["Tlm"] = ts["Tlm"] @ g.translate(tx, ty)
                ts["Tm"] = ts["Tlm"].copy()
            elif op == "Tm" and len(nums) >= 6:
                a, b, c, d, e, f_ = nums[-6:]
                ts["Tlm"] = np.array([[a, c, e], [b, d, f_], [0, 0, 1.0]])
                ts["Tm"] = ts["Tlm"].copy()
            elif op == "T*":
                ts["Tlm"] = ts["Tlm"] @ g.translate(0, -ts["TL"])
                ts["Tm"] = ts["Tlm"].copy()
            elif op == "Tj" and strs:
                show([strs[-1]])
            elif op == "TJ" and operands and isinstance(operands[-1], list):
                show([v for v in operands[-1] if isinstance(v, (float, PdfStr))])
            elif op in ("'", '"') and strs:
                if op == '"' and len(nums) >= 2:
                    ts["Tw"], ts["Tc"] = nums[-2], nums[-1]
                ts["Tlm"] = ts["Tlm"] @ g.translate(0, -ts["TL"])
                ts["Tm"] = ts["Tlm"].copy()
                show([strs[-1]])
            elif op == "q":
                stack.append(dict(state))
            elif op == "Q":
                if stack:
                    state = stack.pop()
            elif op == "cm" and len(nums) >= 6:
                a, b, c, d, e, f = nums[-6:]
                state["ctm"] = state["ctm"] @ np.array([[a, c, e], [b, d, f], [0, 0, 1.0]])
            elif op == "m" and len(nums) >= 2:
                x, y = nums[-2:]
                bb.move(T(x, y)[:2])
                cur = start = (x, y)
            elif op == "l" and len(nums) >= 2:
                x, y = nums[-2:]
                bb.line(T(x, y)[:2])
                cur = (x, y)
            elif op == "c" and len(nums) >= 6:
                x1, y1, x2, y2, x3, y3 = nums[-6:]
                bb.cubic(T(x1, y1)[:2], T(x2, y2)[:2], T(x3, y3)[:2])
                cur = (x3, y3)
            elif op == "v" and len(nums) >= 4 and cur:
                x2, y2, x3, y3 = nums[-4:]
                bb.cubic(T(*cur)[:2], T(x2, y2)[:2], T(x3, y3)[:2])
                cur = (x3, y3)
            elif op == "y" and len(nums) >= 4:
                x1, y1, x3, y3 = nums[-4:]
                bb.cubic(T(x1, y1)[:2], T(x3, y3)[:2], T(x3, y3)[:2])
                cur = (x3, y3)
            elif op == "h":
                if bb.current is not None and start is not None:
                    if not np.allclose(bb.current, T(*start)[:2]):
                        bb.line(T(*start)[:2])
                    bb.finish(True)
                    cur = start
            elif op == "re" and len(nums) >= 4:
                x, y, w, h = nums[-4:]
                bb.move(T(x, y)[:2])
                bb.line(T(x + w, y)[:2])
                bb.line(T(x + w, y + h)[:2])
                bb.line(T(x, y + h)[:2])
                bb.line(T(x, y)[:2])
                bb.finish(True)
                cur = start = (x, y)
            elif op in ("S", "s"):
                if op == "s":
                    bb.finish(True)
                emit(True, False)
            elif op in ("f", "F", "f*"):
                emit(False, True)
            elif op in ("B", "B*", "b", "b*"):
                if op in ("b", "b*"):
                    bb.finish(True)
                emit(True, False)
            elif op == "n":
                bb.finish(False)
                bb.out = []
            elif op == "RG" and len(nums) >= 3:
                state["stroke"] = _color(nums[-3:])
            elif op == "rg" and len(nums) >= 3:
                state["fill"] = _color(nums[-3:])
            elif op == "G" and nums:
                state["stroke"] = _color(nums[-1:])
            elif op == "g" and nums:
                state["fill"] = _color(nums[-1:])
            elif op == "K" and len(nums) >= 4:
                state["stroke"] = _color(nums[-4:])
            elif op == "k" and len(nums) >= 4:
                state["fill"] = _color(nums[-4:])
            elif op in ("SC", "SCN") and nums:
                state["stroke"] = _color(nums[-4:] if len(nums) >= 4 else nums)
            elif op in ("sc", "scn") and nums:
                state["fill"] = _color(nums[-4:] if len(nums) >= 4 else nums)
            elif op == "Do" and operands and isinstance(operands[-1], bytes) and not isinstance(operands[-1], PdfStr):
                self._do(operands[-1][1:], resources, state["ctm"], depth)
            operands = []

    def _do(self, name: bytes, resources, ctm, depth):
        if resources is None:
            return
        xd = _dict_entry(self.objs, resources, b"XObject")
        if not xd:
            return
        m = re.search(rb"/" + re.escape(name) + rb"\s+(\d+)\s+\d+\s+R", xd)
        if not m:
            return
        body = self.objs.get(int(m.group(1)), b"")
        head, data = _split_stream(body)
        if not re.search(rb"/Subtype\s*/Form", head):
            if re.search(rb"/Subtype\s*/Image", head):
                self.warn.add("Eingebettete Bilder in der PDF wurden ignoriert (separat als Bild importieren).")
            return
        data = _decode(head, data or b"")
        if data is None:
            return
        mm = re.search(rb"/Matrix\s*\[([^\]]*)\]", head)
        M = np.eye(3)
        if mm:
            a, b, c, d, e, f = [float(x) for x in mm.group(1).split()[:6]]
            M = np.array([[a, c, e], [b, d, f], [0, 0, 1.0]])
        res = _dict_entry(self.objs, head, b"Resources") or resources
        self.run(data, res, ctm @ M, depth + 1)


def import_pdf(path: str, page: int = 0) -> tuple[list[ImportedPath], list[str]]:
    with open(path, "rb") as f:
        raw = f.read()
    k = raw.find(b"%PDF")
    if k < 0:
        raise PdfError("Keine PDF-Datei. Ältere Illustrator-Dateien (vor Version 9) bitte als SVG oder PDF speichern.")
    if re.search(rb"/Encrypt\s", raw):
        raise PdfError("Verschlüsselte PDF-Dateien werden nicht unterstützt.")
    objs = _read_objects(raw[k:])
    pages = _pages(objs)
    if not pages:
        raise PdfError("Keine Seite in der PDF gefunden.")
    warnings = []
    if len(pages) > 1:
        warnings.append(f"PDF hat {len(pages)} Seiten – Seite {page + 1} wird importiert.")
    pbody = objs[pages[min(page, len(pages) - 1)]]
    mb_body = _inherited(objs, pbody, b"MediaBox") or pbody
    mb = re.search(rb"/MediaBox\s*\[([^\]]*)\]", mb_body)
    x0, y0 = (0.0, 0.0)
    if mb:
        vals = [float(v) for v in mb.group(1).split()[:4]]
        x0, y0 = vals[0], vals[1]
    rot_m = re.search(rb"/Rotate\s+(-?\d+)", _inherited(objs, pbody, b"Rotate") or b"")
    base = g.scale(PT) @ g.translate(-x0, -y0)
    if rot_m and int(rot_m.group(1)) % 360:
        warnings.append("Seitendrehung (/Rotate) wird nicht berücksichtigt.")
    res_body = _inherited(objs, pbody, b"Resources")
    resources = _dict_entry(objs, res_body, b"Resources") if res_body else None
    # Inhaltsströme
    contents = []
    m = re.search(rb"/Contents\s*\[([^\]]*)\]", pbody)
    if m:
        contents = [int(x) for x in re.findall(rb"(\d+)\s+\d+\s+R", m.group(1))]
    else:
        r = _ref(pbody, b"Contents")
        if r is not None:
            body = objs.get(r, b"")
            if body.strip().startswith(b"["):           # Referenz auf ein Array
                contents = [int(x) for x in re.findall(rb"(\d+)\s+\d+\s+R", body)]
            else:
                contents = [r]
    data = b""
    for c in contents:
        head, s = _split_stream(objs.get(c, b""))
        dec = _decode(head, s or b"")
        if dec is None:
            warnings.append("Ein Inhaltsstrom verwendet ein nicht unterstütztes Kompressionsverfahren.")
            continue
        data += dec + b"\n"
    it = _Interp(objs, base)
    it.run(data, resources, np.eye(3))
    warnings += sorted(it.warn)
    return it.out, warnings
