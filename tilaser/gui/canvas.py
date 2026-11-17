"""Arbeitsfläche: Bett, Objekte (verschiebbar), Werkzeugweg-Vorschau.

Szenenkoordinaten = mm, Y nach unten (Qt). Modell: Y nach oben -> y_scene = -y_mm.
"""
from __future__ import annotations

import numpy as np
from PyQt6.QtCore import QPointF, QRectF, Qt, pyqtSignal
from PyQt6.QtGui import QBrush, QColor, QImage, QPainter, QPainterPath, QPen, QTransform
from PyQt6.QtWidgets import (QGraphicsItem, QGraphicsPathItem, QGraphicsRectItem, QGraphicsScene,
                             QGraphicsSimpleTextItem, QGraphicsView)

from ..core.model import Item, Project
from ..core.geometry import apply_matrix as g_apply
from ..core.raster import preview_image


def _cosmetic(color: QColor, width: float = 1.0, style=Qt.PenStyle.SolidLine) -> QPen:
    pen = QPen(color, width)
    pen.setCosmetic(True)
    pen.setStyle(style)
    return pen


class ItemGraphic(QGraphicsItem):
    """Darstellung eines Modell-Objekts. Lokale Koordinaten = (x, -y) der Modellpfade."""

    def __init__(self, item: Item, project: Project, on_moved, snap_fn=None):
        super().__init__()
        self.snap_fn = snap_fn
        self.item = item
        self.project = project
        self.on_moved = on_moved
        self._paths: list[tuple[str, QPainterPath, bool, bool]] = []
        self._qimage: QImage | None = None
        self._img_buf = None
        self._rect = QRectF()
        self._syncing = False
        self.setFlags(QGraphicsItem.GraphicsItemFlag.ItemIsMovable
                      | QGraphicsItem.GraphicsItemFlag.ItemIsSelectable
                      | QGraphicsItem.GraphicsItemFlag.ItemSendsGeometryChanges)
        self.rebuild()
        self.sync_from_model()

    def rebuild(self):
        """Pfade/Bild neu aufbauen (nach Ebenen- oder Geometrieänderung)."""
        self.prepareGeometryChange()
        it = self.item
        self._paths = []
        if it.kind == "raster":
            layer = self.project.layer(it.layer_id)
            if layer is not None and it.image is not None:
                img = preview_image(it.image, it.img_w, it.img_h, layer.interval, layer.dither,
                                    layer.threshold, layer.invert, 600, layer.brightness, layer.contrast,
                                    layer.gamma)
            else:
                img = it.image
            self._img_buf = np.ascontiguousarray(img)
            h, w = self._img_buf.shape
            self._qimage = QImage(self._img_buf.data, w, h, w, QImage.Format.Format_Grayscale8).copy()
            self._rect = QRectF(-it.img_w / 2, -it.img_h / 2, it.img_w, it.img_h)
        else:
            by_layer: dict[str, QPainterPath] = {}
            for vp in it.paths:
                pp = by_layer.setdefault(vp.layer_id, QPainterPath())
                pts = vp.poly.loop_pts()
                pp.moveTo(pts[0][0], -pts[0][1])
                for x, y in pts[1:]:
                    pp.lineTo(x, -y)
            for lid, pp in by_layer.items():
                layer = self.project.layer(lid)
                color = layer.color if layer else "#000000"
                filled = bool(layer and layer.mode in ("fill", "raster"))
                out = bool(layer and layer.output)
                pp.setFillRule(Qt.FillRule.OddEvenFill)
                self._paths.append((color, pp, filled, out))
            r = QRectF()
            for _, pp, _, _ in self._paths:
                r = r.united(pp.boundingRect())
            self._rect = r
        self.update()

    def sync_from_model(self):
        it = self.item
        self._syncing = True
        self.setTransform(QTransform.fromScale(-1, 1) if it.mirror_x else QTransform())
        self.setRotation(-it.rotation)
        self.setScale(it.scale)
        self.setPos(it.x, -it.y)
        self._syncing = False

    def boundingRect(self) -> QRectF:
        m = 1.0 / max(self.item.scale, 1e-6)
        return self._rect.adjusted(-m, -m, m, m)

    def paint(self, painter: QPainter, option, widget=None):
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        if self.item.kind == "raster" and self._qimage is not None:
            layer = self.project.layer(self.item.layer_id)
            if layer is not None and not layer.output:
                painter.setOpacity(0.3)
            painter.drawImage(self._rect, self._qimage)
            painter.setOpacity(1.0)
            painter.setPen(_cosmetic(QColor(layer.color if layer else "#888"), 1, Qt.PenStyle.DotLine))
            painter.drawRect(self._rect)
        for color, pp, filled, out in self._paths:
            c = QColor(color)
            if not out:
                c.setAlpha(70)
            painter.setPen(_cosmetic(c, 1.2))
            if filled:
                fc = QColor(c)
                fc.setAlpha(60 if out else 20)
                painter.setBrush(QBrush(fc))
            else:
                painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawPath(pp)
        if self.isSelected():
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(_cosmetic(QColor("#1976d2"), 1, Qt.PenStyle.DashLine))
            painter.drawRect(self._rect)

    def itemChange(self, change, value):
        if (change == QGraphicsItem.GraphicsItemChange.ItemPositionChange and not self._syncing
                and self.snap_fn is not None):
            return self.snap_fn(self, value)
        if change == QGraphicsItem.GraphicsItemChange.ItemPositionHasChanged and not self._syncing:
            p = self.pos()
            self.item.x, self.item.y = p.x(), -p.y()
            self.on_moved(self.item)
        return super().itemChange(change, value)


