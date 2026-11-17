"""Schriften aus PDF-Dateien: eingebettete TrueType-/CFF-/OpenType-Schriften lesen und
Glyphen als Bézier-Konturen liefern. Ohne eingebettete Schrift wird eine Systemschrift ersetzt."""
from __future__ import annotations

import io
import re

import numpy as np

from . import geometry as g

# Unicode-Werte für die häufigsten Glyphennamen (Ergänzung zu fontTools.agl)
_STD_NAMES_EXTRA = {"space": " ", "hyphen": "-", "period": ".", "comma": ","}


def _agl(name: str) -> str:
    try:
        from fontTools import agl
        u = agl.toUnicode(name)
        if u:
            return u
    except Exception:
        pass
    return _STD_NAMES_EXTRA.get(name, "")


def parse_tounicode(data: bytes) -> dict[int, str]:
    """ToUnicode-CMap: Code -> Unicode-Text."""
    out: dict[int, str] = {}

    def u16(h: bytes) -> str:
        b = bytes.fromhex(h.decode())
        try:
            return b.decode("utf-16-be")
        except UnicodeDecodeError:
            return ""

    for block in re.findall(rb"beginbfchar(.*?)endbfchar", data, re.S):
        for src, dst in re.findall(rb"<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]*)>", block):
            out[int(src, 16)] = u16(dst)
    for block in re.findall(rb"beginbfrange(.*?)endbfrange", data, re.S):
        for lo, hi, rest in re.findall(rb"<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>\s*(\[[^\]]*\]|<[0-9A-Fa-f]+>)", block):
            lo_i, hi_i = int(lo, 16), int(hi, 16)
            if rest.startswith(b"["):
                for k, d in enumerate(re.findall(rb"<([0-9A-Fa-f]*)>", rest)):
                    out[lo_i + k] = u16(d)
            else:
                base = u16(rest[1:-1])
                if base:
                    for k in range(min(hi_i - lo_i + 1, 65536)):
                        out[lo_i + k] = base[:-1] + chr(ord(base[-1]) + k)
    return out


class _Pen:
    """fontTools-Pen, der Konturen als Bézierpfade sammelt."""

    def __new__(cls, glyphset=None):
        from fontTools.pens.basePen import BasePen

        class P(BasePen):
            def __init__(self, gs):
                super().__init__(gs)
                self.bb = g.BezBuilder(5.0)

            def _moveTo(self, p):
                self.bb.move(p)

            def _lineTo(self, p):
                self.bb.line(p)

            def _curveToOne(self, p1, p2, p3):
                self.bb.cubic(p1, p2, p3)

            def _qCurveToOne(self, p1, p2):
                self.bb.quad(p1, p2)

            def _closePath(self):
                if self.bb.a and not np.allclose(self.bb.a[0], self.bb.current):
                    self.bb.line(self.bb.a[0])
                self.bb.finish(True)

            def _endPath(self):
                self.bb.finish(True)

        return P(glyphset)


