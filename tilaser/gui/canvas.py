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
from ..core.raster import preview_image


def _cosmetic(color: QColor, width: float = 1.0, style=Qt.PenStyle.SolidLine) -> QPen:
    pen = QPen(color, width)
    pen.setCosmetic(True)
    pen.setStyle(style)
    return pen


class ItemGraphic(QGraphicsItem):
    """Darstellung eines Modell-Objekts. Lokale Koordinaten = (x, -y) der Modellpfade."""

    def __init__(self, item: Item, project: Project, on_moved):
        super().__init__()
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
                                    layer.threshold, layer.invert)
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
        if change == QGraphicsItem.GraphicsItemChange.ItemPositionHasChanged and not self._syncing:
            p = self.pos()
            self.item.x, self.item.y = p.x(), -p.y()
            self.on_moved(self.item)
        return super().itemChange(change, value)


class LaserCanvas(QGraphicsView):
    selectionChanged = pyqtSignal(list)         # Liste der Item-IDs
    itemMoved = pyqtSignal(str)
    mouseMoved = pyqtSignal(float, float)
    filesDropped = pyqtSignal(list)
    deleteRequested = pyqtSignal()
    contextMenuRequested = pyqtSignal(object)   # globale Position (QPoint)

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

    def add_item(self, item: Item, select=False):
        gi = ItemGraphic(item, self.project, lambda it: self.itemMoved.emit(it.id))
        self.scene_.addItem(gi)
        self.graphics[item.id] = gi
        gi.setSelected(select)
        return gi

    def remove_item(self, item_id: str):
        gi = self.graphics.pop(item_id, None)
        if gi:
            self.scene_.removeItem(gi)

    def refresh_item(self, item_id: str, geometry=False):
        gi = self.graphics.get(item_id)
        if gi:
            if geometry:
                gi.rebuild()
            gi.sync_from_model()

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
            self.selectionChanged.emit(self.selected_ids())
        except RuntimeError:
            pass

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

    # ------------------------------------------------------------ Maus / Tastatur
    def wheelEvent(self, e):
        f = 1.15 ** (e.angleDelta().y() / 120)
        cur = self.transform().m11()
        if 0.05 < cur * f < 200:
            self.scale(f, f)

    def mousePressEvent(self, e):
        # Mitte: verschieben. Rechts: verschieben beim Ziehen, Kontextmenü beim Klicken.
        if e.button() in (Qt.MouseButton.MiddleButton, Qt.MouseButton.RightButton):
            self._pan_start = e.position()
            self._pan_origin = e.position()
            self._pan_button = e.button()
            self._panned = False
            return
        super().mousePressEvent(e)

    def mouseMoveEvent(self, e):
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

    def _graphic_at(self, pos):
        for it in self.items(pos):
            if isinstance(it, ItemGraphic):
                return it
        return None

    def keyPressEvent(self, e):
        step = 10.0 if e.modifiers() & Qt.KeyboardModifier.ShiftModifier else 1.0
        moves = {Qt.Key.Key_Left: (-step, 0), Qt.Key.Key_Right: (step, 0),
                 Qt.Key.Key_Up: (0, step), Qt.Key.Key_Down: (0, -step)}
        if e.key() in moves and self.selected_ids():
            dx, dy = moves[e.key()]
            for i in self.selected_ids():
                gi = self.graphics[i]
                gi.item.x += dx
                gi.item.y += dy
                gi.sync_from_model()
                self.itemMoved.emit(i)
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