class SelectionOverlay(QGraphicsItem):
    """Auswahlrahmen mit Anfassern: Ecken = proportional skalieren, Kreis oben = drehen."""
    HANDLE_PX = 7
    ROT_PX = 22

    def __init__(self, view):
        super().__init__()
        self.view = view
        self.bb = None          # Welt-Box (x0, y0, x1, y1)
        self.setZValue(50)
        self.setAcceptedMouseButtons(Qt.MouseButton.NoButton)

    def set_bbox(self, bb):
        self.prepareGeometryChange()
        self.bb = bb
        self.setVisible(bb is not None)
        self.update()

    def _px(self):
        return 1.0 / max(self.view.transform().m11(), 1e-6)

    def scene_rect(self):
        x0, y0, x1, y1 = self.bb
        return QRectF(x0, -y1, x1 - x0, y1 - y0)

    def handles(self):
        """{'name': (Szenenpunkt QPointF, Ankerpunkt in Weltkoordinaten)}"""
        x0, y0, x1, y1 = self.bb
        h = {"bl": (QPointF(x0, -y0), (x1, y1)), "br": (QPointF(x1, -y0), (x0, y1)),
             "tl": (QPointF(x0, -y1), (x1, y0)), "tr": (QPointF(x1, -y1), (x0, y0))}
        h["rot"] = (QPointF((x0 + x1) / 2, -y1 - self.ROT_PX * self._px()), ((x0 + x1) / 2, (y0 + y1) / 2))
        return h

    def hit(self, scene_pt):
        if self.bb is None or not self.isVisible():
            return None
        tol = (self.HANDLE_PX + 3) * self._px()
        for name, (pt, anchor) in self.handles().items():
            if abs(pt.x() - scene_pt.x()) <= tol and abs(pt.y() - scene_pt.y()) <= tol:
                return name, anchor
        return None

    def boundingRect(self):
        if self.bb is None:
            return QRectF()
        m = (self.ROT_PX + self.HANDLE_PX * 2) * self._px()
        return self.scene_rect().adjusted(-m, -m, m, m)

    def paint(self, painter, option, widget=None):
        if self.bb is None:
            return
        px = self._px()
        r = self.scene_rect()
        painter.setPen(_cosmetic(QColor("#1976d2"), 1, Qt.PenStyle.DashLine))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRect(r)
        hs = self.HANDLE_PX * px
        painter.setPen(_cosmetic(QColor("#1976d2"), 1))
        painter.setBrush(QColor("white"))
        hd = self.handles()
        for name, (pt, _) in hd.items():
            if name == "rot":
                painter.drawLine(QPointF(pt.x(), r.top()), pt)
                painter.drawEllipse(pt, hs * 0.8, hs * 0.8)
            else:
                painter.drawRect(QRectF(pt.x() - hs / 2, pt.y() - hs / 2, hs, hs))


