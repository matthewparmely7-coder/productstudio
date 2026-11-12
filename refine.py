"""Refine edges: a full-window brush mode for fixing the cutout mask.

The mask lives in a QImage that the brush paints on directly. Only the small
rectangle under each stroke is recomposed, so large photos stay responsive.
"""
from __future__ import annotations

import numpy as np
from PIL import Image
from PySide6.QtCore import QPointF, QRect, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QImage, QPainter, QPen, QRadialGradient
from PySide6.QtWidgets import QButtonGroup, QFrame, QHBoxLayout, QLabel, QVBoxLayout, QWidget

from ui_kit import (C, SliderRow, Tile, TitleBar, button, checker, icon_pixmap, label, segmented, to_qimage,
                    window_controls)

CORAL, LIME = QColor(C['coral']), QColor(C['lime'])


class RefineView(QWidget):
    zoomed = Signal(float)

    def __init__(self):
        super().__init__()
        self.setMouseTracking(True)
        self.setMinimumSize(400, 300)
        self.orig = self.dim = self.mask = self.cut = self.initial = None
        self.zoom, self.off = 1.0, QPointF(0, 0)
        self.tool, self.brush, self.soft, self.view = 'erase', 40, False, 'overlay'
        self.undo_stack, self.redo_stack = [], []
        self.last = self.cursor = self.pan = None

    # --------------------------------------------------------------------- data

    def load(self, image: Image.Image, mask: Image.Image):
        rgba = image.convert('RGBA')
        self.orig = to_qimage(rgba).convertToFormat(QImage.Format.Format_ARGB32_Premultiplied)
        # The "removed" look: everything outside the cutout loses its colour and turns
        # flat coral, so it can never be mistaken for the product. Built small, drawn stretched.
        small = rgba.convert('RGB')
        small.thumbnail((1600, 1600))
        grey = small.convert('L').convert('RGB').point(lambda v: 40 + int(v * 0.5))
        self.dim = to_qimage(Image.blend(grey, Image.new('RGB', grey.size, C['coral']), 0.62))
        m = mask.convert('L').resize(rgba.size)
        self.mask = QImage(m.tobytes(), m.width, m.height, m.width, QImage.Format.Format_Alpha8).copy()
        self.initial = self.mask.copy()
        self.cut = QImage(self.orig.size(), QImage.Format.Format_ARGB32_Premultiplied)
        self.undo_stack, self.redo_stack = [], []
        self.recompose(self.orig.rect())
        self.fit()

    def result(self) -> Image.Image:
        m = self.mask
        rows = np.frombuffer(m.constBits(), dtype=np.uint8).reshape(m.height(), m.bytesPerLine())
        return Image.fromarray(rows[:, :m.width()].copy(), 'L')

    def recompose(self, rect: QRect):
        """Rebuild the visible cutout inside `rect` from the original and the mask."""
        rect = rect.intersected(self.orig.rect())
        if rect.isEmpty():
            return
        p = QPainter(self.cut)
        p.setCompositionMode(QPainter.CompositionMode.CompositionMode_Source)
        p.drawImage(rect, self.orig, rect)
        p.setCompositionMode(QPainter.CompositionMode.CompositionMode_DestinationIn)
        p.drawImage(rect, self.mask, rect)
        p.end()

    # --------------------------------------------------------------------- view

    def fit(self):
        if self.orig is None:
            return
        w, h = self.orig.width(), self.orig.height()
        self.zoom = max(0.02, min((self.width() - 48) / w, (self.height() - 48) / h))
        self.off = QPointF((self.width() - w * self.zoom) / 2, (self.height() - h * self.zoom) / 2)
        self.zoomed.emit(self.zoom)
        self.update()

    def zoom_at(self, factor: float, anchor: QPointF | None = None):
        anchor = anchor or QPointF(self.width() / 2, self.height() / 2)
        new = max(0.02, min(16.0, self.zoom * factor))
        point = (anchor - self.off) / self.zoom
        self.zoom = new
        self.off = anchor - point * new
        self.zoomed.emit(self.zoom)
        self.update()

    def to_image(self, pos: QPointF) -> QPointF:
        return (pos - self.off) / self.zoom

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self.orig is not None and not self.undo_stack:
            self.fit()

    # ------------------------------------------------------------------ editing

    def stroke(self, a: QPointF, b: QPointF):
        r = max(0.5, self.brush / self.zoom / 2)
        p = QPainter(self.mask)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        erase = self.tool == 'erase'
        p.setCompositionMode(QPainter.CompositionMode.CompositionMode_DestinationOut if erase
                             else QPainter.CompositionMode.CompositionMode_SourceOver)
        if self.soft:
            p.setPen(Qt.PenStyle.NoPen)
            length = max(1.0, ((b.x() - a.x()) ** 2 + (b.y() - a.y()) ** 2) ** 0.5)
            steps = max(1, int(length / max(1.0, r / 3)))
            for i in range(steps + 1):
                c = a + (b - a) * (i / steps)
                g = QRadialGradient(c, r)
                g.setColorAt(0, QColor(255, 255, 255, 70))
                g.setColorAt(1, QColor(255, 255, 255, 0))
                p.setBrush(g)
                p.drawEllipse(c, r, r)
        else:
            p.setPen(QPen(QColor(255, 255, 255), r * 2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            p.drawLine(a, b)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(255, 255, 255))
            p.drawEllipse(b, r, r)
        p.end()
        dirty = QRectF(a, b).normalized().adjusted(-r - 2, -r - 2, r + 2, r + 2).toAlignedRect()
        self.recompose(dirty)
        self.update()

    def _push_undo(self):
        self.undo_stack.append(self.mask.copy())
        self.undo_stack = self.undo_stack[-10:]
        self.redo_stack.clear()

    def _swap(self, source: list, target: list):
        if source:
            target.append(self.mask)
            self.mask = source.pop()
            self.recompose(self.orig.rect())
            self.update()

    def undo(self):
        self._swap(self.undo_stack, self.redo_stack)

    def redo(self):
        self._swap(self.redo_stack, self.undo_stack)

    def reset(self):
        self._push_undo()
        self.mask = self.initial.copy()
        self.recompose(self.orig.rect())
        self.update()

    # -------------------------------------------------------------------- mouse

    def mousePressEvent(self, e):
        if self.orig is None:
            return
        middle = e.button() == Qt.MouseButton.MiddleButton
        if middle or (e.button() == Qt.MouseButton.LeftButton and self.tool == 'move'):
            self.pan = (e.position(), QPointF(self.off))
        elif e.button() == Qt.MouseButton.LeftButton:
            self._push_undo()
            self.last = self.to_image(e.position())
            self.stroke(self.last, self.last)

    def mouseMoveEvent(self, e):
        self.cursor = e.position()
        if self.pan:
            self.off = self.pan[1] + (e.position() - self.pan[0])
        elif self.last is not None:
            point = self.to_image(e.position())
            self.stroke(self.last, point)
            self.last = point
        self.update()

    def mouseReleaseEvent(self, _e):
        self.last = self.pan = None

    def leaveEvent(self, _e):
        self.cursor = None
        self.update()

    def wheelEvent(self, e):
        self.zoom_at(1.15 ** (e.angleDelta().y() / 120), e.position())

    # -------------------------------------------------------------------- paint

    def paintEvent(self, _event):
        p = QPainter(self)
        p.fillRect(self.rect(), QColor(C['bg']))
        if self.orig is None:
            return
        target = QRectF(self.off.x(), self.off.y(), self.orig.width() * self.zoom, self.orig.height() * self.zoom)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, self.zoom < 1)
        if self.view == 'overlay':
            p.drawImage(target, self.dim, QRectF(self.dim.rect()))
        elif self.view == 'checker':
            checker(p, target, 14)
        else:
            p.fillRect(target, QColor('#000000' if self.view == 'black' else '#FFFFFF'))
        p.drawImage(target, self.cut, QRectF(self.cut.rect()))
        if self.cursor is not None and self.tool != 'move':
            p.setRenderHint(QPainter.RenderHint.Antialiasing)
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.setPen(QPen(QColor(0, 0, 0, 160), 3.5))
            p.drawEllipse(self.cursor, self.brush / 2, self.brush / 2)
            p.setPen(QPen(CORAL if self.tool == 'erase' else LIME, 2))
            p.drawEllipse(self.cursor, self.brush / 2, self.brush / 2)