class PdfFont:
    """Eine Schrift aus dem /Font-Dictionary einer PDF-Seite."""

    def __init__(self, objs, body: bytes, resolve, decode_stream):
        self.objs = objs
        self.body = body
        self.two_byte = False
        self.widths: dict[int, float] = {}
        self.default_width = 1000.0
        self.to_unicode: dict[int, str] = {}
        self.encoding: dict[int, str] = {}         # Code -> Glyphenname (Differences)
        self.base_codec = "cp1252"
        self.cid_to_gid: np.ndarray | None = None
        self.tt = None            # fontTools TTFont
        self.cff = None           # (CharStrings, Namensliste, CID-keyed?)
        self.upm = 1000.0
        self.fallback = None      # (TTFont, glyphset, cmap) einer Systemschrift
        self.cache: dict[int, tuple[list, float]] = {}
        self.warnings: list[str] = []
        self._load(resolve, decode_stream)

    # ------------------------------------------------------------ Laden
    def _load(self, resolve, decode_stream):
        body = self.body
        sub = re.search(rb"/Subtype\s*/(\w+)", body)
        subtype = sub.group(1) if sub else b"Type1"
        desc_body = body
        if subtype == b"Type0":
            self.two_byte = re.search(rb"/Encoding\s*/Identity-[HV]", body) is not None
            if not self.two_byte:
                self.warnings.append("PDF-Schrift mit CMap-Kodierung wird vereinfacht behandelt.")
                self.two_byte = True
            m = re.search(rb"/DescendantFonts\s*(\[\s*(\d+)\s+\d+\s+R\s*\]|(\d+)\s+\d+\s+R)", body)
            if m:
                ref = int(m.group(2) or m.group(3))
                d = self.objs.get(ref, b"")
                if d.strip().startswith(b"["):
                    ref = int(re.search(rb"(\d+)\s+\d+\s+R", d).group(1))
                    d = self.objs.get(ref, b"")
                desc_body = d
                dw = re.search(rb"/DW\s+([-\d.]+)", d)
                if dw:
                    self.default_width = float(dw.group(1))
                self._parse_w(resolve(d, b"W", raw=True) or b"")
                cg = re.search(rb"/CIDToGIDMap\s+(\d+)\s+\d+\s+R", d)
                if cg:
                    data = decode_stream(int(cg.group(1)))
                    if data:
                        self.cid_to_gid = np.frombuffer(data[: len(data) // 2 * 2], ">u2").astype(int)
        else:
            fc = re.search(rb"/FirstChar\s+(\d+)", body)
            first = int(fc.group(1)) if fc else 0
            wtxt = resolve(body, b"Widths", raw=True) or b""
            for k, v in enumerate(re.findall(rb"[-\d.]+", wtxt)):
                self.widths[first + k] = float(v)
            enc = re.search(rb"/Encoding\s*/(\w+)", body)
            if enc:
                self.base_codec = {b"MacRomanEncoding": "mac_roman", b"WinAnsiEncoding": "cp1252"}.get(
                    enc.group(1), "latin-1")
            encd = resolve(body, b"Encoding")
            if encd:
                be = re.search(rb"/BaseEncoding\s*/(\w+)", encd)
                if be:
                    self.base_codec = {b"MacRomanEncoding": "mac_roman"}.get(be.group(1), "cp1252")
                dm = re.search(rb"/Differences\s*\[([^\]]*)\]", encd)
                if dm:
                    code = 0
                    for tok in re.findall(rb"/[^\s/\[\]]+|\d+", dm.group(1)):
                        if tok.startswith(b"/"):
                            self.encoding[code] = tok[1:].decode("latin-1")
                            code += 1
                        else:
                            code = int(tok)
        tu = re.search(rb"/ToUnicode\s+(\d+)\s+\d+\s+R", body)
        if tu:
            data = decode_stream(int(tu.group(1)))
            if data:
                self.to_unicode = parse_tounicode(data)
        # Eingebettete Schrift
        fd = re.search(rb"/FontDescriptor\s+(\d+)\s+\d+\s+R", desc_body)
        fdb = self.objs.get(int(fd.group(1)), b"") if fd else b""
        for key in (b"FontFile2", b"FontFile3", b"FontFile"):
            m = re.search(rb"/" + key + rb"\s+(\d+)\s+\d+\s+R", fdb)
            if not m:
                continue
            data = decode_stream(int(m.group(1)))
            if not data:
                continue
            try:
                self._load_program(key, data, self.objs.get(int(m.group(1)), b""))
            except Exception as e:      # defekte Untermengen-Schrift -> Ersatzschrift
                self.warnings.append(f"Eingebettete Schrift nicht lesbar ({e.__class__.__name__}), Ersatzschrift verwendet.")
            break
        if self.tt is None and self.cff is None:
            self._load_fallback()

    def _parse_w(self, w: bytes):
        toks = re.findall(rb"\[|\]|[-\d.]+", w)
        i = 0
        while i < len(toks):
            if toks[i] in (b"[", b"]"):
                i += 1
                continue
            c = int(float(toks[i]))
            if i + 1 < len(toks) and toks[i + 1] == b"[":
                i += 2
                k = 0
                while i < len(toks) and toks[i] != b"]":
                    self.widths[c + k] = float(toks[i])
                    k += 1
                    i += 1
                i += 1
            elif i + 2 < len(toks):
                c2, wv = int(float(toks[i + 1])), float(toks[i + 2])
                for cc in range(c, min(c2, c + 65535) + 1):
                    self.widths[cc] = wv
                i += 3
            else:
                break

    def _load_program(self, key, data, head):
        from fontTools.ttLib import TTFont
        if key == b"FontFile2" or data[:4] in (b"\x00\x01\x00\x00", b"true", b"OTTO"):
            self.tt = TTFont(io.BytesIO(data), lazy=True)
            self.upm = float(self.tt["head"].unitsPerEm)
            return
        if key == b"FontFile3":
            from fontTools.cffLib import CFFFontSet
            cff = CFFFontSet()
            cff.decompile(io.BytesIO(data), None)
            top = cff[cff.fontNames[0]]
            cs = top.CharStrings
            names = list(top.charset)
            cid_keyed = hasattr(top, "ROS")
            fm = getattr(top, "FontMatrix", [0.001, 0, 0, 0.001, 0, 0])
            self.upm = 1.0 / fm[0] if fm[0] else 1000.0
            enc = getattr(top, "Encoding", None)
            self.cff = (cs, names, cid_keyed, enc if isinstance(enc, list) else None)
            return
        self.warnings.append("Type1-Schriften (PostScript) werden durch eine Systemschrift ersetzt.")

    def _load_fallback(self):
        from fontTools.ttLib import TTFont
        from .text_import import default_font
        path = default_font()
        if path:
            f = TTFont(path, fontNumber=0, lazy=True)
            self.fallback = (f, f.getGlyphSet(), f.getBestCmap() or {})
            self.upm = float(f["head"].unitsPerEm)
            name = re.search(rb"/BaseFont\s*/([^\s/>]+)", self.body)
            self.warnings.append(f"Schrift {name.group(1).decode('latin-1') if name else ''} nicht eingebettet – "
                                 f"Ersatzschrift verwendet.")

    # ------------------------------------------------------------ Glyphen
    def codes(self, s: bytes) -> list[int]:
        if self.two_byte:
            return [int.from_bytes(s[i:i + 2], "big") for i in range(0, len(s) - 1, 2)]
        return list(s)

    def unicode(self, code: int) -> str:
        if code in self.to_unicode:
            return self.to_unicode[code]
        if code in self.encoding:
            return _agl(self.encoding[code])
        if self.two_byte:
            return ""
        try:
            return bytes([code]).decode(self.base_codec)
        except UnicodeDecodeError:
            return ""

    def width(self, code: int) -> float:
        """Vorschub in 1/1000 Textraum."""
        if code in self.widths:
            return self.widths[code]
        if self.two_byte:
            return self.default_width
        _, adv = self.glyph(code)
        return adv * 1000.0 / self.upm if adv else 500.0

    def _glyph_name(self, code: int) -> str | None:
        if self.tt is not None:
            order = self.tt.getGlyphOrder()
            if self.two_byte:
                gid = int(self.cid_to_gid[code]) if self.cid_to_gid is not None and code < len(self.cid_to_gid) else code
                return order[gid] if 0 <= gid < len(order) else None
            if code in self.encoding and self.encoding[code] in self.tt.getGlyphSet():
                return self.encoding[code]
            cmaps = {(t.platformID, t.platEncID): t.cmap for t in self.tt["cmap"].tables} if "cmap" in self.tt else {}
            u = self.unicode(code)
            if u and (3, 1) in cmaps and ord(u[0]) in cmaps[(3, 1)]:
                return cmaps[(3, 1)][ord(u[0])]
            if (1, 0) in cmaps and code in cmaps[(1, 0)]:
                return cmaps[(1, 0)][code]
            if (3, 0) in cmaps:
                for c in (0xF000 + code, code):
                    if c in cmaps[(3, 0)]:
                        return cmaps[(3, 0)][c]
            return order[code] if code < len(order) and self.tt.get("cmap") is None else None
        if self.cff is not None:
            cs, names, cid_keyed, enc = self.cff
            if self.two_byte:
                if cid_keyed:
                    n = f"cid{code:05d}"
                    return n if n in cs else None
                return names[code] if code < len(names) else None
            if code in self.encoding:
                return self.encoding[code]
            if enc and code < len(enc) and enc[code] != ".notdef":
                return enc[code]
            u = self.unicode(code)
            if u:
                try:
                    from fontTools import agl
                    n = agl.UV2AGL.get(ord(u[0]))
                    if n and n in cs:
                        return n
                except Exception:
                    pass
            return None
        return None

    def glyph(self, code: int) -> tuple[list, float]:
        """(Konturen in Schrift-Einheiten, Vorschub in Schrift-Einheiten)."""
        if code in self.cache:
            return self.cache[code]
        polys, adv = [], 0.0
        try:
            if self.fallback is not None:
                f, gs, cmap = self.fallback
                u = self.unicode(code)
                name = cmap.get(ord(u[0])) if u else None
                if name:
                    pen = _Pen(gs)
                    gs[name].draw(pen)
                    polys, adv = pen.bb.out, float(f["hmtx"][name][0])
            else:
                name = self._glyph_name(code)
                if name is not None:
                    if self.tt is not None:
                        gs = self.tt.getGlyphSet()
                        if name in gs:
                            pen = _Pen(gs)
                            gs[name].draw(pen)
                            polys, adv = pen.bb.out, float(self.tt["hmtx"][name][0]) if "hmtx" in self.tt else 0.0
                    else:
                        cs = self.cff[0]
                        if name in cs:
                            pen = _Pen(None)
                            cs[name].draw(pen)
                            polys = pen.bb.out
                            adv = float(getattr(cs[name], "width", 0) or 0)
        except Exception:
            polys = []
        self.cache[code] = (polys, adv)
        return polys, adv