class LaserCanvas(QGraphicsView):
    selectionChanged = pyqtSignal(list)         # Liste der Item-IDs
    itemMoved = pyqtSignal(str)
    mouseMoved = pyqtSignal(float, float)
    filesDropped = pyqtSignal(list)
    deleteRequested = pyqtSignal()
    contextMenuRequested = pyqtSignal(object)   # globale Position (QPoint)
    aboutToModify = pyqtSignal(str)             # vor einer Änderung (für Rückgängig)
    shapeDrawn = pyqtSignal(str, object)        # Art, Daten (Box bzw. Punktliste)
    nodesEdited = pyqtSignal(str)               # Item-ID nach Knotenbearbeitung
    toolChanged = pyqtSignal(str)
    itemsTransformed = pyqtSignal(list)         # Anfasser: Skalierung/Drehung geändert

    def __init__(self, parent=None):
        super().__init__(parent)
        self.scene_ = QGraphicsScene(self)
        self.setScene(self.scene_)
        self.setRenderHint(QPainter.RenderHint.Antialiasing)
        self.setDragMode(QGraphicsView.DragMode.RubberBandDrag)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setViewportUpdateMode(QGraphicsView.ViewportUpdateMode.FullViewportUpdate)
        self.setBackgroundBrush(QColor("#e9ecef"))
        self.setMouseTracking(True)
        self.setAcceptDrops(True)
        self.project: Project | None = None
        self.graphics: dict[str, ItemGraphic] = {}
        self.bed_items = []
        self.toolpath_items = []
        self._pan_start = None
        self.bed_w, self.bed_h = 400.0, 400.0
        self.snap_enabled = False
        self.snap_step = 1.0
        self._drag = None              # laufende Anfasser-Aktion
        self.overlay = SelectionOverlay(self)
        self.scene_.addItem(self.overlay)
        self.overlay.set_bbox(None)
        self.sim_items = []
        self.tool = "select"
        self._draw = None              # laufende Zeichnung
        self._draw_item = QGraphicsPathItem()
        self._draw_item.setPen(_cosmetic(QColor("#1976d2"), 1.5, Qt.PenStyle.DashLine))
        self._draw_item.setZValue(60)
        self.scene_.addItem(self._draw_item)
        self._node = None              # Knotenbearbeitung: {'gi', 'drag': (pi, vi), 'sel': (pi, vi)}
        self._node_item = QGraphicsPathItem()
        self._node_item.setPen(_cosmetic(QColor("#1976d2"), 1))
        self._node_item.setBrush(QColor("white"))
        self._node_item.setZValue(61)
        self.scene_.addItem(self._node_item)
        self._node_sel_item = QGraphicsPathItem()
        self._node_sel_item.setPen(_cosmetic(QColor("#d32f2f"), 1))
        self._node_sel_item.setBrush(QColor("#d32f2f"))
        self._node_sel_item.setZValue(62)
        self.scene_.addItem(self._node_sel_item)
        self.background = None
        self.scene_.selectionChanged.connect(self._on_selection)

    # ------------------------------------------------------------ Bett
    def set_bed(self, w: float, h: float):
        self.bed_w, self.bed_h = w, h
        for it in self.bed_items:
            self.scene_.removeItem(it)
        self.bed_items = []
        bed = QGraphicsRectItem(0, -h, w, h)
        bed.setBrush(QColor("white"))
        bed.setPen(_cosmetic(QColor("#555"), 1))
        bed.setZValue(-10)
        self.scene_.addItem(bed)
        self.bed_items.append(bed)
        grid = QPainterPath()
        major = QPainterPath()
        step = 10
        for x in range(step, int(w), step):
            (major if x % 50 == 0 else grid).moveTo(x, 0)
            (major if x % 50 == 0 else grid).lineTo(x, -h)
        for y in range(step, int(h), step):
            (major if y % 50 == 0 else grid).moveTo(0, -y)
            (major if y % 50 == 0 else grid).lineTo(w, -y)
        for pp, col in ((grid, "#eef1f4"), (major, "#d5dbe1")):
            gi = QGraphicsPathItem(pp)
            gi.setPen(_cosmetic(QColor(col), 1))
            gi.setZValue(-9)
            self.scene_.addItem(gi)
            self.bed_items.append(gi)
        for x in range(0, int(w) + 1, 50):
            t = QGraphicsSimpleTextItem(str(x))
            t.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIgnoresTransformations)
            t.setBrush(QColor("#6c757d"))
            t.setPos(x, 1)
            self.scene_.addItem(t)
            self.bed_items.append(t)
        for y in range(50, int(h) + 1, 50):
            t = QGraphicsSimpleTextItem(str(y))
            t.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIgnoresTransformations)
            t.setBrush(QColor("#6c757d"))
            t.setPos(-12, -y)
            self.scene_.addItem(t)
            self.bed_items.append(t)
        origin = QGraphicsPathItem()
        op = QPainterPath()
        op.moveTo(0, 0)
        op.lineTo(15, 0)
        op.moveTo(0, 0)
        op.lineTo(0, -15)
        origin.setPath(op)
        origin.setPen(_cosmetic(QColor("#d32f2f"), 2))
        origin.setZValue(-8)
        self.scene_.addItem(origin)
        self.bed_items.append(origin)
        m = 20
        self.scene_.setSceneRect(QRectF(-m - 50, -h - m - 50, w + 2 * m + 100, h + 2 * m + 100))

    def zoom_fit(self):
        bb = self.project.bbox(only_output=False) if self.project and self.project.items else None
        if bb:
            r = QRectF(min(0, bb[0]), -max(self.bed_h, bb[3]), max(self.bed_w, bb[2]) - min(0, bb[0]),
                       max(self.bed_h, bb[3]) - min(0, bb[1]))
        else:
            r = QRectF(0, -self.bed_h, self.bed_w, self.bed_h)
        self.fitInView(r.adjusted(-15, -15, 15, 15), Qt.AspectRatioMode.KeepAspectRatio)

    # ------------------------------------------------------------ Objekte
    def set_project(self, project: Project):
        self.project = project
        self.rebuild_all()

    def rebuild_all(self):
        selected = set(self.selected_ids())
        for gi in self.graphics.values():
            self.scene_.removeItem(gi)
        self.graphics = {}
        for it in self.project.items:
            self.add_item(it, select=it.id in selected)
        self.update_overlay()

    def add_item(self, item: Item, select=False):
        gi = ItemGraphic(item, self.project, self._on_graphic_moved, self._snap)
        self.scene_.addItem(gi)
        self.graphics[item.id] = gi
        gi.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable, self.tool == "select")
        gi.setSelected(select)
        return gi

    def remove_item(self, item_id: str):
        gi = self.graphics.pop(item_id, None)
        if gi:
            self.scene_.removeItem(gi)
        self.update_overlay()

    def refresh_item(self, item_id: str, geometry=False):
        gi = self.graphics.get(item_id)
        if gi:
            if geometry:
                gi.rebuild()
            gi.sync_from_model()
        self.update_overlay()

    def refresh_styles(self):
        for gi in self.graphics.values():
            gi.rebuild()

    def selected_ids(self) -> list[str]:
        return [gi.item.id for gi in self.graphics.values() if gi.isSelected()]

    def select_only(self, ids):
        self.scene_.clearSelection()
        for i in ids:
            if i in self.graphics:
                self.graphics[i].setSelected(True)

    def _on_selection(self):
        try:
            self.update_overlay()
            self.selectionChanged.emit(self.selected_ids())
        except RuntimeError:
            pass

    def _on_graphic_moved(self, item):
        self.update_overlay()
        self.itemMoved.emit(item.id)

    def update_overlay(self):
        items = [gi.item for gi in self.graphics.values() if gi.isSelected()]
        if self._node is not None:
            if self._node.get("gi") is not None:
                self._node["gi"] = self.graphics.get(self._node["gi"].item.id)   # nach Neuaufbau ersetzen
            self._update_nodes()
        if not items:
            self.overlay.set_bbox(None)
            return
        bbs = [it.world_bbox() for it in items]
        self.overlay.set_bbox((min(b[0] for b in bbs), min(b[1] for b in bbs),
                               max(b[2] for b in bbs), max(b[3] for b in bbs)))
        if self.tool != "select":
            self.overlay.setVisible(False)

    def _snap(self, gi, value):
        """Fangen: linke untere Ecke des Objekts auf das Raster legen."""
        if not self.snap_enabled or self._drag is not None:
            return value
        it = gi.item
        bb = it.world_bbox()
        offx, offy = bb[0] - it.x, bb[1] - it.y
        cx, cy = value.x(), -value.y()
        st = self.snap_step
        nx = round((cx + offx) / st) * st - offx
        ny = round((cy + offy) / st) * st - offy
        return QPointF(nx, -ny)

    # ------------------------------------------------------------ Werkzeugweg
    def clear_toolpath(self):
        for it in self.toolpath_items:
            self.scene_.removeItem(it)
        self.toolpath_items = []

    def show_toolpath(self, result, show_travel=True):
        self.clear_toolpath()

        def segs_path(arr):
            pp = QPainterPath()
            last = None
            for (x0, y0), (x1, y1) in arr:
                if last is None or abs(last[0] - x0) > 1e-6 or abs(last[1] - y0) > 1e-6:
                    pp.moveTo(x0, -y0)
                pp.lineTo(x1, -y1)
                last = (x1, y1)
            return pp

        if show_travel and len(result.travel):
            ti = QGraphicsPathItem(segs_path(result.travel))
            ti.setPen(_cosmetic(QColor(120, 120, 120, 150), 1, Qt.PenStyle.DashLine))
            ti.setZValue(20)
            self.scene_.addItem(ti)
            self.toolpath_items.append(ti)
        for color, arr in result.burn:
            bi = QGraphicsPathItem(segs_path(arr))
            c = QColor(color)
            if c.lightness() > 200:
                c = c.darker(150)
            bi.setPen(_cosmetic(c, 1.5))
            bi.setZValue(21)
            self.scene_.addItem(bi)
            self.toolpath_items.append(bi)

    # ------------------------------------------------------------ Kamera-Hintergrund
    def set_background(self, img, bed_w, bed_h, opacity=0.75):
        """Entzerrtes Kamerabild (uint8, H×W oder H×W×3, oben = Y max) unter die Objekte legen."""
        from PyQt6.QtGui import QPixmap
        from PyQt6.QtWidgets import QGraphicsPixmapItem
        self.clear_background()
        a = np.ascontiguousarray(img)
        h, w = a.shape[:2]
        if a.ndim == 2:
            qi = QImage(a.data, w, h, w, QImage.Format.Format_Grayscale8).copy()
        else:
            qi = QImage(a.data, w, h, w * 3, QImage.Format.Format_RGB888).copy()
        item = QGraphicsPixmapItem(QPixmap.fromImage(qi))
        item.setTransformationMode(Qt.TransformationMode.SmoothTransformation)
        item.setScale(bed_w / w)
        item.setPos(0, -bed_h)
        item.setZValue(-9.5)
        item.setOpacity(opacity)
        self.scene_.addItem(item)
        self.background = item

    def clear_background(self):
        if self.background is not None:
            self.scene_.removeItem(self.background)
            self.background = None

    # ------------------------------------------------------------ Werkzeuge
    def set_tool(self, tool: str):
        self._cancel_draw()
        self.tool = tool
        drag = QGraphicsView.DragMode.RubberBandDrag if tool == "select" else QGraphicsView.DragMode.NoDrag
        self.setDragMode(drag)
        self.setCursor(Qt.CursorShape.CrossCursor if tool not in ("select", "node") else Qt.CursorShape.ArrowCursor)
        for gi in self.graphics.values():
            gi.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable, tool == "select")
        self.overlay.setVisible(tool == "select" and self.overlay.bb is not None)
        if tool == "node":
            sel = [gi for gi in self.graphics.values() if gi.isSelected() and gi.item.kind == "vector"]
            self._node = {"gi": sel[0] if len(sel) == 1 else None, "drag": None, "sel": None}
        else:
            self._node = None
        self._update_nodes()
        self.toolChanged.emit(tool)

    def _snap_pt(self, x, y):
        if self.snap_enabled:
            st = self.snap_step
            return round(x / st) * st, round(y / st) * st
        return x, y

    def _cancel_draw(self):
        self._draw = None
        self._draw_item.setPath(QPainterPath())

    def _draw_preview(self):
        d = self._draw
        pp = QPainterPath()
        if d is None:
            self._draw_item.setPath(pp)
            return
        if d["kind"] == "line":
            pts = d["pts"] + ([d["cur"]] if d.get("cur") else [])
            if pts:
                pp.moveTo(pts[0][0], -pts[0][1])
                for x, y in pts[1:]:
                    pp.lineTo(x, -y)
        else:
            from ..core import shapes
            (x0, y0), (x1, y1) = d["p0"], d["p1"]
            w, h = x1 - x0, y1 - y0
            if abs(w) < 1e-6 or abs(h) < 1e-6:
                self._draw_item.setPath(pp)
                return
            if d["kind"] == "rect":
                poly = shapes.rectangle(w, h, d.get("radius", 0.0))
            elif d["kind"] == "ellipse":
                poly = shapes.ellipse(w, h)
            else:
                poly = shapes.polygon(d.get("sides", 6), w, h, d.get("star", 0.0))
            cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
            pts = poly.pts + [cx, cy]
            pp.moveTo(pts[0][0], -pts[0][1])
            for x, y in pts[1:]:
                pp.lineTo(x, -y)
            pp.closeSubpath()
        self._draw_item.setPath(pp)

    def _box_from(self, p0, p, mods):
        x0, y0 = p0
        x1, y1 = p
        if mods & Qt.KeyboardModifier.ShiftModifier:          # Quadrat / Kreis
            s_ = max(abs(x1 - x0), abs(y1 - y0))
            x1 = x0 + s_ * (1 if x1 >= x0 else -1)
            y1 = y0 + s_ * (1 if y1 >= y0 else -1)
        if mods & Qt.KeyboardModifier.ControlModifier:        # vom Mittelpunkt aus
            x0, y0 = 2 * x0 - x1, 2 * y0 - y1
        return (x0, y0), (x1, y1)

    def _finish_line(self, closed=False):
        d = self._draw
        if d and len(d["pts"]) >= 2:
            self.shapeDrawn.emit("line", {"pts": list(d["pts"]), "closed": closed})
        self._cancel_draw()

    # ------------------------------------------------------------ Knotenbearbeitung
    def _node_world(self):
        n = self._node
        if not n or n["gi"] is None:
            return []
        it = n["gi"].item
        m = it.matrix()
        return [g_apply(m, vp.poly.pts) for vp in it.paths]

    def _update_nodes(self):
        pp, sp = QPainterPath(), QPainterPath()
        n = self._node
        if n and n["gi"] is not None:
            px = 1.0 / max(self.transform().m11(), 1e-6)
            hs = 3.5 * px
            world = self._node_world()
            total = sum(len(w) for w in world)
            if total <= 5000:
                for pi, pts in enumerate(world):
                    for vi, (x, y) in enumerate(pts):
                        target = sp if n["sel"] == (pi, vi) else pp
                        target.addRect(QRectF(x - hs, -y - hs, 2 * hs, 2 * hs))
        self._node_item.setPath(pp)
        self._node_sel_item.setPath(sp)

    def _node_hit(self, x, y):
        px = 1.0 / max(self.transform().m11(), 1e-6)
        tol = 6 * px
        best, bd = None, tol
        for pi, pts in enumerate(self._node_world()):
            if not len(pts):
                continue
            d = np.hypot(pts[:, 0] - x, pts[:, 1] - y)
            vi = int(np.argmin(d))
            if d[vi] < bd:
                best, bd = (pi, vi), d[vi]
        return best

    def _segment_hit(self, x, y):
        px = 1.0 / max(self.transform().m11(), 1e-6)
        tol = 6 * px
        best, bd = None, tol
        it = self._node["gi"].item
        for pi, pts in enumerate(self._node_world()):
            closed = it.paths[pi].poly.closed
            n = len(pts)
            for i in range(n if closed else n - 1):
                a, b = pts[i], pts[(i + 1) % n]
                ab = b - a
                L2 = float(np.dot(ab, ab))
                if L2 < 1e-12:
                    continue
                t = max(0.0, min(1.0, float(np.dot([x, y] - a, ab)) / L2))
                q = a + ab * t
                d = float(np.hypot(*(q - [x, y])))
                if d < bd:
                    best, bd = (pi, i + 1, (q[0], q[1])), d
        return best

    def _node_detach(self, pi):
        """Polylinie vor dem Ändern kopieren (Rückgängig-Schnappschüsse teilen die Geometrie)."""
        from ..core.geometry import Polyline
        vp = self._node["gi"].item.paths[pi]
        vp.poly = Polyline(vp.poly.pts.copy(), vp.poly.closed)
        return vp

    def _to_local(self, x, y):
        it = self._node["gi"].item
        inv = np.linalg.inv(it.matrix())
        return g_apply(inv, np.array([[x, y]]))[0]

    def delete_node(self):
        n = self._node
        if not n or n["gi"] is None or n["sel"] is None:
            return False
        pi, vi = n["sel"]
        it = n["gi"].item
        poly = it.paths[pi].poly
        min_pts = 3 if poly.closed else 2
        self.aboutToModify.emit("Knoten löschen")
        if len(poly.pts) <= min_pts:
            del it.paths[pi]                                # ganze Kontur entfernen
        else:
            vp = self._node_detach(pi)
            vp.poly.pts = np.delete(vp.poly.pts, vi, axis=0)
        n["sel"] = None
        n["gi"].rebuild()
        self._update_nodes()
        self.nodesEdited.emit(it.id)
        return True

    # ------------------------------------------------------------ Simulation
    def clear_simulation(self):
        for it in self.sim_items:
            self.scene_.removeItem(it)
        self.sim_items = []
        self._sim = None

    def start_simulation(self, result):
        """Bereitet die Simulation vor: Werkzeugweg blass, Fortschritt kräftig."""
        self.clear_simulation()
        self.clear_toolpath()
        mv = result.moves if result.moves is not None else np.zeros((0, 6))
        faint = QGraphicsPathItem(self._moves_path(mv[mv[:, 4] >= 0]))
        faint.setPen(_cosmetic(QColor(0, 0, 0, 40), 1))
        faint.setZValue(19)
        self.scene_.addItem(faint)
        travel = QGraphicsPathItem()
        travel.setPen(_cosmetic(QColor(120, 120, 120, 160), 1, Qt.PenStyle.DashLine))
        travel.setZValue(20)
        self.scene_.addItem(travel)
        burns = []
        for c in result.colors:
            bi = QGraphicsPathItem()
            col = QColor(c)
            if col.lightness() > 200:
                col = col.darker(150)
            bi.setPen(_cosmetic(col, 2))
            bi.setZValue(21)
            self.scene_.addItem(bi)
            burns.append(bi)
        head = QGraphicsPathItem()
        hp = QPainterPath()
        hp.addEllipse(QPointF(0, 0), 4, 4)
        hp.moveTo(-8, 0)
        hp.lineTo(8, 0)
        hp.moveTo(0, -8)
        hp.lineTo(0, 8)
        head.setPath(hp)
        head.setPen(_cosmetic(QColor("#d32f2f"), 2))
        head.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIgnoresTransformations)
        head.setZValue(30)
        self.scene_.addItem(head)
        self.sim_items = [faint, travel, head] + burns
        self._sim = {"mv": mv, "k": 0, "travel": travel, "burns": burns, "head": head,
                     "paths": [QPainterPath() for _ in burns], "tpath": QPainterPath(),
                     "total": float(mv[-1, 5]) if len(mv) else 0.0}
        self.set_simulation_time(0.0)

    @staticmethod
    def _moves_path(mv, pp=None):
        if pp is None:
            pp = QPainterPath()
        last = None
        for x0, y0, x1, y1 in mv[:, :4]:
            if last is None or abs(last[0] - x0) > 1e-6 or abs(last[1] - y0) > 1e-6:
                pp.moveTo(x0, -y0)
            pp.lineTo(x1, -y1)
            last = (x1, y1)
        return pp

    def set_simulation_time(self, t: float):
        sim = getattr(self, "_sim", None)
        if not sim:
            return
        mv = sim["mv"]
        k = int(np.searchsorted(mv[:, 5], t, side="right")) if len(mv) else 0
        if k < sim["k"]:                                  # rückwärts: neu aufbauen
            sim["paths"] = [QPainterPath() for _ in sim["burns"]]
            sim["tpath"] = QPainterPath()
            sim["k"] = 0
        new = mv[sim["k"]:k]
        if len(new):
            for ci in range(len(sim["burns"])):
                sel = new[new[:, 4] == ci]
                if len(sel):
                    sim["paths"][ci] = self._moves_path(sel, sim["paths"][ci])
            sel = new[new[:, 4] < 0]
            if len(sel):
                sim["tpath"] = self._moves_path(sel, sim["tpath"])
        sim["k"] = k
        for bi, pp in zip(sim["burns"], sim["paths"]):
            bi.setPath(pp)
        sim["travel"].setPath(sim["tpath"])
        # Kopfposition (innerhalb der aktuellen Bewegung interpoliert)
        if len(mv) == 0:
            return
        if k >= len(mv):
            x, y = mv[-1, 2], mv[-1, 3]
        else:
            t0 = mv[k - 1, 5] if k > 0 else 0.0
            t1 = mv[k, 5]
            f = 0.0 if t1 <= t0 else (t - t0) / (t1 - t0)
            x = mv[k, 0] + (mv[k, 2] - mv[k, 0]) * f
            y = mv[k, 1] + (mv[k, 3] - mv[k, 1]) * f
        sim["head"].setPos(x, -y)

    # ------------------------------------------------------------ Maus / Tastatur
    def wheelEvent(self, e):
        f = 1.15 ** (e.angleDelta().y() / 120)
        cur = self.transform().m11()
        if 0.05 < cur * f < 200:
            self.scale(f, f)
            self.overlay.set_bbox(self.overlay.bb)
            self._update_nodes()

    def mousePressEvent(self, e):
        # Mitte: verschieben. Rechts: verschieben beim Ziehen, Kontextmenü beim Klicken.
        if e.button() in (Qt.MouseButton.MiddleButton, Qt.MouseButton.RightButton):
            self._pan_start = e.position()
            self._pan_origin = e.position()
            self._pan_button = e.button()
            self._panned = False
            return
        if e.button() == Qt.MouseButton.LeftButton and self.tool not in ("select",):
            sp = self.mapToScene(e.position().toPoint())
            x, y = sp.x(), -sp.y()
            if self.tool == "node":
                n = self._node
                if n["gi"] is not None:
                    hit = self._node_hit(x, y)
                    if hit is not None:
                        self.aboutToModify.emit("Knoten verschieben")
                        self._node_detach(hit[0])
                        n["drag"] = hit
                        n["sel"] = hit
                        self._update_nodes()
                        return
                gi = self._graphic_at(e.position().toPoint())
                if gi is not None and gi.item.kind == "vector":
                    self.scene_.clearSelection()
                    gi.setSelected(True)
                    n["gi"], n["sel"] = gi, None
                    self.overlay.setVisible(False)
                    self._update_nodes()
                return
            x, y = self._snap_pt(x, y)
            if self.tool == "line":
                d = self._draw or {"kind": "line", "pts": []}
                if len(d["pts"]) >= 2:
                    fx, fy = d["pts"][0]
                    px = 1.0 / max(self.transform().m11(), 1e-6)
                    if np.hypot(fx - x, fy - y) < 8 * px:            # Klick auf Startpunkt = schließen
                        self._draw = d
                        self._finish_line(closed=True)
                        return
                d["pts"].append((x, y))
                self._draw = d
                self._draw_preview()
                return
            self._draw = {"kind": self.tool, "p0": (x, y), "p1": (x, y), "start": (x, y),
                          "sides": getattr(self, "poly_sides", 6), "star": getattr(self, "poly_star", 0.0),
                          "radius": getattr(self, "rect_radius", 0.0)}
            return
        if e.button() == Qt.MouseButton.LeftButton:
            sp = self.mapToScene(e.position().toPoint())
            hit = self.overlay.hit(sp)
            if hit is not None:
                name, anchor = hit
                items = [gi.item for gi in self.graphics.values() if gi.isSelected()]
                self.aboutToModify.emit("Drehen" if name == "rot" else "Skalieren")
                self._drag = {"mode": name, "anchor": anchor, "start": (sp.x(), -sp.y()), "items": items,
                              "orig": [(it.x, it.y, it.scale, it.rotation) for it in items]}
                return
            gi = self._graphic_at(e.position().toPoint())
            if gi is not None:
                self.aboutToModify.emit("Verschieben")
        super().mousePressEvent(e)

    def mouseMoveEvent(self, e):
        if self.tool != "select" and self._pan_start is None:
            sp = self.mapToScene(e.position().toPoint())
            x, y = sp.x(), -sp.y()
            self.mouseMoved.emit(x, y)
            if self.tool == "node":
                n = self._node
                if n and n["drag"] is not None:
                    pi, vi = n["drag"]
                    lx, ly = self._to_local(*self._snap_pt(x, y))
                    n["gi"].item.paths[pi].poly.pts[vi] = (lx, ly)
                    n["gi"].rebuild()
                    self._update_nodes()
                return
            if self._draw is not None:
                x, y = self._snap_pt(x, y)
                if self._draw["kind"] == "line":
                    self._draw["cur"] = (x, y)
                else:
                    self._draw["p0"], self._draw["p1"] = self._box_from(self._draw["start"], (x, y), e.modifiers())
                self._draw_preview()
            return
        if self._drag is not None:
            sp = self.mapToScene(e.position().toPoint())
            self._drag_update(sp.x(), -sp.y(), e.modifiers())
            return
        if self._pan_start is not None:
            if not self._panned and (e.position() - self._pan_origin).manhattanLength() < 4:
                return
            if not self._panned:
                self._panned = True
                self.setCursor(Qt.CursorShape.ClosedHandCursor)
            d = e.position() - self._pan_start
            self._pan_start = e.position()
            self.horizontalScrollBar().setValue(int(self.horizontalScrollBar().value() - d.x()))
            self.verticalScrollBar().setValue(int(self.verticalScrollBar().value() - d.y()))
            return
        p = self.mapToScene(e.position().toPoint())
        self.mouseMoved.emit(p.x(), -p.y())
        super().mouseMoveEvent(e)

    def mouseReleaseEvent(self, e):
        if self.tool != "select" and e.button() == Qt.MouseButton.LeftButton:
            if self.tool == "node":
                n = self._node
                if n and n["drag"] is not None:
                    n["drag"] = None
                    self.nodesEdited.emit(n["gi"].item.id)
                return
            d = self._draw
            if d is not None and d["kind"] != "line":
                (x0, y0), (x1, y1) = d["p0"], d["p1"]
                if abs(x1 - x0) > 0.2 and abs(y1 - y0) > 0.2:
                    self.shapeDrawn.emit(d["kind"], dict(d))
                self._cancel_draw()
            return
        if self._drag is not None and e.button() == Qt.MouseButton.LeftButton:
            self._drag = None
            self.update_overlay()
            return
        if self._pan_start is not None and e.button() == getattr(self, "_pan_button", None):
            self._pan_start = None
            self.unsetCursor()
            if e.button() == Qt.MouseButton.RightButton and not self._panned:
                gi = self._graphic_at(e.position().toPoint())
                if gi is not None and not gi.isSelected():
                    self.scene_.clearSelection()
                    gi.setSelected(True)
                elif gi is None:
                    self.scene_.clearSelection()
                self.contextMenuRequested.emit(e.globalPosition().toPoint())
            return
        super().mouseReleaseEvent(e)

    def mouseDoubleClickEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton and self.tool == "line" and self._draw:
            self._finish_line(False)
            return
        if e.button() == Qt.MouseButton.LeftButton and self.tool == "node" and self._node and self._node["gi"]:
            sp = self.mapToScene(e.position().toPoint())
            hit = self._segment_hit(sp.x(), -sp.y())
            if hit is not None:
                pi, idx, (wx, wy) = hit
                self.aboutToModify.emit("Knoten einfügen")
                vp = self._node_detach(pi)
                lx, ly = self._to_local(wx, wy)
                vp.poly.pts = np.insert(vp.poly.pts, idx, [lx, ly], axis=0)
                self._node["sel"] = (pi, idx)
                self._node["gi"].rebuild()
                self._update_nodes()
                self.nodesEdited.emit(self._node["gi"].item.id)
            return
        if self.tool == "select":
            super().mouseDoubleClickEvent(e)

    def _drag_update(self, x, y, mods):
        import math
        from ..core.editing import transform_items
        d = self._drag
        ax, ay = d["anchor"]
        sx, sy = d["start"]
        if d["mode"] == "rot":
            a = math.degrees(math.atan2(y - ay, x - ax) - math.atan2(sy - ay, sx - ax))
            if mods & Qt.KeyboardModifier.ShiftModifier:
                a = round(a / 15) * 15
            transform_items(d["items"], d["orig"], 1.0, a, (ax, ay))
            self.mouseMoved.emit(x, y)
        else:
            v0 = (sx - ax, sy - ay)
            L2 = v0[0] ** 2 + v0[1] ** 2
            if L2 < 1e-12:
                return
            f = ((x - ax) * v0[0] + (y - ay) * v0[1]) / L2       # Projektion auf die Diagonale
            f = max(0.01, f)
            transform_items(d["items"], d["orig"], f, 0.0, (ax, ay))
        for it in d["items"]:
            gi = self.graphics.get(it.id)
            if gi:
                gi.sync_from_model()
        self.update_overlay()
        self.itemsTransformed.emit([it.id for it in d["items"]])

    def _graphic_at(self, pos):
        for it in self.items(pos):
            if isinstance(it, ItemGraphic):
                return it
        return None

    def keyPressEvent(self, e):
        if self.tool == "line" and self._draw:
            if e.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                self._finish_line(False)
                return
            if e.key() == Qt.Key.Key_Escape:
                self._cancel_draw()
                return
        if self.tool != "select" and e.key() == Qt.Key.Key_Escape:
            self.set_tool("select")
            return
        if self.tool == "node" and e.key() in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace):
            self.delete_node()
            return
        step = 10.0 if e.modifiers() & Qt.KeyboardModifier.ShiftModifier else 1.0
        moves = {Qt.Key.Key_Left: (-step, 0), Qt.Key.Key_Right: (step, 0),
                 Qt.Key.Key_Up: (0, step), Qt.Key.Key_Down: (0, -step)}
        if e.key() in moves and self.selected_ids():
            self.aboutToModify.emit("Verschieben (Tastatur)")
            dx, dy = moves[e.key()]
            for i in self.selected_ids():
                gi = self.graphics[i]
                gi.item.x += dx
                gi.item.y += dy
                gi.sync_from_model()
                self.itemMoved.emit(i)
            self.update_overlay()
            return
        if e.key() in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace):
            self.deleteRequested.emit()
            return
        super().keyPressEvent(e)

    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dragMoveEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dropEvent(self, e):
        files = [u.toLocalFile() for u in e.mimeData().urls() if u.isLocalFile()]
        if files:
            self.filesDropped.emit(files)
            e.acceptProposedAction()