def _paint_tool(name: str):
    def paint(p: QPainter, r: QRectF):
        c = r.center()
        if name == 'erase':
            p.setPen(QPen(CORAL, 3))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawEllipse(c, 10, 10)
        elif name == 'restore':
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(LIME)
            p.drawEllipse(c, 11, 11)
        else:
            p.drawPixmap(QPointF(c.x() - 12, c.y() - 12), icon_pixmap('move', C['soft'], 24))
    return paint


def _paint_view(name: str):
    def paint(p: QPainter, r: QRectF):
        if name == 'overlay':
            p.fillRect(r, CORAL)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor('#C9803F'))
            p.drawRoundedRect(r.adjusted(r.width() * 0.3, r.height() * 0.3, -r.width() * 0.3, -r.height() * 0.3), 3, 3)
        elif name == 'checker':
            checker(p, r, 7, '#D6D6D2', '#77777D')
        else:
            p.fillRect(r, QColor('#000000' if name == 'black' else '#FFFFFF'))
    return paint


class RefinePage(QWidget):
    done = Signal(object)
    cancelled = Signal()

    def __init__(self):
        super().__init__()
        self.setObjectName('page')
        self.view = RefineView()
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        bar = TitleBar()
        top = QHBoxLayout(bar)
        top.setContentsMargins(16, 0, 8, 0)
        top.setSpacing(10)
        back = button('', self.cancelled.emit, None, 'back', 'Back to editor')
        back.setFixedWidth(44)
        top.addWidget(back)
        top.addWidget(label('Refine edges', 'h2'))
        top.addStretch(1)
        self.view_group = QButtonGroup(self)
        tips = {'overlay': 'Removed area in coral', 'checker': 'Transparent', 'black': 'On black', 'white': 'On white'}
        for name, tip in tips.items():
            t = Tile(size=QSize(42, 42), paint=_paint_view(name), fill=None, tip=tip)
            t.setChecked(name == 'overlay')
            t.clicked.connect(lambda _=False, n=name: self._set('view', n))
            self.view_group.addButton(t)
            top.addWidget(t)
        top.addStretch(1)
        top.addWidget(button('Cancel', self.cancelled.emit))
        top.addWidget(button('Done', lambda: self.done.emit(self.view.result()), 'primary', 'check'))
        top.addWidget(window_controls())
        root.addWidget(bar)

        body = QHBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(0)
        rail = QFrame()
        rail.setObjectName('rail')
        tools = QVBoxLayout(rail)
        tools.setContentsMargins(12, 16, 12, 16)
        tools.setSpacing(8)
        self.tool_group = QButtonGroup(self)
        for name, caption in (('erase', 'Erase'), ('restore', 'Restore'), ('move', 'Move')):
            t = Tile(caption, QSize(68, 60), paint=_paint_tool(name), fill=C['panel'], tip=caption)
            t.setChecked(name == 'erase')
            t.clicked.connect(lambda _=False, n=name: self._set('tool', n))
            self.tool_group.addButton(t)
            tools.addWidget(t)
        tools.addStretch(1)
        body.addWidget(rail)

        stage = QVBoxLayout()
        stage.setContentsMargins(0, 0, 0, 16)
        stage.setSpacing(8)
        stage.addWidget(self.view, 1)
        dock = QFrame()
        dock.setObjectName('float')
        row = QHBoxLayout(dock)
        row.setContentsMargins(14, 8, 14, 8)
        row.setSpacing(12)
        self.size = SliderRow('Brush size', 6, 160, 40)
        self.size.setFixedWidth(200)
        self.size.moved.connect(lambda v: self._set('brush', v))
        row.addWidget(self.size)
        edge, self.edge_buttons = segmented(['Hard', 'Soft'], lambda n: self._set('soft', n == 'Soft'))
        self.edge_buttons['Hard'].setChecked(True)
        edge.setFixedWidth(150)
        row.addWidget(edge)
        for name, tip, action in (('undo', 'Undo', self.view.undo), ('redo', 'Redo', self.view.redo)):
            b = button('', action, 'ghost', name, tip)
            b.setFixedSize(44, 44)
            row.addWidget(b)
        row.addWidget(button('Reset', self.view.reset))
        minus = button('', lambda: self.view.zoom_at(1 / 1.25), 'ghost', 'minus', 'Zoom out')
        plus = button('', lambda: self.view.zoom_at(1.25), 'ghost', 'plus', 'Zoom in')
        self.zoom_label = QLabel('100%')
        self.zoom_label.setFixedWidth(52)
        self.zoom_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        fit = button('Fit', self.view.fit)
        for w in (minus, self.zoom_label, plus, fit):
            if w in (minus, plus):
                w.setFixedSize(44, 44)
            row.addWidget(w)
        self.view.zoomed.connect(lambda z: self.zoom_label.setText(f'{round(z * 100)}%'))
        holder = QHBoxLayout()
        holder.addStretch(1)
        holder.addWidget(dock)
        holder.addStretch(1)
        stage.addLayout(holder)
        body.addLayout(stage, 1)
        root.addLayout(body, 1)

    def _set(self, name: str, value):
        setattr(self.view, name, value)
        self.view.update()

    def load(self, image, mask):
        self.view.load(image, mask)
