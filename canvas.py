"""The editor stage: shows the composed picture and lets the user drag and resize the product."""
from __future__ import annotations

import math

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen, QPixmap, QRadialGradient
from PySide6.QtWidgets import QFrame, QHBoxLayout, QWidget

from ui_kit import C, button, icon_pixmap


class StageCanvas(QWidget):
    changed = Signal()      # the placement changed while dragging
    committed = Signal()    # a drag ended
    refine = Signal()
    center = Signal()
    flip = Signal()

    def __init__(self):
        super().__init__()
        self.setMinimumSize(480, 420)
        self.setMouseTracking(True)
        self.pix: QPixmap | None = None        # composed preview
        self.plain: QPixmap | None = None      # original photo, shown while there is no cutout
        self.original: QPixmap | None = None   # original photo, shown while Compare is held
        self.art = (1600, 1600)
        self.box = None                        # product box in output pixels
        self.place = None
        self.note = ''
        self.toast = ''
        self.comparing = False
        self.mode = None
        self.start = None
        self.bright = False

        self.bar = QFrame(self)
        self.bar.setObjectName('float')
        row = QHBoxLayout(self.bar)
        row.setContentsMargins(4, 4, 4, 4)
        row.setSpacing(2)
        for name, tip, signal in (('center', 'Center on canvas', self.center),
                                  ('flip', 'Flip horizontally', self.flip),
                                  ('brush', 'Refine edges', self.refine)):
            b = button('', lambda _=False, s=signal: s.emit(), 'ghost', name, tip)
            b.setFixedSize(42, 42)
            row.addWidget(b)
        self.bar.adjustSize()
        self.bar.hide()

    # ------------------------------------------------------------------ content

    def show_frame(self, pixmap: QPixmap, art: tuple, box, place, bright: bool = False):
        self.bright = bright       # a bright backdrop gets a dark frame, a dark one gets the lime frame
        self.pix, self.plain, self.art, self.box, self.place, self.note = pixmap, None, art, box, place, ''
        self.place_bar()
        self.update()

    def show_plain(self, pixmap: QPixmap, note: str):
        self.plain, self.pix, self.box, self.note = pixmap, None, None, note
        self.art = (pixmap.width(), pixmap.height())
        self.place_bar()
        self.update()

    def clear(self):
        self.pix = self.plain = self.box = None
        self.place_bar()
        self.update()

    def set_comparing(self, on: bool):
        self.comparing = on and self.original is not None
        self.place_bar()
        self.update()

    def set_toast(self, text: str):
        self.toast = text
        self.update()

    # ----------------------------------------------------------------- geometry

    def art_rect(self) -> QRectF:
        area = QRectF(self.rect()).adjusted(40, 64, -40, -64)
        w, h = self.art
        k = max(0.01, min(area.width() / w, area.height() / h))
        r = QRectF(0, 0, w * k, h * k)
        r.moveCenter(area.center())
        return r

    def box_rect(self) -> QRectF | None:
        if self.box is None:
            return None
        r = self.art_rect()
        k = r.width() / self.art[0]
        x, y, w, h = self.box
        return QRectF(r.left() + x * k, r.top() + y * k, w * k, h * k)

    def place_bar(self):
        b = self.box_rect()
        if b is None or self.comparing or self.mode:
            self.bar.hide()
            return
        x = b.center().x() - self.bar.width() / 2
        y = b.top() - self.bar.height() - 12
        x = max(8, min(self.width() - self.bar.width() - 8, x))
        self.bar.move(int(x), int(max(8, y)))
        self.bar.show()
        self.bar.raise_()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.place_bar()

    # -------------------------------------------------------------------- mouse

    def _handle_at(self, pos: QPointF) -> int | None:
        """Which corner handle is under the mouse: 0 top-left, 1 top-right, 2 bottom-left, 3 bottom-right."""
        b = self.box_rect()
        if b is None:
            return None
        corners = (b.topLeft(), b.topRight(), b.bottomLeft(), b.bottomRight())
        for index, c in enumerate(corners):
            if math.hypot(pos.x() - c.x(), pos.y() - c.y()) < 12:
                return index
        return None

    def mousePressEvent(self, e):
        b = self.box_rect()
        if e.button() != Qt.MouseButton.LeftButton or b is None or self.comparing:
            return
        pos = e.position()
        if self._handle_at(pos) is not None:
            self.mode, self.start = 'scale', (pos, self.place.scale, b.center())
        elif b.contains(pos):
            self.mode, self.start = 'move', (pos, self.place.x, self.place.y)
        if self.mode:
            self.bar.hide()

    def mouseMoveEvent(self, e):
        pos = e.position()
        if self.mode == 'move':
            start, x0, y0 = self.start
            k = self.art_rect().width() / self.art[0]
            self.place.x = max(-150.0, min(150.0, x0 + (pos.x() - start.x()) / k * 200 / self.art[0]))
            self.place.y = max(-150.0, min(150.0, y0 + (pos.y() - start.y()) / k * 200 / self.art[1]))
            self.changed.emit()
        elif self.mode == 'scale':
            start, scale0, mid = self.start
            d0 = max(1.0, math.hypot(start.x() - mid.x(), start.y() - mid.y()))
            d1 = math.hypot(pos.x() - mid.x(), pos.y() - mid.y())
            self.place.scale = max(5.0, min(160.0, scale0 * d1 / d0))
            self.changed.emit()
        else:
            b = self.box_rect()
            corner = self._handle_at(pos)
            if corner is not None:
                # Top-left and bottom-right lean one way, the other two corners lean the other way.
                self.setCursor(Qt.CursorShape.SizeFDiagCursor if corner in (0, 3) else Qt.CursorShape.SizeBDiagCursor)
            elif b is not None and b.contains(pos):
                self.setCursor(Qt.CursorShape.SizeAllCursor)
            else:
                self.unsetCursor()

    def mouseReleaseEvent(self, _e):
        if self.mode:
            self.mode = None
            self.place_bar()
            self.committed.emit()

    # ------------------------------------------------------------------- paint

    def _chip(self, p: QPainter, text: str, x: float, y: float, icon_name: str | None = None, plain: bool = False):
        font = QFont(self.font())
        font.setPointSizeF(9)
        p.setFont(font)
        width = p.fontMetrics().horizontalAdvance(text) + (46 if icon_name else 24)
        r = QRectF(x, y, width, 32)
        if not plain:
            p.setPen(QPen(QColor(C['line']), 1))
            p.setBrush(QColor(C['panel']))
            p.drawRoundedRect(r, 10, 10)
        if icon_name:
            p.drawPixmap(QPointF(r.left() + 10, r.top() + 8), icon_pixmap(icon_name, C['muted'], 16))
        p.setPen(QColor(C['muted'] if plain else C['soft']))
        p.drawText(r.adjusted(34 if icon_name else 12, 0, -8, 0), Qt.AlignmentFlag.AlignVCenter, text)
        return r

    def paintEvent(self, _event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        glow = QRadialGradient(QPointF(self.width() / 2, self.height() * 0.45), max(self.width(), self.height()) * 0.6)
        glow.setColorAt(0, QColor('#1B1B1F'))
        glow.setColorAt(1, QColor(C['bg']))
        p.fillRect(self.rect(), glow)

        shown = self.original if self.comparing else (self.pix or self.plain)
        if shown is None:
            return
        r = self.art_rect()
        p.setPen(Qt.PenStyle.NoPen)
        for i in range(1, 9):   # a soft drop shadow under the artboard
            p.setBrush(QColor(0, 0, 0, 9))
            p.drawRoundedRect(r.adjusted(-i * 2, -i * 2 + 10, i * 2, i * 2 + 10), 6 + i, 6 + i)
        clip = QPainterPath()
        clip.addRoundedRect(r, 4, 4)
        p.save()
        p.setClipPath(clip)
        if self.comparing:
            p.fillRect(r, QColor('#000000'))
            k = min(r.width() / shown.width(), r.height() / shown.height())
            t = QRectF(0, 0, shown.width() * k, shown.height() * k)
            t.moveCenter(r.center())
            p.drawPixmap(t, shown, QRectF(shown.rect()))
        else:
            p.drawPixmap(r, shown, QRectF(shown.rect()))
            if self.plain is not None:
                p.fillRect(r, QColor(14, 14, 16, 150))
        p.restore()

        if self.plain is not None and self.note and not self.comparing:
            font = QFont(self.font())
            font.setPointSizeF(13)
            font.setBold(True)
            p.setFont(font)
            p.setPen(QColor(C['text']))
            p.drawText(r, Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap, self.note)

        b = self.box_rect()
        if b is not None and not self.comparing:
            # Two strokes, one light and one dark, so the frame shows on any colour.
            line = QColor(C['ink']) if self.bright else QColor(C['lime'])
            halo = QColor(255, 255, 255, 150) if self.bright else QColor(0, 0, 0, 150)
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.setPen(QPen(halo, 3.5))
            p.drawRect(b)
            p.setPen(QPen(line, 1.5))
            p.drawRect(b)
            for c in (b.topLeft(), b.topRight(), b.bottomLeft(), b.bottomRight()):
                p.setPen(QPen(line, 1.5))
                p.setBrush(QColor('#FFFFFF'))
                p.drawRoundedRect(QRectF(c.x() - 5.5, c.y() - 5.5, 11, 11), 3, 3)

        if self.pix is not None:
            label = 'Original' if self.comparing else f'{self.art[0]} × {self.art[1]} px'
            self._chip(p, label, 20, 18)
        self._chip(p, 'Stays on this PC', 12, self.height() - 44, 'lock', plain=True)
        if self.toast:
            font = QFont(self.font())
            font.setPointSizeF(9)
            width = p.fontMetrics().horizontalAdvance(self.toast) + 30
            self._chip(p, self.toast, self.width() - width - 20, self.height() - 50)
