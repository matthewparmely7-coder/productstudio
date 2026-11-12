"""ProductStudio: offline product photo editor. Main window and application entry point."""
from __future__ import annotations

import copy
import io
import json
import math
import os
import shutil
import sys
import threading
import time
import traceback
from dataclasses import asdict, replace
from pathlib import Path

from PIL import Image, ImageOps
from PySide6.QtCore import QEasingCurve, QPropertyAnimation, QPoint, QPointF, QRectF, QRunnable, QSize, QStandardPaths, Qt, QThreadPool, QTimer
from PySide6.QtGui import QColor, QConicalGradient, QFont, QIcon, QKeySequence, QPainter, QPainterPath, QPen, QPixmap, QShortcut
from PySide6.QtWidgets import (QApplication, QButtonGroup, QCheckBox, QDialog, QFileDialog, QFrame,
                               QGridLayout, QHBoxLayout, QLabel, QMainWindow, QMenu, QMessageBox, QProgressBar,
                               QScrollArea, QSpinBox, QStackedWidget, QVBoxLayout, QWidget)

import engine as E
from canvas import StageCanvas
from colorpick import make_backdrop_dialog, pick_color
from export_dialog import ExportDialog
from jobs import PHOTO_TYPES, Job, JobControl, Signals, build_name, find_photos, unique_path
from maker import BrandDialog, MakerDialog, mark_thumb
from refine import RefinePage
from ui_kit import (C, QSS, Clickable, SliderRow, Tile, TitleBar, button, checker, icon_pixmap, label, paint_backdrop,
                    paint_layout, paint_shadow, paint_shape, segmented, to_pixmap, window_controls, GlowBar, Toast,
                    fade_in, glide, AnchorPad, RemovableTile, StepButton)

PHOTO_FILTER = 'Images (*.jpg *.jpeg *.png *.webp *.bmp *.tif *.tiff)'
PREVIEW_SIDE = 1100
QUEUE_TITLE = 'Removing backgrounds'
MARK_GROUPS = ('All', 'Text', 'Script', 'Monogram', 'Badge', 'Label', 'Logo', 'Mine')
# Three usual jobs of a watermark, each as one click. The numbers follow common practice for
# product photos: a quiet corner mark for branding, a faint repeat to stop copying, a large
# centre mark for previews.
GOALS = {
    'Brand': ('A quiet mark in the corner', {'layout': 'single', 'mark_anchor': 'br', 'mark_size': 4,
                                             'mark_opacity': 35, 'mark_margin': 4}),
    'Protect': ('A faint repeat over the whole photo, hard to remove', {'layout': 'brick', 'mark_size': 3,
                                                                        'mark_opacity': 14, 'mark_angle': -25,
                                                                        'mark_spacing': 40}),
    'Proof': ('A large mark across the centre, for previews', {'layout': 'diagonal', 'mark_size': 6,
                                                               'mark_opacity': 22, 'mark_angle': -25}),
}


class Backdrop:
    """A backdrop image. The tiny copy for tiles is made at once. The larger copies are
    loaded the first time they are needed: full for export, small for the canvas."""

    def __init__(self, path: str):
        self.path = path
        self._full = self._small = None
        with Image.open(path) as picture:
            picture.draft('RGB', (480, 480))
            self.tiny = ImageOps.exif_transpose(picture).convert('RGBA')
        self.tiny.thumbnail((240, 240))

    @property
    def full(self):
        if self._full is None:
            self._full = E.load_image(self.path)
        return self._full

    @property
    def small(self):
        if self._small is None:
            self._small = self.full.copy()
            self._small.thumbnail((1400, 1400))
        return self._small


class Photo:
    """One photo in the strip.

    Only a thumbnail stays in memory for every photo. The full picture and its mask are
    loaded when needed and dropped again, so a folder of hundreds of photos stays light.
    """
    KEEP = 3                      # how many full pictures stay loaded
    pinned = None                 # the photo on screen is never dropped
    _live: list = []
    _lock = threading.RLock()

    def __init__(self, path: str):
        self.path = path
        self.error = ''
        self.settings = E.Settings()     # this photo's own look: backdrop, shadow, shape, watermark
        self.bg = None                   # its backdrop image, if it uses one
        self.place = E.Placement()
        self.work = threading.Lock()     # only one thread may cut this photo out at a time
        self._image = self._mask = self._mask_png = self._product = self._original = None
        with Image.open(path) as picture:
            turned = picture.getexif().get(0x0112) in (5, 6, 7, 8)     # stored sideways
            self.size = picture.size[::-1] if turned else picture.size
            picture.draft('RGB', (320, 320))    # lets JPEGs decode small and fast
            small = ImageOps.exif_transpose(picture).convert('RGB')
        self.original_thumb = to_pixmap(ImageOps.fit(small, (132, 132)))
        self.thumb = self.original_thumb      # replaced by the edited result once there is a cutout
        self._mini = None

    def _touch(self):
        live = Photo._live
        if self in live:
            live.remove(self)
        live.insert(0, self)
        keep, extra = live[:Photo.KEEP], live[Photo.KEEP:]
        for old in extra:
            if old is Photo.pinned:
                keep.append(old)
            else:
                old._image = old._mask = old._product = old._original = None
        Photo._live = keep

    @property
    def image(self):
        with Photo._lock:
            if self._image is None:
                self._image = E.load_image(self.path)
            self._touch()
            return self._image

    @property
    def has_mask(self) -> bool:
        return self._mask_png is not None

    @property
    def mask(self):
        with Photo._lock:
            if self._mask is None and self._mask_png is not None:
                self._mask = Image.open(io.BytesIO(self._mask_png))
                self._mask.load()
            return self._mask

    @property
    def mask_token(self):
        """Identifies the current mask. Undo keeps these to bring an older mask back."""
        return self._mask_png

    def set_mask(self, mask):
        data = io.BytesIO()
        mask.convert('L').save(data, 'PNG', compress_level=1)
        with Photo._lock:
            self._mask_png, self._mask, self._product = data.getvalue(), mask.convert('L'), None
            self._mini = self._build_mini()
        self.error = ''

    def restore_mask(self, token):
        with Photo._lock:
            self._mask_png, self._mask, self._product, self._mini = token, None, None, None

    def _build_mini(self):
        image, mask = self.image, self.mask
        k = min(1.0, 160 / max(image.size))
        size = (max(1, round(image.width * k)), max(1, round(image.height * k)))
        return E.cutout(image.resize(size, E.LANCZOS), mask.resize(size, E.LANCZOS))

    @property
    def mini(self):
        """A tiny cutout kept for every photo. The strip pictures are drawn from it."""
        with Photo._lock:
            if self._mini is None and self.has_mask:
                self._mini = self._build_mini()
            return self._mini

    def original_pixmap(self):
        if self._original is None:
            small = self.image.copy()
            small.thumbnail((PREVIEW_SIDE, PREVIEW_SIDE))
            self._original = to_pixmap(small)
        return self._original

    def product(self, feather: int, preview: bool):
        """The cutout. Previews use a reduced copy so dragging stays smooth."""
        if not self.has_mask:
            return None
        image, mask = self.image, self.mask
        if not preview:
            return E.cutout(image, mask, feather)
        if self._product is None or self._product[0] != feather:
            k = min(1.0, PREVIEW_SIDE / max(image.size))
            if k < 1:
                size = (round(image.width * k), round(image.height * k))
                image, mask = image.resize(size, E.LANCZOS), mask.resize(size, E.LANCZOS)
            self._product = (feather, E.cutout(image, mask, feather * k))
        return self._product[1]


def get_segmenter(holder: dict):
    if holder.get('segmenter') is None:
        holder['segmenter'] = E.Segmenter()
    return holder['segmenter']


def ensure_cutout(photo: Photo, holder: dict):
    """Remove the background unless it is done already. The lock stops two threads doing it twice."""
    with photo.work:
        if not photo.has_mask:
            photo.set_mask(get_segmenter(holder).mask(photo.image))


class CutWorker(QRunnable):
    """Removes the background of one photo in the background."""

    def __init__(self, photo: Photo, holder: dict):
        super().__init__()
        self.photo, self.holder, self.signals = photo, holder, Signals()

    def run(self):
        try:
            ensure_cutout(self.photo, self.holder)
        except Exception as error:
            self.photo.error = str(error)
        self.signals.touched.emit(self.photo)


def save_picture(picture, path, quality: int = 95):
    path = Path(path)
    if path.suffix.lower() in ('.jpg', '.jpeg'):
        picture.convert('RGB').save(path, 'JPEG', quality=quality, subsampling=0)
    else:
        picture.save(path, 'PNG')


def write_cutout(photo: Photo, path, feather: int):
    """Save the product at the original photo size with a transparent background."""
    picture = photo.image.copy()
    picture.putalpha(E.feathered(photo.mask, feather))
    picture.save(path, 'PNG')


def plural(count: int, word: str) -> str:
    return f'{count} {word}' if count == 1 else f'{count} {word}s'


class JobBar(QFrame):
    """The progress strip under the canvas: what is running, on which photo, how far along,
    roughly how long is left, and the pause and stop buttons."""

    def __init__(self):
        super().__init__()
        self.setObjectName('jobbar')
        self.setStyleSheet(f'QFrame#jobbar {{ background: {C["panel"]}; border-top: 1px solid {C["line"]}; }}')
        self.total, self.started = 1, time.monotonic()
        row = QHBoxLayout(self)
        row.setContentsMargins(16, 10, 16, 10)
        row.setSpacing(14)
        self.thumb = QLabel()
        self.thumb.setFixedSize(44, 44)
        self.thumb.setScaledContents(True)
        self.thumb.setStyleSheet(f'background: {C["raised"]}; border-radius: 8px;')
        row.addWidget(self.thumb)
        words = QVBoxLayout()
        words.setSpacing(1)
        self.title = label('')
        self.title.setStyleSheet('font-weight: 700; font-size: 10.5pt;')
        self.text = label('', 'muted')
        self.text.setStyleSheet(f'color: {C["muted"]}; font-size: 9pt;')
        words.addWidget(self.title)
        words.addWidget(self.text)
        holder = QWidget()
        holder.setLayout(words)
        holder.setFixedWidth(300)
        row.addWidget(holder)
        self.bar = GlowBar()
        row.addWidget(self.bar, 1)
        self.percent = label('0%')
        self.percent.setFixedWidth(62)
        self.percent.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.percent.setStyleSheet(f'color: {C["lime"]}; font-weight: 700; font-size: 15pt;')
        row.addWidget(self.percent)
        self.pause = button('Pause')
        self.stop = button('Stop')
        row.addWidget(self.pause)
        row.addWidget(self.stop)
        self.hide()

    def begin(self, title: str, total: int, pause: bool, stop: bool):
        self.total, self.started = max(1, total), time.monotonic()
        self.title.setText(title)
        self.text.setText('Starting…')
        self.thumb.clear()
        self.bar.paused = False
        self.bar.set(0, self.total, jump=True)
        self.percent.setText('0%')
        self.pause.setText('Pause')
        self.pause.setVisible(pause)
        self.stop.setVisible(stop)
        if not self.isVisible():            # slide up from the bottom edge
            self.setMaximumHeight(0)
            self.show()
            rise = QPropertyAnimation(self, b'maximumHeight', self)
            rise.setDuration(220)
            rise.setEndValue(72)
            rise.setEasingCurve(QEasingCurve.Type.OutCubic)
            rise.start(QPropertyAnimation.DeletionPolicy.DeleteWhenStopped)

    def step(self, done: int, detail: str, pixmap=None):
        self.bar.set(done, self.total)
        self.percent.setText(f'{round(100 * min(done, self.total) / self.total)}%')
        if done >= 2 and done < self.total and not self.bar.paused:
            left = (time.monotonic() - self.started) / done * (self.total - done)
            detail += f' · about {round(left)} s left' if left < 90 else f' · about {round(left / 60)} min left'
        self.text.setText(detail)
        if pixmap is not None:
            self.thumb.setPixmap(pixmap)

    def set_paused(self, on: bool, note: str = ''):
        self.bar.paused = on
        self.pause.setText('Resume' if on else 'Pause')
        if note:
            self.text.setText(note)

    def end(self):
        self.hide()


class PhotoTile(QWidget):
    """One photo in the left strip.

    It shows the edited result, not the original. A lime ring marks the photo on the
    canvas, a check badge marks selected photos, and a dash running round the border
    means the photo is being worked on right now.
    """

    def __init__(self, photo: Photo, window):
        super().__init__()
        self.photo, self.win = photo, window
        self.setFixedSize(68, 68)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip(Path(photo.path).name + '\nCtrl+click to select more, Shift+click for a range')
        self.setAccessibleName(Path(photo.path).name)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(lambda pos: window.tile_menu(photo, self.mapToGlobal(pos)))

    @staticmethod
    def badge_rect() -> QRectF:
        return QRectF(6, 6, 20, 20)

    def enterEvent(self, event):
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self.update()
        super().leaveEvent(event)

    def mouseReleaseEvent(self, e):
        if e.button() != Qt.MouseButton.LeftButton or not self.rect().contains(e.position().toPoint()):
            return
        mods = e.modifiers()
        if self.badge_rect().contains(e.position()) or mods & Qt.KeyboardModifier.ControlModifier:
            self.win.click_photo(self.photo, 'toggle')
        elif mods & Qt.KeyboardModifier.ShiftModifier:
            self.win.click_photo(self.photo, 'range')
        else:
            self.win.click_photo(self.photo, 'only')

    def paintEvent(self, _event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        photo, win = self.photo, self.win
        outer = QRectF(1, 1, 66, 66)
        face = outer.adjusted(4, 4, -4, -4)
        shape = QPainterPath()
        shape.addRoundedRect(face, 9, 9)
        p.save()
        p.setClipPath(shape)
        p.fillPath(shape, QColor('#0A0A0C'))
        pix = photo.thumb
        fit = max if not photo.has_mask else min     # originals fill the tile, results show whole
        k = fit(face.width() / pix.width(), face.height() / pix.height())
        target = QRectF(0, 0, pix.width() * k, pix.height() * k)
        target.moveCenter(face.center())
        p.drawPixmap(target, pix, QRectF(pix.rect()))
        status = win.status_of(photo)
        if not photo.has_mask or status == 'queued':
            p.fillRect(face, QColor(14, 14, 16, 140))
        p.restore()

        p.setBrush(Qt.BrushStyle.NoBrush)
        selected, current = photo in win.selected, photo is win.current
        if current:
            p.setPen(QPen(QColor(C['lime']), 2.4))
            p.drawRoundedRect(outer, 13, 13)
        elif selected:
            p.setPen(QPen(QColor(C['lime']), 1.3))
            p.drawRoundedRect(outer, 13, 13)

        if status == 'working':       # one bright dash travels round the border
            p.setPen(QPen(QColor(0, 0, 0, 170), 4))
            p.drawPath(shape)
            pen = QPen(QColor(C['lime']), 3, Qt.PenStyle.CustomDashLine, Qt.PenCapStyle.RoundCap)
            pen.setDashPattern([18, 58])
            pen.setDashOffset(-win.spin)
            p.setPen(pen)
            p.drawPath(shape)
        elif status == 'queued':
            p.setPen(QPen(QColor(C['muted']), 1.4, Qt.PenStyle.DotLine))
            p.drawPath(shape)

        if status in ('done', 'failed'):
            spot = QRectF(face.right() - 20, face.bottom() - 20, 18, 18)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(C['lime'] if status == 'done' else C['coral']))
            p.drawEllipse(spot)
            p.drawPixmap(QPointF(spot.left() + 3, spot.top() + 3),
                         icon_pixmap('check' if status == 'done' else 'win_close', C['ink'], 12))

        many = len(win.selected) > 1
        badge = self.badge_rect()
        if selected and many:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(C['lime']))
            p.drawEllipse(badge)
            p.drawPixmap(QPointF(badge.left() + 3, badge.top() + 3), icon_pixmap('check', C['ink'], 14))
        elif self.underMouse() or many:
            p.setPen(QPen(QColor(244, 244, 240, 200), 1.4))
            p.setBrush(QColor(14, 14, 16, 150))
            p.drawEllipse(badge.adjusted(1, 1, -1, -1))


def clear_layout(layout):
    """Remove and delete everything inside a layout, including nested layouts."""
    while layout.count():
        item = layout.takeAt(0)
        if item.widget():
            item.widget().deleteLater()
        elif item.layout():
            clear_layout(item.layout())


# ------------------------------------------------------------------ start page

def asset_pixmap(name: str) -> QPixmap | None:
    """A picture from the assets folder, or None when the file is missing."""
    pix = QPixmap(str(E.asset_path('assets/' + name)))
    return None if pix.isNull() else pix


class Hero(QWidget):
    """The moving before and after picture on the start screen.

    Left of the line is the untouched original photo (assets/hero_before.jpg).
    Right of the line is the finished result: new backdrop, shadow and watermark
    (assets/hero_after.jpg). The line glides left and right on its own.
    """

    def __init__(self):
        super().__init__()
        self.setMinimumSize(380, 285)
        self.before = asset_pixmap('hero_before.jpg')
        self.after = asset_pixmap('hero_after.jpg')
        self.seconds = 0.0
        self.timer = QTimer(self)
        self.timer.setInterval(33)
        self.timer.timeout.connect(self.tick)

    def tick(self):
        self.seconds += 0.033
        self.update()

    def showEvent(self, event):
        self.timer.start()
        super().showEvent(event)

    def hideEvent(self, event):
        self.timer.stop()
        super().hideEvent(event)

    @staticmethod
    def _vase(r: QRectF) -> QPainterPath:
        w, h, cx = r.width(), r.height(), r.center().x()
        path = QPainterPath()
        path.addRoundedRect(QRectF(cx - w * 0.16, r.top() + h * 0.36, w * 0.32, h * 0.46), w * 0.07, w * 0.07)
        path.addRoundedRect(QRectF(cx - w * 0.07, r.top() + h * 0.20, w * 0.14, h * 0.20), w * 0.02, w * 0.02)
        return path.simplified()

    def paintEvent(self, _event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        w = min(self.width(), self.height() * 4 / 3)
        r = QRectF(0, 0, w, w * 3 / 4)
        r.moveCenter(QRectF(self.rect()).center())
        clip = QPainterPath()
        clip.addRoundedRect(r, 20, 20)
        p.setClipPath(clip)
        # A slow sine wave: the line eases from 20% to 80% of the width and back in 8 seconds.
        split = r.left() + r.width() * (0.5 + 0.30 * math.sin(self.seconds * 2 * math.pi / 8))
        left = QRectF(r.left(), r.top(), split - r.left(), r.height())
        if self.before is not None and self.after is not None:
            p.drawPixmap(r, self.after, QRectF(self.after.rect()))
            p.save()
            p.setClipRect(left, Qt.ClipOperation.IntersectClip)
            p.drawPixmap(r, self.before, QRectF(self.before.rect()))
            p.restore()
        else:
            p.fillRect(r, QColor('#FF8A6B'))
            p.save()
            p.setClipRect(left, Qt.ClipOperation.IntersectClip)
            p.fillRect(r, QColor('#8EC5FF'))
            p.fillRect(QRectF(r.left(), r.top() + r.height() * 0.48, r.width(), r.height()), QColor('#2F8FB5'))
            p.fillRect(QRectF(r.left(), r.top() + r.height() * 0.74, r.width(), r.height()), QColor('#C9B89A'))
            p.restore()
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor('#C9803F'))
            p.drawPath(self._vase(r))
        font = QFont(self.font())
        font.setPointSizeF(8.5)
        font.setBold(True)
        p.setFont(font)
        for text, chip in (('Original', QRectF(r.left() + 14, r.bottom() - 40, 66, 26)),
                           ('Finished', QRectF(r.right() - 80, r.bottom() - 40, 66, 26))):
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(14, 14, 16, 200))
            p.drawRoundedRect(chip, 8, 8)
            p.setPen(QColor(C['text']))
            p.drawText(chip, Qt.AlignmentFlag.AlignCenter, text)
        p.setPen(Qt.PenStyle.NoPen)
        p.fillRect(QRectF(split - 1.5, r.top(), 3, r.height()), QColor(C['lime']))
        p.setBrush(QColor(C['lime']))
        p.drawEllipse(QPointF(split, r.center().y()), 20, 20)
        p.setClipping(False)
        p.setPen(QPen(QColor(C['ink']), 2.4, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
        y = r.center().y()
        for side in (-1, 1):
            arrow = QPainterPath(QPointF(split + side * 4, y - 5))
            arrow.lineTo(split + side * 9, y)
            arrow.lineTo(split + side * 4, y + 5)
            p.drawPath(arrow)


class DropFrame(QFrame):
    def paintEvent(self, _event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        p.setBrush(QColor('#151517'))
        p.setPen(QPen(QColor(C['dash']), 2, Qt.PenStyle.DashLine))
        p.drawRoundedRect(r, 28, 28)


def step_card(number: int, title: str, hint: str, paint) -> QFrame:
    card = QFrame()
    card.setStyleSheet(f'QFrame#card {{ background: {C["panel"]}; border-radius: 20px; }}')
    card.setObjectName('card')
    row = QHBoxLayout(card)
    row.setContentsMargins(14, 14, 14, 14)
    row.setSpacing(16)
    picture = Tile(size=QSize(104, 104), paint=paint, fill=None)
    picture.setEnabled(False)
    row.addWidget(picture)
    text = QVBoxLayout()
    text.setSpacing(4)
    text.addStretch(1)
    step = label(f'Step {number}')
    step.setStyleSheet(f'color: {C["lime"]}; font-weight: 600; font-size: 9pt;')
    text.addWidget(step)
    text.addWidget(label(title, 'h2'))
    text.addWidget(label(hint, 'muted', wrap=True))
    text.addStretch(1)
    row.addLayout(text, 1)
    return card


def _card_picture(kind: str):
    product = asset_pixmap('product.png')

    def paint(p: QPainter, r: QRectF):
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        if kind == 'cut':
            checker(p, r, max(3, round(r.width() / 11)))
        else:
            p.fillRect(r, QColor('#FF8A6B' if kind == 'scene' else '#F6D860'))
        p.setPen(Qt.PenStyle.NoPen)
        if product is not None:
            k = min(r.width() * 0.78 / product.width(), r.height() * 0.78 / product.height())
            box = QRectF(0, 0, product.width() * k, product.height() * k)
            box.moveCenter(r.center())
            if kind == 'scene':   # a soft floor shadow
                for i in range(5):
                    p.setBrush(QColor(60, 10, 0, 26))
                    p.drawEllipse(QRectF(box.left() + 4 - i, box.bottom() - 7 - i / 2, box.width() - 8 + 2 * i, 10 + i))
            p.drawPixmap(box, product, QRectF(product.rect()))
        else:
            vase = Hero._vase(r.adjusted(0, -6, 0, 6))
            p.setBrush(QColor('#C9803F'))
            p.drawPath(vase)
        if kind == 'brand':
            p.setPen(QColor(20, 20, 16, 120))
            font = p.font()
            font.setBold(True)
            font.setPointSizeF(max(3.0, r.width() * 0.1))
            p.setFont(font)
            p.save()
            p.translate(r.center())
            p.rotate(-25)
            p.drawText(QRectF(-r.width(), -r.height() * 0.15, r.width() * 2, r.height() * 0.3),
                       Qt.AlignmentFlag.AlignCenter, 'YOUR BRAND')
            p.restore()
    return paint


# ----------------------------------------------------------------- main window

class MainWindow(QMainWindow):
    def __init__(self, segmenter=None, data_dir: str | None = None):
        super().__init__()
        self.setWindowTitle('ProductStudio')
        # No Windows title bar: the app draws its own bar with the window buttons.
        self.setWindowFlags(Qt.WindowType.Window | Qt.WindowType.FramelessWindowHint
                            | Qt.WindowType.WindowMinMaxButtonsHint | Qt.WindowType.WindowSystemMenuHint)
        self.resize(1440, 900)
        self.setMinimumSize(1100, 720)
        self.setAcceptDrops(True)

        self.photos: list[Photo] = []
        self.current: Photo | None = None
        self._blank = E.Settings()        # shown by the panels while no photo is open
        self.toasts = []
        self.holder = {'segmenter': segmenter}
        self.selected: list[Photo] = []   # photos picked in the strip; batch actions work on these
        self.strip_tiles = {}
        self.thumb_queue = []
        self.working_photo = None         # the photo being processed right now
        self.job_pending, self.job_done, self.job_failed = set(), set(), set()
        self.queue_done, self.queue_paused = 0, False
        self.spin = 0.0
        self.import_stop = False
        self.cutting = False
        self.importing = False
        self.job = None                   # the running batch job, if any
        self.export_memory = {}           # the export window remembers the last choices
        self.syncing = False
        self.history, self.hpos = [], -1
        base = Path(data_dir or QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppDataLocation))
        self.data_dir = base
        saved_kit = self.load_list('brand.json', [])
        self.kit = dict(E.DEFAULT_KIT, **(saved_kit[0] if saved_kit else {}))    # the user's brand
        self.marks = [m for m in self.load_list('my_marks.json', []) if m.get('kind') in E.MARK_KINDS]
        self.mark_group = 'All'
        self.palette = self.load_list('palette.json', E.DEFAULT_BACKDROPS)   # colours and gradients
        built_in = {(d['w'], d['h']) for d in E.DEFAULT_SHAPES}
        self.shapes = [s for s in self.load_list('shapes.json', [])       # only the user's own sizes
                       if 'w' in s and 'h' in s and (s['w'], s['h']) not in built_in]
        self.backdrop_dir = base / 'backdrops'
        self.backdrops = self.load_backdrops()     # the user's stored backdrop images
        self.wheel_angle = 90.0

        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(self.refresh)
        self.commit_timer = QTimer(self)   # records an undo step once a slider or box stops changing
        self.commit_timer.setSingleShot(True)
        self.commit_timer.setInterval(350)
        self.commit_timer.timeout.connect(self.commit)
        self.wheel_timer = QTimer(self)    # turns the colour wheel on the colour-picker tile
        self.wheel_timer.setInterval(50)
        self.wheel_timer.timeout.connect(self.wheel_step)
        self.wheel_timer.start()
        self.spin_timer = QTimer(self)     # moves the dash round the tile that is being worked on
        self.spin_timer.setInterval(40)
        self.spin_timer.timeout.connect(self.spin_step)
        self.thumb_timer = QTimer(self)    # redraws strip pictures a few at a time
        self.thumb_timer.setInterval(30)
        self.thumb_timer.timeout.connect(self.thumb_step)
        self.thumb_later = QTimer(self)    # waits until a change settles before redrawing every strip picture
        self.thumb_later.setSingleShot(True)
        self.thumb_later.setInterval(280)
        self.thumb_later.timeout.connect(lambda: self.mark_thumbs([self.current] if self.current else []))

        self.pages = QStackedWidget()
        self.setCentralWidget(self.pages)
        self.canvas = StageCanvas()
        self.refine_page = RefinePage()
        self.pages.addWidget(self.build_start())
        self.pages.addWidget(self.build_editor())
        self.pages.addWidget(self.refine_page)

        self.canvas.changed.connect(self.schedule)
        self.canvas.committed.connect(self.commit)
        self.canvas.refine.connect(self.open_refine)
        self.canvas.center.connect(self.center_product)
        self.canvas.flip.connect(self.flip_product)
        self.refine_page.done.connect(self.finish_refine)
        self.refine_page.cancelled.connect(lambda: self.pages.setCurrentIndex(1))
        for keys, action in (('Ctrl+Z', self.key_undo), ('Ctrl+Y', self.key_redo), ('Ctrl+Shift+Z', self.key_redo),
                             ('Ctrl+O', self.choose_photos), ('Ctrl+E', self.open_export),
                             ('Ctrl+A', self.key_select_all), ('Delete', self.key_delete)):
            shortcut = QShortcut(QKeySequence(keys), self)
            shortcut.setContext(Qt.ShortcutContext.WindowShortcut)
            shortcut.activated.connect(action)
        self.pages.currentChanged.connect(lambda i: fade_in(self.pages.widget(i)))
        self.panels.currentChanged.connect(lambda i: fade_in(self.panels.widget(i), 160))
        self.sync_controls()

    # ------------------------------------------------------------------- pages

    # ------------------------------------------------- the look of the current photo

    @property
    def settings(self) -> E.Settings:
        """Every photo has its own look. The panels always edit the photo on the canvas."""
        return self.current.settings if self.current is not None else self._blank

    def _backdrop(self, size: str):
        bg = self.current.bg if self.current is not None else None
        return getattr(bg, size) if bg is not None else None

    @property
    def background(self):
        return self._backdrop('full')

    @property
    def background_small(self):
        return self._backdrop('small')

    def show_current(self):
        """Point the canvas and every panel at the current photo."""
        Photo.pinned = self.current
        self.sync_controls()
        self.update_rail()
        self.refresh()

    # --------------------------------------------------------------- messages

    def say(self, text: str, seconds: float | None = None, kind: str = 'info', detail: str = ''):
        """Show a short message at the top centre. Kinds: info, success, warning, error.
        Messages stay on one line. Anything longer, such as an error's details, goes in `detail`
        and shows when the mouse rests on the message."""
        text = ' '.join(text.split())
        if len(text) > 64:
            text = text[:61].rstrip() + '…'
        for toast in self.toasts:
            if toast.text == text:
                toast.restart()
                return
        toast = Toast(self, text, kind, seconds)
        if detail:
            toast.setToolTip(detail)
        toast.closed.connect(self._toast_closed)
        self.toasts.append(toast)
        if len(self.toasts) > 3:
            self.toasts[0].dismiss()
        toast.show()
        self.place_toasts()
        QTimer.singleShot(0, self.place_toasts)

    def _toast_closed(self, toast):
        if toast in self.toasts:
            self.toasts.remove(toast)
        self.place_toasts()

    def place_toasts(self):
        y = 74
        for toast in self.toasts:
            # Each toast gets its own row under the one before it. They are placed directly,
            # never mid-flight, so two toasts can never sit on top of each other.
            toast.move((self.width() - toast.width()) // 2, y)
            toast.raise_()
            y += max(toast.height(), toast.sizeHint().height()) + 10

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.place_toasts()

    # ------------------------------------------------- one thing at a time

    def blocked(self) -> bool:
        """True, with a message, while photos are being added or a batch job runs.
        Starting a second piece of work on top of the first is never allowed."""
        if self.importing:
            self.say('Still adding photos. Please wait.', kind='warning')
            return True
        if self.job is not None:
            self.say(f'{self.job[2]} is still running.', kind='warning')
            return True
        return False

    def still_cutting(self, photos: list) -> bool:
        """True, with a message, while any of these photos still waits for its background removal."""
        waiting = [ph for ph in photos if not ph.has_mask and not ph.error]
        if not waiting:
            return False
        if self.queue_paused:
            self.say('Background removal is paused. Press Resume.', kind='warning')
        else:
            self.say(f'{plural(len(waiting), "photo")} still processing. Try again soon.', kind='warning')
        return True

    def closeEvent(self, event):
        if self.job is not None:
            box = QMessageBox(self)
            box.setWindowTitle('Work is still running')
            box.setIcon(QMessageBox.Icon.Warning)
            box.setText(f'"{self.job[2]}" has not finished.')
            box.setInformativeText('Photos already saved stay saved. The rest will not be done.')
            yes = box.addButton('Stop and close', QMessageBox.ButtonRole.DestructiveRole)
            box.addButton(QMessageBox.StandardButton.Cancel)
            box.exec()
            if box.clickedButton() is not yes:
                event.ignore()
                return
            self.job[1].stop()
        self.import_stop = True
        event.accept()

    def logo(self) -> QWidget:
        """The brand lockup: the mark plus the two-weight wordmark. Click it to go home."""
        holder = Clickable()
        holder.setCursor(Qt.CursorShape.PointingHandCursor)
        holder.setToolTip('Go to the start screen')
        holder.clicked.connect(self.go_home)
        row = QHBoxLayout(holder)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(11)
        mark = QLabel()
        pix = asset_pixmap('logo.png')
        if pix is not None:
            pix = pix.scaled(60, 60, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
            pix.setDevicePixelRatio(2)
            mark.setPixmap(pix)
        row.addWidget(mark)
        word = QLabel(f'<span style="font-weight:700; color:{C["text"]};">Product</span>'
                      f'<span style="font-weight:300; color:{C["muted"]};">Studio</span>')
        font = QFont()
        font.setFamilies(['Bahnschrift', 'Segoe UI Variable Display', 'Segoe UI'])
        font.setPointSizeF(14.5)
        font.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, -0.2)
        word.setFont(font)
        word.setStyleSheet('font-size: 14.5pt;')
        row.addWidget(word)
        return holder

    def go_home(self):
        """The logo leads back to the start screen. Loaded photos stay loaded."""
        self.back_button.setVisible(bool(self.photos))
        if not self.photos:
            self.job_bar.end()
        self.pages.setCurrentIndex(0)

    def nativeEvent(self, event_type, message):
        """Windows only: let the user resize the frameless window by its edges."""
        if sys.platform == 'win32' and not self.isMaximized():
            try:
                import ctypes
                from ctypes import wintypes
                msg = wintypes.MSG.from_address(int(message))
                if msg.message == 0x0084:   # WM_NCHITTEST: "what is under the mouse?"
                    x = ctypes.c_short(msg.lParam & 0xFFFF).value
                    y = ctypes.c_short((msg.lParam >> 16) & 0xFFFF).value
                    rect = wintypes.RECT()
                    ctypes.windll.user32.GetWindowRect(msg.hWnd, ctypes.byref(rect))
                    edge = max(4, round(6 * self.devicePixelRatioF()))
                    left, right = x - rect.left < edge, rect.right - x <= edge
                    top, bottom = y - rect.top < edge, rect.bottom - y <= edge
                    code = {(True, False, True, False): 13, (False, True, True, False): 14,
                            (True, False, False, True): 16, (False, True, False, True): 17,
                            (True, False, False, False): 10, (False, True, False, False): 11,
                            (False, False, True, False): 12, (False, False, False, True): 15}.get((left, right, top, bottom))
                    if code:
                        return True, code
            except Exception:
                pass
        return super().nativeEvent(event_type, message)

    def build_start(self) -> QWidget:
        page = QWidget()
        page.setObjectName('page')
        root = QVBoxLayout(page)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        bar = TitleBar()
        top = QHBoxLayout(bar)
        top.setContentsMargins(20, 0, 8, 0)
        top.setSpacing(12)
        top.addWidget(self.logo())
        top.addStretch(1)
        self.back_button = button('Back to editor', lambda: self.pages.setCurrentIndex(1))
        self.back_button.hide()
        top.addWidget(self.back_button)
        top.addWidget(window_controls())
        root.addWidget(bar)

        middle = QVBoxLayout()
        middle.setContentsMargins(48, 36, 48, 40)
        middle.setSpacing(24)
        drop = DropFrame()
        drop.setMaximumWidth(1180)
        row = QHBoxLayout(drop)
        row.setContentsMargins(48, 44, 48, 44)
        row.setSpacing(48)
        text = QVBoxLayout()
        text.setSpacing(18)
        text.addStretch(1)
        text.addWidget(label('Drop a product\nphoto', 'h1'))
        text.addWidget(label('The background comes off on this PC.\nNothing is uploaded.', 'lead'))
        picks = QHBoxLayout()
        picks.setSpacing(12)
        choose = button('Choose photos', self.choose_photos, 'primary', 'plus')
        choose.setFixedSize(190, 54)
        folder = button('Choose a folder', self.choose_folder, None, 'image')
        folder.setFixedSize(190, 54)
        picks.addWidget(choose)
        picks.addWidget(folder)
        picks.addStretch(1)
        text.addLayout(picks)
        text.addWidget(label('Or drop photos or a whole folder here.\nJPG · PNG · WebP · BMP · TIFF', 'muted'))
        text.addStretch(1)
        row.addLayout(text, 4)
        row.addWidget(Hero(), 5)
        cards = QHBoxLayout()
        cards.setSpacing(20)
        cards.addWidget(step_card(1, 'Cut out', 'One click, then a brush for edges', _card_picture('cut')))
        cards.addWidget(step_card(2, 'Set the scene', 'Backdrops, shadows, canvas shape', _card_picture('scene')))
        cards.addWidget(step_card(3, 'Brand and export', 'Watermark, then PNG or JPEG', _card_picture('brand')))
        holder = QWidget()
        holder.setMaximumWidth(1180)
        holder.setLayout(cards)
        middle.addWidget(drop, 1, Qt.AlignmentFlag.AlignHCenter)
        middle.addWidget(holder, 0, Qt.AlignmentFlag.AlignHCenter)
        drop.setMinimumWidth(900)
        holder.setMinimumWidth(900)
        root.addLayout(middle, 1)
        return page

    def build_editor(self) -> QWidget:
        page = QWidget()
        page.setObjectName('page')
        root = QVBoxLayout(page)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        bar = TitleBar()
        top = QHBoxLayout(bar)
        top.setContentsMargins(16, 0, 8, 0)
        top.setSpacing(8)
        top.addWidget(self.logo())
        top.addStretch(1)
        self.step_group = QButtonGroup(self)
        steps = (('Cut out', 'cut', 'Remove the background and fix the edges'),
                 ('Scene', 'scene', 'Backdrop, shadow and canvas shape'),
                 ('Brand', 'brand', 'Watermark'))
        for index, (name, kind, tip) in enumerate(steps):
            b = StepButton(name, _card_picture(kind), tip)
            b.clicked.connect(lambda _=False, i=index: self.panels.setCurrentIndex(i))
            b.setChecked(index == 1)
            self.step_group.addButton(b, index)
            top.addWidget(b)
        top.addStretch(1)
        self.undo_button = button('', self.undo, 'ghost', 'undo', 'Undo (Ctrl+Z)')
        self.redo_button = button('', self.redo, 'ghost', 'redo', 'Redo (Ctrl+Y)')
        for b in (self.undo_button, self.redo_button):
            b.setFixedSize(44, 44)
            top.addWidget(b)
        compare = button('Compare', None, None, 'compare', 'Hold to see the original photo')
        compare.pressed.connect(lambda: self.set_comparing(True))
        compare.released.connect(lambda: self.set_comparing(False))
        top.addWidget(compare)
        self.apply_button = button('Apply to all', self.apply_look, None, 'check',
                                   'Copy this photo\'s size and position to the selected photos, and remove their '
                                   'backgrounds. With one photo selected it applies to all.')
        top.addWidget(self.apply_button)
        self.export_button = button('Export', self.open_export, 'primary', 'export')
        top.addWidget(self.export_button)
        top.addWidget(window_controls())
        root.addWidget(bar)

        body = QHBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(0)
        rail = QFrame()
        rail.setObjectName('rail')
        rail.setFixedWidth(92)
        rail_box = QVBoxLayout(rail)
        rail_box.setContentsMargins(0, 12, 0, 10)
        rail_box.setSpacing(8)
        add = Tile(size=QSize(66, 44), pixmap=icon_pixmap('plus', C['lime'], 22), dashed=True, accent=True,
                   tip='Add photos or a folder')
        add.clicked.connect(lambda: self.add_menu(add))
        rail_box.addWidget(add, 0, Qt.AlignmentFlag.AlignHCenter)
        strip_area = QScrollArea()
        strip_area.setWidgetResizable(True)
        strip_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        strip_holder = QWidget()
        self.strip = QVBoxLayout(strip_holder)
        self.strip.setContentsMargins(12, 2, 12, 6)
        self.strip.setSpacing(8)
        self.strip.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignHCenter)
        strip_area.setWidget(strip_holder)
        self.strip_area = strip_area
        rail_box.addWidget(strip_area, 1)
        self.count_label = label('', 'muted')
        self.count_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.count_label.setStyleSheet(f'color: {C["muted"]}; font-size: 8pt;')
        rail_box.addWidget(self.count_label)
        tools = QHBoxLayout()
        tools.setContentsMargins(10, 0, 10, 0)
        tools.setSpacing(4)
        self.all_button = button('', self.toggle_select_all, 'ghost', 'checkall', 'Select all photos (Ctrl+A)')
        self.trash_button = button('', self.remove_selected, 'ghost', 'trash', 'Remove the selected photos (Delete)')
        for b in (self.all_button, self.trash_button):
            b.setFixedSize(34, 34)
            tools.addWidget(b)
        rail_box.addLayout(tools)
        self.clear_button = button('Clear', self.clear_all, None, None, 'Remove every photo from the list')
        self.clear_button.setFixedSize(72, 30)
        self.clear_button.setStyleSheet('min-height: 28px; padding: 0; font-size: 8.5pt; border-radius: 9px;')
        rail_box.addWidget(self.clear_button, 0, Qt.AlignmentFlag.AlignHCenter)
        body.addWidget(rail)
        middle = QVBoxLayout()
        middle.setContentsMargins(0, 0, 0, 0)
        middle.setSpacing(0)
        middle.addWidget(self.canvas, 1)
        self.job_bar = JobBar()
        self.job_bar.pause.clicked.connect(self.toggle_pause)
        self.job_bar.stop.clicked.connect(self.stop_job)
        middle.addWidget(self.job_bar)
        body.addLayout(middle, 1)

        side = QFrame()
        side.setObjectName('side')
        side.setFixedWidth(348)
        side_box = QVBoxLayout(side)
        side_box.setContentsMargins(0, 0, 0, 0)
        self.panels = QStackedWidget()
        for build in (self.build_cut_panel, self.build_scene_panel, self.build_brand_panel):
            area = QScrollArea()
            area.setWidgetResizable(True)
            area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            inner = QWidget()
            box = QVBoxLayout(inner)
            box.setContentsMargins(20, 20, 20, 20)
            box.setSpacing(12)
            build(box)
            box.addStretch(1)
            area.setWidget(inner)
            self.panels.addWidget(area)
        self.panels.setCurrentIndex(1)
        side_box.addWidget(self.panels, 1)
        body.addWidget(side)
        root.addLayout(body, 1)
        return page

    # ------------------------------------------------------------------ panels

    def tile_grid(self, box, tiles: list, columns: int, spacing: int = 12) -> QButtonGroup:
        grid = QGridLayout()
        grid.setSpacing(spacing)
        grid.setContentsMargins(0, 0, 0, 0)
        group = QButtonGroup(self)
        for i, tile in enumerate(tiles):
            grid.addWidget(tile, i // columns, i % columns)
            if tile.isCheckable():
                group.addButton(tile)
        grid.setColumnStretch(columns, 1)
        box.addLayout(grid)
        return group

    def build_cut_panel(self, box):
        box.addWidget(label('Cutout', 'h2'))
        self.cut_status = label('Open a photo to begin.', 'muted', wrap=True)
        box.addWidget(self.cut_status)
        self.feather = SliderRow('Edge softness', 0, 15, 0)
        self.feather.moved.connect(lambda v: self.set_value('feather', v))
        self.feather.released.connect(self.commit)
        box.addWidget(self.feather)
        box.addSpacing(6)
        self.refine_button = button('Refine edges', self.open_refine, None, 'brush')
        box.addWidget(self.refine_button)
        self.again_button = button('Remove background again', self.cut_again)
        box.addWidget(self.again_button)
        self.cutout_button = button('Save cutout as PNG', self.save_cutout, None, 'export')
        box.addWidget(self.cutout_button)

    def build_scene_panel(self, box):
        box.addWidget(label('Backdrop', 'h2'))
        self.backdrops_holder = QVBoxLayout()
        self.backdrops_holder.setContentsMargins(0, 0, 0, 0)
        box.addLayout(self.backdrops_holder)
        self.rebuild_backdrops()
        self.fit_row, self.fit_buttons = segmented(['Cover', 'Contain'],
                                                   lambda n: self.set_value('background_mode', n, True))
        box.addWidget(self.fit_row)

        box.addSpacing(8)
        box.addWidget(label('Shadow', 'h2'))
        self.shadow_tiles = {}
        for name in E.SHADOWS:
            t = Tile(name.capitalize(), QSize(66, 66), paint=paint_shadow(name))
            t.clicked.connect(lambda _=False, n=name: self.set_value('shadow', n, True))
            self.shadow_tiles[name] = t
        self.tile_grid(box, list(self.shadow_tiles.values()), 4)
        # Two sliders are enough: how dark, and which way it falls. They hide when there is no shadow.
        self.shadow_more = QWidget()
        grid = QGridLayout(self.shadow_more)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(16)
        self.shadow_sliders = {}
        for i, (key, caption, low, high) in enumerate((('shadow_strength', 'Strength', 0, 200),
                                                       ('shadow_angle', 'Direction', -180, 180))):
            row = SliderRow(caption, low, high, getattr(self.settings, key))
            row.moved.connect(lambda v, k=key: self.set_value(k, v))
            row.released.connect(self.commit)
            self.shadow_sliders[key] = row
            grid.addWidget(row, 0, i)
        box.addWidget(self.shadow_more)

        box.addSpacing(8)
        box.addWidget(label('Shape', 'h2'))
        self.shapes_holder = QVBoxLayout()
        self.shapes_holder.setContentsMargins(0, 0, 0, 0)
        box.addLayout(self.shapes_holder)
        row = QHBoxLayout()
        row.setSpacing(8)
        self.width_box, self.height_box = QSpinBox(), QSpinBox()
        for spin, name in ((self.width_box, 'Width in pixels'), (self.height_box, 'Height in pixels')):
            spin.setRange(64, 6000)
            spin.setSingleStep(100)
            spin.setAccessibleName(name)
            spin.setToolTip(name)
            spin.editingFinished.connect(lambda: self.set_size(self.width_box.value(), self.height_box.value()))
        row.addWidget(self.width_box, 1)
        row.addWidget(label('×', 'muted'))
        row.addWidget(self.height_box, 1)
        row.addWidget(label('px', 'muted'))
        box.addLayout(row)
        self.rebuild_shapes()

    def rebuild_shapes(self):
        """The shape tiles: the three built-in shapes, Original, then the user's saved sizes.
        Only the saved sizes can be removed."""
        clear_layout(self.shapes_holder)
        self.shape_tiles = []
        for shape in E.DEFAULT_SHAPES:
            t = Tile(shape['name'], QSize(92, 60), paint=paint_shape(shape['w'], shape['h']), fill=C['panel'],
                     tip=f'{shape["w"]} × {shape["h"]} px')
            t.clicked.connect(lambda _=False, s=shape: self.set_size(s['w'], s['h']))
            self.shape_tiles.append(((shape['w'], shape['h']), t))
        original = Tile('Original', QSize(92, 60), pixmap=icon_pixmap('image', C['soft'], 24), fill=C['panel'],
                        tip='Keep each photo at its own size')
        original.clicked.connect(self.use_original_size)
        self.shape_tiles.append(('original', original))
        for shape in self.shapes:
            t = RemovableTile(shape['name'], QSize(92, 60), paint=paint_shape(shape['w'], shape['h']), fill=C['panel'],
                              tip=f'{shape["w"]} × {shape["h"]} px')
            t.clicked.connect(lambda _=False, s=shape: self.set_size(s['w'], s['h']))
            t.removed.connect(lambda s=shape: self.remove_shape(s))
            self.shape_tiles.append(((shape['w'], shape['h']), t))
        save = Tile('Save size', QSize(92, 60), pixmap=icon_pixmap('plus', C['muted'], 22), dashed=True,
                    tip='Save the width and height below as a new shape')
        save.clicked.connect(self.save_shape)
        holder = QVBoxLayout()
        holder.setContentsMargins(0, 0, 0, 0)
        self.shape_group = self.tile_grid(holder, [t for _k, t in self.shape_tiles] + [save], 3)
        self.shapes_holder.addLayout(holder)

    def build_brand_panel(self, box):
        # 1. The brand kit: typed once, used by every watermark style.
        card = QFrame()
        card.setObjectName('kit')
        card.setStyleSheet(f'QFrame#kit {{ background: {C["panel"]}; border-radius: 14px; }}')
        row = QHBoxLayout(card)
        row.setContentsMargins(14, 10, 10, 10)
        words = QVBoxLayout()
        words.setSpacing(1)
        self.kit_name = label('')
        self.kit_name.setStyleSheet('font-weight: 700; font-size: 11pt;')
        self.kit_line = label('', 'muted')
        self.kit_line.setStyleSheet(f'color: {C["muted"]}; font-size: 8.5pt;')
        words.addWidget(self.kit_name)
        words.addWidget(self.kit_line)
        row.addLayout(words, 1)
        row.addWidget(button('Edit', self.edit_kit, None, 'brush', 'Your brand name, tagline, handle and logo'))
        box.addWidget(card)

        # 2. The style gallery.
        head = QHBoxLayout()
        head.addWidget(label('Watermark', 'h2'))
        head.addStretch(1)
        head.addWidget(button('Customize', self.customize_mark, None, None,
                              'Change the words, font, spacing or symbol of this style'))
        box.addLayout(head)
        chips = QGridLayout()
        chips.setSpacing(6)
        self.group_buttons = {}
        self.group_choice = QButtonGroup(self)
        for i, name in enumerate(MARK_GROUPS):
            chip = button(name, lambda _=False, n=name: self.set_mark_group(n), 'chip')
            chip.setCheckable(True)
            chip.setChecked(name == self.mark_group)
            self.group_choice.addButton(chip)
            self.group_buttons[name] = chip
            chips.addWidget(chip, i // 4, i % 4)
        box.addLayout(chips)
        self.marks_holder = QVBoxLayout()
        self.marks_holder.setContentsMargins(0, 0, 0, 0)
        box.addLayout(self.marks_holder)
        self.rebuild_marks()

        # 3. One click for the three usual jobs of a watermark.
        box.addSpacing(8)
        box.addWidget(label('Goal', 'h2'))
        goals = QHBoxLayout()
        goals.setSpacing(8)
        for name, (tip, _values) in GOALS.items():
            goals.addWidget(button(name, lambda _=False, n=name: self.apply_goal(n), None, None, tip))
        box.addLayout(goals)

        box.addSpacing(8)
        box.addWidget(label('Placement', 'h2'))
        self.layout_tiles = {}
        for name in E.LAYOUTS:
            t = Tile(name.capitalize(), QSize(92, 60), paint=paint_layout(name))
            t.clicked.connect(lambda _=False, n=name: self.set_value('layout', n, True))
            self.layout_tiles[name] = t
        self.tile_grid(box, list(self.layout_tiles.values()), 3)
        self.anchor_row = QWidget()
        spot = QHBoxLayout(self.anchor_row)
        spot.setContentsMargins(0, 4, 0, 0)
        spot.setSpacing(14)
        self.anchor_pad = AnchorPad()
        self.anchor_pad.picked.connect(lambda code: self.set_value('mark_anchor', code, True))
        spot.addWidget(self.anchor_pad)
        self.margin_slider = SliderRow('Edge margin', 0, 20, self.settings.mark_margin)
        self.margin_slider.moved.connect(lambda v: self.set_value('mark_margin', v))
        self.margin_slider.released.connect(self.commit)
        spot.addWidget(self.margin_slider, 1)
        box.addWidget(self.anchor_row)

        box.addSpacing(8)
        box.addWidget(label('Look', 'h2'))
        self.look_tiles = {}
        for name in E.LOOKS:
            pix = to_pixmap(E.mark_image({'kind': 'wordmark', 'text': 'Aa', 'case': 'title', 'tracking': 0}, 22,
                                         (58, 58, 54), name))
            t = Tile(name.capitalize(), QSize(52, 46), pixmap=pix)
            t.clicked.connect(lambda _=False, n=name: self.set_value('look', n, True))
            self.look_tiles[name] = t
        self.tile_grid(box, list(self.look_tiles.values()), 5, spacing=10)

        box.addSpacing(8)
        box.addWidget(label('Color', 'h2'))
        self.mark_color_tiles = {}
        colors = []
        for key, tip, fill in ((None, 'Automatic: white on dark photos, dark on light ones', None),
                               ('#FFFFFF', 'White', '#FFFFFF'), ('#141410', 'Black', '#141410'),
                               ('custom', 'Pick any color', None)):
            paint = self.paint_auto_color if key is None else (self.paint_mark_wheel if key == 'custom' else None)
            t = Tile(size=QSize(46, 46), fill=fill, paint=paint, tip=tip)
            t.clicked.connect(lambda _=False, k=key: self.set_mark_color(k))
            self.mark_color_tiles[key] = t
            colors.append(t)
        self.tile_grid(box, colors, 5, spacing=10)

        box.addSpacing(8)
        grid = QGridLayout()
        grid.setHorizontalSpacing(16)
        grid.setVerticalSpacing(10)
        self.mark_sliders = {'mark_margin': self.margin_slider}
        for i, (key, caption, low, high) in enumerate((('mark_size', 'Size', 2, 25), ('mark_opacity', 'Strength', 5, 100),
                                                       ('mark_angle', 'Angle', -90, 90), ('mark_spacing', 'Spacing', 0, 100))):
            row = SliderRow(caption, low, high, getattr(self.settings, key))
            row.moved.connect(lambda v, k=key: self.set_value(k, v))
            row.released.connect(self.commit)
            self.mark_sliders[key] = row
            grid.addWidget(row, i // 2, i % 2)
        box.addLayout(grid)
        self.show_kit()

    @staticmethod
    def paint_auto_color(p: QPainter, r: QRectF):
        p.fillRect(r, QColor('#FFFFFF'))
        p.fillRect(QRectF(r.left(), r.top(), r.width() / 2, r.height()), QColor('#141410'))

    def paint_mark_wheel(self, p: QPainter, r: QRectF):
        chosen = self.settings.mark_color
        if chosen and chosen.upper() not in ('#FFFFFF', '#141410'):
            p.fillRect(r, QColor(chosen))
            return
        wheel = QConicalGradient(r.center(), 90)
        for i in range(13):
            wheel.setColorAt(i / 12, QColor.fromHsvF((i / 12) % 1.0 if i < 12 else 0.0, 0.85, 1.0))
        p.fillRect(r, wheel)

    def rebuild_marks(self):
        """The gallery for the chosen group. Each tile is drawn with the user's own brand name."""
        clear_layout(self.marks_holder)
        none = Tile('None', QSize(92, 62), pixmap=icon_pixmap('none', C['tileink'], 26), tip='No watermark')
        none.clicked.connect(lambda: self.pick_mark(None))
        self.mark_tiles = [(lambda spec: not spec, none)]
        extra = []
        if self.mark_group == 'Mine':
            for saved in self.marks:
                t = RemovableTile(self._short(saved.get('name', 'Mine')), QSize(92, 62), pixmap=self._thumb(saved),
                                  tip=saved.get('name', 'My watermark'),
                                  menu_actions=[('Edit', lambda s=saved: self.edit_mine(s))])
                t.clicked.connect(lambda _=False, s=saved: self.pick_mark(dict(s)))
                t.removed.connect(lambda s=saved: self.remove_mine(s))
                self.mark_tiles.append((lambda spec, s=saved: spec == s, t))
            upload = Tile('Upload', QSize(92, 62), pixmap=icon_pixmap('upload', C['muted'], 24), dashed=True,
                          tip='Use your own logo or pattern image')
            upload.clicked.connect(self.upload_mark)
            create = Tile('Create', QSize(92, 62), pixmap=icon_pixmap('plus', C['lime'], 24), dashed=True, accent=True,
                          tip='Make your own from any style')
            create.clicked.connect(self.customize_mark)
            extra = [upload, create]
        else:
            for ident, name, group in E.TEMPLATES:
                if self.mark_group not in ('All', group):
                    continue
                t = Tile(name, QSize(92, 62), pixmap=self._thumb(E.template_spec(ident, self.kit)), tip=f'{group}: {name}')
                t.clicked.connect(lambda _=False, i=ident: self.pick_mark(E.template_spec(i, self.kit)))
                self.mark_tiles.append((lambda spec, i=ident: bool(spec) and bool(spec.get('from_kit'))
                                        and E.OLD_KINDS.get(spec.get('kind'), spec.get('kind')) == i, t))
        holder = QVBoxLayout()
        holder.setContentsMargins(0, 0, 0, 0)
        self.marks_group = self.tile_grid(holder, [t for _m, t in self.mark_tiles] + extra, 3)
        self.marks_holder.addLayout(holder)

    @staticmethod
    def _short(name: str) -> str:
        return name if len(name) <= 13 else name[:12] + '…'

    @staticmethod
    def _thumb(spec: dict):
        try:
            return mark_thumb(spec, 26)
        except Exception:
            return icon_pixmap('image', C['tileink'], 26)

    def rebuild_backdrops(self):
        """The backdrop grid: your colours and gradients, your stored images, then the two add tiles.
        Every colour and image can be removed. Right-click one to bring the starter set back."""
        clear_layout(self.backdrops_holder)
        self.swatches, self.swatch_tiles, self.image_tiles = [], {}, {}
        restore = [('Restore the starter backdrops', self.restore_palette)]
        tiles = []
        for spec in self.palette:
            t = RemovableTile(size=QSize(66, 66), paint=paint_backdrop(spec), fill=None,
                              tip=spec.get('name', 'Backdrop'), menu_actions=restore)
            t.clicked.connect(lambda _=False, s=spec: self.set_value('backdrop', dict(s), True))
            t.removed.connect(lambda s=spec: self.remove_swatch(s))
            self.swatches.append((spec, t))
            self.swatch_tiles[spec.get('name', '')] = t
            tiles.append(t)
        # The colour maker sits at the end of the colours, before the images.
        self.wheel_tile = Tile(size=QSize(66, 66), paint=self.paint_wheel, fill=None,
                               tip='Make a new backdrop: a color, a linear gradient or a radial gradient')
        self.wheel_tile.setCheckable(False)
        self.wheel_tile.clicked.connect(self.create_backdrop)
        tiles.append(self.wheel_tile)
        for backdrop in self.backdrops:
            pix = to_pixmap(ImageOps.fit(backdrop.tiny.convert('RGB'), (132, 132)))
            t = RemovableTile(size=QSize(66, 66), paint=lambda p, r, px=pix: p.drawPixmap(r, px, QRectF(px.rect())),
                              fill=None, tip=Path(backdrop.path).name)
            t.clicked.connect(lambda _=False, b=backdrop: self.use_backdrop(b))
            t.removed.connect(lambda b=backdrop: self.remove_backdrop(b))
            self.image_tiles[backdrop.path] = t
            tiles.append(t)
        add = Tile(size=QSize(66, 66), pixmap=icon_pixmap('image', C['muted'], 24), dashed=True,
                   tip='Add your own backdrop image. It is kept for next time.')
        add.clicked.connect(self.add_backdrop_image)
        holder = QVBoxLayout()
        holder.setContentsMargins(0, 0, 0, 0)
        self.backdrop_group = self.tile_grid(holder, tiles + [add], 4)
        self.backdrops_holder.addLayout(holder)

    def paint_wheel(self, p: QPainter, r: QRectF):
        """The new-backdrop tile: a slowly turning colour wheel with the picker icon."""
        wheel = QConicalGradient(r.center(), self.wheel_angle)
        for i in range(13):
            wheel.setColorAt(i / 12, QColor.fromHsvF((i / 12) % 1.0 if i < 12 else 0.0, 0.85, 1.0))
        p.fillRect(r, wheel)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(20, 22, 16, 235))
        p.drawEllipse(r.center(), 15, 15)
        p.drawPixmap(QPointF(r.center().x() - 9, r.center().y() - 9), icon_pixmap('palette', '#FFFFFF', 18))

    def wheel_step(self):
        tile = getattr(self, 'wheel_tile', None)
        try:
            if tile is not None and tile.isVisible():
                self.wheel_angle = (self.wheel_angle + 1.2) % 360
                tile.update()
        except RuntimeError:        # the tile was rebuilt a moment ago
            pass

    # ---------------------------------------------------------- state changes

    def set_value(self, key: str, value, commit: bool = False):
        if self.syncing:
            return
        setattr(self.settings, key, value)
        if key == 'backdrop':
            self.sync_controls()
        if key == 'shadow':
            self.shadow_more.setVisible(value != 'none')
        if key == 'layout':
            self.anchor_row.setVisible(value in ('single', 'band'))
        self.schedule()
        if commit:
            self.commit()
        else:
            self.commit_timer.start()

    def set_size(self, w: int, h: int):
        s = self.settings
        if self.syncing or ((w, h) == (s.width, s.height) and s.size_mode == 'fixed'):
            return
        s.size_mode = 'fixed'
        s.width, s.height = w, h
        self.sync_controls()
        self.schedule()
        self.commit()

    # ------------------------------------------------- lists the user can edit

    def load_list(self, name: str, default: list) -> list:
        try:
            data = json.loads((self.data_dir / name).read_text(encoding='utf-8'))
            if isinstance(data, list) and all(isinstance(item, dict) for item in data):
                return data
        except (OSError, ValueError):
            pass
        return copy.deepcopy(default)

    def save_list(self, name: str, data: list):
        try:
            self.data_dir.mkdir(parents=True, exist_ok=True)
            (self.data_dir / name).write_text(json.dumps(data, indent=2), encoding='utf-8')
        except OSError as error:
            self.say('Could not save your change.', kind='error', detail=str(error))

    def create_backdrop(self):
        """Open the backdrop maker. The canvas follows live while the user is choosing."""
        photo = self.current
        before = copy.deepcopy(photo.settings.backdrop) if photo is not None else None

        def live(spec: dict):
            if photo is not None:
                photo.settings.backdrop = dict(spec)
                self.schedule()

        spec = make_backdrop_dialog(self, before, live)
        if spec is None:
            if photo is not None:
                photo.settings.backdrop = before
                self.schedule()
            self.sync_controls()
            return
        if not any(E.same_backdrop(spec, known) for known in self.palette):
            self.palette.append(spec)
            self.save_list('palette.json', self.palette)
            self.rebuild_backdrops()
        if photo is not None:
            photo.settings.backdrop = before        # so the change is recorded as one undo step
            self.set_value('backdrop', dict(spec), True)
        else:
            self.sync_controls()

    def remove_swatch(self, spec: dict):
        self.palette = [known for known in self.palette if known is not spec]
        self.save_list('palette.json', self.palette)
        self.rebuild_backdrops()
        self.sync_controls()
        self.say('Backdrop removed.', kind='success')

    def restore_palette(self):
        missing = [d for d in E.DEFAULT_BACKDROPS if not any(E.same_backdrop(d, known) for known in self.palette)]
        self.palette += copy.deepcopy(missing)
        self.save_list('palette.json', self.palette)
        self.rebuild_backdrops()
        self.sync_controls()
        self.say('Starter backdrops restored.' if missing else 'Nothing to restore.',
                 kind='success' if missing else 'info')

    # ----------------------------------------------------------------- shapes

    @staticmethod
    def fit_size(size: tuple) -> tuple:
        """A photo's own size, scaled down only if it is larger than the 6000 pixel limit."""
        w, h = size
        k = min(1.0, 6000 / max(w, h))
        return max(64, round(w * k)), max(64, round(h * k))

    def use_original_size(self):
        photo = self.current
        if photo is None:
            self.sync_controls()
            return
        s = photo.settings
        s.size_mode = 'original'
        s.width, s.height = self.fit_size(photo.size)
        self.sync_controls()
        self.schedule()
        self.commit()

    def save_shape(self):
        w, h = self.width_box.value(), self.height_box.value()
        if any((shape['w'], shape['h']) == (w, h) for shape in E.DEFAULT_SHAPES + self.shapes):
            self.say('That size is already saved.')
            return
        self.shapes.append({'name': f'{w} × {h}', 'w': w, 'h': h})
        self.save_list('shapes.json', self.shapes)
        self.rebuild_shapes()
        self.sync_controls()
        self.say(f'Saved {w} × {h}.', kind='success')

    def remove_shape(self, shape: dict):
        self.shapes = [known for known in self.shapes if known is not shape]
        self.save_list('shapes.json', self.shapes)
        self.rebuild_shapes()
        self.sync_controls()
        self.say('Shape removed.', kind='success')

    # ----------------------------------------------------- stored backdrop images

    def load_backdrops(self) -> list:
        """Backdrop images added earlier are kept in the app's own data folder."""
        found = []
        if self.backdrop_dir.is_dir():
            for path in sorted(self.backdrop_dir.iterdir(), key=lambda p: p.stat().st_mtime):
                if path.suffix.lower() in PHOTO_TYPES:
                    try:
                        found.append(Backdrop(str(path)))
                    except Exception:
                        continue
        return found

    def add_backdrop_image(self):
        paths, _ = QFileDialog.getOpenFileNames(self, 'Add backdrop images', '', PHOTO_FILTER)
        added = None
        for source in paths:
            try:
                self.backdrop_dir.mkdir(parents=True, exist_ok=True)
                target, n = self.backdrop_dir / Path(source).name, 2
                while target.exists():
                    target = self.backdrop_dir / f'{Path(source).stem} ({n}){Path(source).suffix}'
                    n += 1
                shutil.copyfile(source, target)       # our own copy, so it survives if the original moves
                added = Backdrop(str(target))
                self.backdrops.append(added)
            except Exception as error:
                self.say('Could not add that image.', kind='error', detail=f'{Path(source).name}: {error}')
        if added is not None:
            self.rebuild_backdrops()
            self.use_backdrop(added)
            if len(paths) > 1:
                self.say(f'Added {len(paths)} backdrops.', kind='success')
        else:
            self.sync_controls()

    def use_backdrop(self, backdrop: Backdrop):
        if self.current is None:
            self.sync_controls()
            return
        self.current.bg = backdrop
        self.set_value('backdrop', {'type': 'image'}, True)

    def remove_backdrop(self, backdrop: Backdrop):
        """Take a stored image out of the list. Built-in colours cannot be removed."""
        box = QMessageBox(self)
        box.setWindowTitle('Remove backdrop')
        box.setIcon(QMessageBox.Icon.Question)
        box.setText('Remove this backdrop image from the list?')
        box.setInformativeText('Photos that already use it keep it until you change them. '
                               'Your original image file is not touched.')
        yes = box.addButton('Remove', QMessageBox.ButtonRole.DestructiveRole)
        box.addButton(QMessageBox.StandardButton.Cancel)
        box.exec()
        if box.clickedButton() is yes:
            self.drop_backdrop(backdrop)

    def drop_backdrop(self, backdrop: Backdrop):
        if backdrop in self.backdrops:
            self.backdrops.remove(backdrop)
        try:
            os.remove(backdrop.path)           # only our stored copy, never the user's file
        except OSError:
            pass
        self.rebuild_backdrops()
        self.sync_controls()
        self.say('Backdrop removed.', kind='success')

    def center_product(self):
        if self.current:
            self.current.place.x = self.current.place.y = 0.0
            self.schedule()
            self.commit()

    def flip_product(self):
        if self.current:
            self.current.place.flip = not self.current.place.flip
            self.schedule()
            self.commit()

    def set_comparing(self, on: bool):
        if self.current:
            self.canvas.original = self.current.original_pixmap()
            self.canvas.set_comparing(on)

    # ---------------------------------------------------------------- history

    def snapshot(self):
        """Everything undo can bring back, for every photo: look, position, cutout and backdrop image."""
        return [[ph, replace(ph.place), ph.mask_token, copy.deepcopy(asdict(ph.settings)), ph.bg] for ph in self.photos]

    @staticmethod
    def _same(a, b) -> bool:
        return len(a) == len(b) and all(x[0] is y[0] and x[1] == y[1] and x[2] is y[2] and x[3] == y[3]
                                        and x[4] is y[4] for x, y in zip(a, b))

    def commit(self):
        """Record one undo step. Identical states are not recorded twice."""
        self.commit_timer.stop()
        if self.syncing:
            return
        snap = self.snapshot()
        if self.history and self._same(self.history[self.hpos], snap):
            return
        self.history = self.history[:self.hpos + 1] + [snap]
        self.history = self.history[-80:]
        self.hpos = len(self.history) - 1
        self.update_buttons()

    def restore(self, snap):
        for photo, place, token, settings, bg in snap:
            if photo in self.photos:
                photo.place = replace(place)
                photo.settings = E.Settings(**copy.deepcopy(settings))
                photo.bg = bg
                if token is not None and photo.mask_token is not token:
                    photo.restore_mask(token)
        self.show_current()
        self.mark_thumbs()

    def undo(self):
        """One step back. Ctrl+Z lands here on the editor page."""
        if self.blocked():
            return
        if self.commit_timer.isActive():
            self.commit()            # a change still waiting to be recorded becomes its own step first
        if self.hpos > 0:
            self.hpos -= 1
            self.restore(self.history[self.hpos])

    def redo(self):
        if self.blocked():
            return
        if self.hpos < len(self.history) - 1:
            self.hpos += 1
            self.restore(self.history[self.hpos])

    def key_undo(self):
        page = self.pages.currentIndex()
        if page == 2:
            self.refine_page.view.undo()
        elif page == 1:
            self.undo()

    def key_redo(self):
        page = self.pages.currentIndex()
        if page == 2:
            self.refine_page.view.redo()
        elif page == 1:
            self.redo()

    def update_buttons(self):
        ready = self.current is not None and self.current.has_mask
        self.undo_button.setEnabled(self.hpos > 0)
        self.redo_button.setEnabled(self.hpos < len(self.history) - 1)
        self.export_button.setEnabled(ready)
        for b in (self.refine_button, self.cutout_button, self.again_button):
            b.setEnabled(ready)
        chosen = len(self.selected)
        self.apply_button.setVisible(len(self.photos) > 1)
        self.apply_button.setEnabled(ready)
        self.apply_button.setText(f'Apply to {chosen - 1} selected' if chosen > 1 else f'Apply to all {len(self.photos)}')
        for b in (self.trash_button, self.clear_button, self.all_button):
            b.setEnabled(bool(self.photos))
        if self.current is None:
            self.cut_status.setText('Open a photo to begin.')
        elif self.current.error:
            self.cut_status.setText('Could not remove the background: ' + self.current.error)
        elif not self.current.has_mask:
            self.cut_status.setText('Removing the background on this PC…')
        else:
            self.cut_status.setText('Background removed. Soften or fix the edges if you need to.')

    def sync_controls(self):
        """Make every tile and slider show the current settings, without firing changes."""
        self.syncing = True
        s = self.settings
        bg = self.current.bg if self.current is not None else None
        is_image = s.backdrop.get('type') == 'image'
        self.backdrop_group.setExclusive(False)
        for spec, tile in self.swatches:
            tile.setChecked(not is_image and E.same_backdrop(spec, s.backdrop))
        for path, tile in self.image_tiles.items():
            tile.setChecked(is_image and bg is not None and bg.path == path)
        self.backdrop_group.setExclusive(True)
        self.fit_row.setVisible(is_image)
        self.fit_buttons[s.background_mode].setChecked(True)
        self.shadow_tiles[s.shadow].setChecked(True)
        self.shadow_more.setVisible(s.shadow != 'none')
        for key, row in self.shadow_sliders.items():
            row.set(getattr(s, key))
        shape = 'original' if s.size_mode == 'original' else (s.width, s.height)
        self.shape_group.setExclusive(False)
        for key, tile in self.shape_tiles:
            tile.setChecked(key == shape)
        self.shape_group.setExclusive(True)
        self.width_box.setValue(s.width)
        self.height_box.setValue(s.height)
        layout = 'single' if s.layout == 'corner' else s.layout
        self.layout_tiles[layout].setChecked(True)
        self.look_tiles[s.look].setChecked(True)
        self.marks_group.setExclusive(False)
        for matches, tile in self.mark_tiles:
            tile.setChecked(bool(matches(s.mark)))
        self.marks_group.setExclusive(True)
        self.anchor_pad.set(s.mark_anchor)
        self.anchor_row.setVisible(layout in ('single', 'band'))
        color = (s.mark_color or '').upper() or None
        key = color if color in (None, '#FFFFFF', '#141410') else 'custom'
        for name, tile in self.mark_color_tiles.items():
            tile.setChecked(name == key)
            tile.update()
        for key, row in self.mark_sliders.items():
            row.set(getattr(s, key))
        self.feather.set(s.feather)
        self.syncing = False
        self.update_buttons()

    # ------------------------------------------------------------------ marks

    def show_kit(self):
        self.kit_name.setText(self.kit.get('name') or 'Your Brand')
        parts = [self.kit.get('tagline'), self.kit.get('handle')]
        self.kit_line.setText(' · '.join(p for p in parts if p) or 'Add a tagline and a handle')

    def edit_kit(self):
        """Change the brand kit. Every style in the gallery, and every photo that uses one, follows."""
        dialog = BrandDialog(self.kit, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        self.kit = dialog.kit
        self.save_list('brand.json', [self.kit])
        self.show_kit()
        self.rebuild_marks()
        for photo in self.photos:
            mark = photo.settings.mark
            if mark and mark.get('from_kit'):
                photo.settings.mark = E.template_spec(E.OLD_KINDS.get(mark['kind'], mark['kind']), self.kit)
        self.sync_controls()
        self.mark_thumbs()
        self.schedule()
        self.commit()
        self.say('Brand updated.', kind='success')

    def set_mark_group(self, name: str):
        self.mark_group = name
        self.group_buttons[name].setChecked(True)
        self.rebuild_marks()
        self.sync_controls()

    def pick_mark(self, spec):
        self.settings.mark = dict(spec) if spec else None
        self.sync_controls()
        self.schedule()
        self.commit()

    def apply_goal(self, name: str):
        """Brand, Protect or Proof: sets placement, size and strength in one go."""
        for key, value in GOALS[name][1].items():
            setattr(self.settings, key, value)
        self.sync_controls()
        self.schedule()
        self.commit()

    def set_mark_color(self, key):
        s = self.settings
        if key == 'custom':
            before = s.mark_color

            def live(color: str):
                s.mark_color = color
                self.schedule()

            key = pick_color(self, before or '#C9803F', 'Watermark color', live)
            if key is None:
                s.mark_color = before
                self.sync_controls()
                self.schedule()
                return
        s.mark_color = key
        self.sync_controls()
        self.schedule()
        self.commit()

    def save_marks(self):
        self.save_list('my_marks.json', self.marks)

    def _maker(self, spec):
        product = self.current.product(self.settings.feather, True) if self.current and self.current.has_mask else None
        dialog = MakerDialog(spec, product, self, self.kit)
        return dialog.spec if dialog.exec() == QDialog.DialogCode.Accepted else None

    def _add_mine(self, spec: dict):
        self.marks.append(spec)
        self.save_marks()
        self.set_mark_group('Mine')
        self.pick_mark(spec)

    def customize_mark(self):
        """Open the customizer on the current watermark. The result is saved under Mine."""
        spec = self._maker(self.settings.mark)
        if spec:
            self._add_mine(spec)
            self.say('Saved to Mine.', kind='success')

    def upload_mark(self):
        path, _ = QFileDialog.getOpenFileName(self, 'Choose a logo or pattern image', '',
                                              'Images (*.png *.jpg *.jpeg *.webp *.bmp)')
        if path:
            self._add_mine({'kind': 'image', 'name': Path(path).stem[:18], 'path': path})

    def edit_mine(self, saved: dict):
        spec = self._maker(saved)
        if spec and saved in self.marks:
            using = self.settings.mark == saved
            self.marks[self.marks.index(saved)] = spec
            self.save_marks()
            self.rebuild_marks()
            if using:
                self.pick_mark(spec)
            else:
                self.sync_controls()

    def remove_mine(self, saved: dict):
        self.marks = [m for m in self.marks if m is not saved]
        self.save_marks()
        self.rebuild_marks()
        self.sync_controls()
        self.say('Watermark removed.', kind='success')

    # ----------------------------------------------------------------- photos

    def choose_photos(self):
        if self.blocked():
            return
        paths, _ = QFileDialog.getOpenFileNames(self, 'Choose product photos', '', PHOTO_FILTER)
        if paths:
            self.add_photos(paths)

    def choose_folder(self):
        if self.blocked():
            return
        folder = QFileDialog.getExistingDirectory(self, 'Choose a folder of product photos')
        if folder:
            self.add_folder(folder)

    def add_folder(self, folder: str):
        """Add every supported photo in a folder. Asks about subfolders only when they hold photos."""
        here, deeper = find_photos(folder)
        name = Path(folder).name or folder
        if not here and not deeper:
            self.say('No photos found in that folder.', kind='warning',
                     detail='ProductStudio opens JPG, PNG, WebP, BMP and TIFF files.')
            return
        paths = here
        if deeper:
            box = QMessageBox(self)
            box.setWindowTitle('Add a folder')
            box.setIcon(QMessageBox.Icon.Question)
            box.setText(f'"{name}" has {plural(len(here), "photo")}.\nIts subfolders have {len(deeper)} more.')
            box.setInformativeText('Which photos do you want to add?')
            only = box.addButton(f'This folder only ({len(here)})', QMessageBox.ButtonRole.AcceptRole)
            every = box.addButton(f'Include subfolders ({len(here) + len(deeper)})', QMessageBox.ButtonRole.AcceptRole)
            box.addButton(QMessageBox.StandardButton.Cancel)
            only.setEnabled(bool(here))
            box.exec()
            if box.clickedButton() is every:
                paths = here + deeper
            elif box.clickedButton() is not only:
                return
        self.add_photos(paths)

    def add_paths(self, paths: list):
        """Used for drag and drop: a mix of photo files and folders."""
        files = [p for p in paths if os.path.isfile(p) and p.lower().endswith(PHOTO_TYPES)]
        if files:
            self.add_photos(files)
        for folder in (p for p in paths if os.path.isdir(p)):
            self.add_folder(folder)

    def add_photos(self, paths: list):
        if self.blocked():
            return
        known = {os.path.normcase(os.path.abspath(p.path)) for p in self.photos}
        fresh = []
        for path in paths:
            key = os.path.normcase(os.path.abspath(path))
            if key not in known:
                known.add(key)
                fresh.append(path)
        if not fresh:
            if paths:
                self.say('Already open.')
            return
        self.importing, self.import_stop = True, False
        self.pages.setCurrentIndex(1)
        big = len(fresh) > 6
        if big:
            self.job_bar.begin('Adding photos', len(fresh), pause=False, stop=True)
        added, problems = [], []
        for i, path in enumerate(fresh):
            if self.import_stop:
                break
            try:
                photo = Photo(path)
                self.photos.append(photo)
                self.add_tile(photo)       # each photo appears in the strip as soon as it is read
                added.append(photo)
                if self.current is None:
                    self.select(photo)
            except Exception as error:
                problems.append(f'{Path(path).name}: {error}')
            if big:
                self.job_bar.step(i + 1, f'{i + 1} of {len(fresh)} · {Path(path).name}',
                                  added[-1].original_thumb if added else None)
                QApplication.processEvents()
        self.importing = False
        self.job_bar.end()
        if problems:
            shown = '\n'.join(problems[:3]) + (f'\n…and {len(problems) - 3} more' if len(problems) > 3 else '')
            self.say(f'{plural(len(problems), "photo")} could not be opened.', kind='error', detail=shown)
        if not added:
            self.update_rail()
            return
        if not big or self.current not in added:
            self.select(added[0])
        stopped = ' Stopped early.' if self.import_stop else ''
        self.say(f'Added {plural(len(added), "photo")}.{stopped}', kind='success')
        self.update_rail()
        self.commit()
        self.pump()

    # ------------------------------------------------------------------ strip

    def add_tile(self, photo: Photo):
        tile = PhotoTile(photo, self)
        self.strip_tiles[photo] = tile
        self.strip.addWidget(tile)

    def add_menu(self, anchor: QWidget):
        menu = QMenu(self)
        menu.addAction('Add photos…', self.choose_photos)
        menu.addAction('Add a folder…', self.choose_folder)
        menu.exec(anchor.mapToGlobal(QPoint(anchor.width() + 4, 0)))

    def tile_menu(self, photo: Photo, where: QPoint):
        if photo not in self.selected:
            self.click_photo(photo, 'only')
        menu = QMenu(self)
        count = len(self.selected)
        menu.addAction('Remove this photo' if count == 1 else f'Remove {count} selected photos', self.remove_selected)
        menu.addAction('Select all', lambda: self.select_all(True))
        menu.exec(where)

    def status_of(self, photo: Photo) -> str:
        """What the strip shows for a photo: working, queued, done, failed or idle."""
        if photo is self.working_photo:
            return 'working'
        if photo in self.job_pending:
            return 'queued'
        if photo in self.job_failed or photo.error:
            return 'failed'
        if photo in self.job_done:
            return 'done'
        return 'idle' if photo.has_mask else 'queued'

    def set_working(self, photo):
        previous, self.working_photo = self.working_photo, photo
        for item in (previous, photo):
            if item in self.strip_tiles:
                self.strip_tiles[item].update()
        if photo is None:
            self.spin_timer.stop()
        elif not self.spin_timer.isActive():
            self.spin_timer.start()

    def spin_step(self):
        self.spin = (self.spin + 2.2) % 76
        tile = self.strip_tiles.get(self.working_photo)
        if tile is not None:
            tile.update()

    def select(self, photo: Photo):
        """Show one photo on the canvas and make it the only selected one."""
        self.current = photo
        self.selected = [photo]
        self.show_current()

    def click_photo(self, photo: Photo, how: str):
        """Clicks in the strip: plain click, Ctrl+click (or the round badge) and Shift+click."""
        if how == 'toggle':
            if photo in self.selected:
                if len(self.selected) == 1:
                    return
                self.selected.remove(photo)
                if photo is self.current:
                    self.current = self.selected[0]
                    self.show_current()
                    return
            else:
                self.selected.append(photo)
        elif how == 'range' and self.current in self.photos:
            a, b = sorted((self.photos.index(self.current), self.photos.index(photo)))
            self.selected = self.photos[a:b + 1]
        else:
            self.select(photo)
            return
        self.update_rail()
        self.update_buttons()

    def select_all(self, on: bool):
        if not self.photos:
            return
        self.selected = list(self.photos) if on else [self.current]
        self.update_rail()
        self.update_buttons()

    def toggle_select_all(self):
        self.select_all(len(self.selected) < len(self.photos))

    def key_select_all(self):
        if self.pages.currentIndex() == 1:
            self.select_all(True)

    def key_delete(self):
        if self.pages.currentIndex() == 1:
            self.remove_selected()

    def update_rail(self):
        for tile in self.strip_tiles.values():
            tile.update()
        total, chosen = len(self.photos), len(self.selected)
        self.count_label.setText('' if not total else (f'{chosen} of {total}' if chosen > 1 else plural(total, 'photo')))
        everything = total > 0 and chosen == total
        self.all_button.setIcon(QIcon(icon_pixmap('checkall', C['lime'] if everything and total > 1 else C['text'], 20)))
        self.all_button.setToolTip('Select only the current photo' if everything else 'Select all photos (Ctrl+A)')

    def batch_targets(self) -> list:
        """Batch actions work on the selected photos. With only one selected they work on all."""
        if len(self.selected) > 1:
            return [ph for ph in self.photos if ph in self.selected]
        return list(self.photos)

    def remove_selected(self):
        if self.photos and self.selected:
            self.confirm_remove(list(self.selected))

    def clear_all(self):
        if self.photos:
            self.confirm_remove(list(self.photos))

    def confirm_remove(self, photos: list):
        """Ask first, then take photos out of the list. Files on disk are never touched."""
        if self.blocked():
            return
        everything = len(photos) == len(self.photos)
        if len(photos) == 1:
            title = f'Remove "{Path(photos[0].path).name}" from the list?'
        elif everything:
            title = f'Clear all {len(photos)} photos from the list?'
        else:
            title = f'Remove {len(photos)} photos from the list?'
        box = QMessageBox(self)
        box.setWindowTitle('Remove photos')
        box.setIcon(QMessageBox.Icon.Warning)
        box.setText(title)
        box.setInformativeText('Your files stay on the disk. Only the cutouts and edits made here are lost, '
                               'and this cannot be undone.')
        yes = box.addButton('Clear all' if everything and len(photos) > 1 else 'Remove', QMessageBox.ButtonRole.DestructiveRole)
        box.addButton(QMessageBox.StandardButton.Cancel)
        box.exec()
        if box.clickedButton() is yes:
            self.remove_photos(photos)

    def remove_photos(self, photos: list):
        gone = set(photos)
        index = self.photos.index(self.current) if self.current in self.photos else 0
        for photo in photos:
            tile = self.strip_tiles.pop(photo, None)
            if tile is not None:
                self.strip.removeWidget(tile)
                tile.deleteLater()
        self.photos = [ph for ph in self.photos if ph not in gone]
        with Photo._lock:
            Photo._live = [ph for ph in Photo._live if ph not in gone]
        self.thumb_queue = [ph for ph in self.thumb_queue if ph not in gone]
        self.job_done -= gone
        self.job_failed -= gone
        self.history, self.hpos = [], -1          # undo steps that point at removed photos are dropped
        if not self.photos:
            self.current = Photo.pinned = None
            self.selected = []
            self.canvas.clear()
            self.update_rail()
            self.update_buttons()
            self.update_queue_bar()
            self.go_home()
            return
        self.select(self.photos[min(index, len(self.photos) - 1)] if self.current in gone else self.current)
        self.say(f'Removed {plural(len(photos), "photo")}.', kind='success')
        self.commit()
        self.update_queue_bar()

    # ------------------------------------------------------------- thumbnails

    def mark_thumbs(self, photos=None):
        """Queue strip pictures for a redraw. They are rebuilt a few at a time so the window stays smooth."""
        for photo in (self.photos if photos is None else photos):
            if photo not in self.thumb_queue:
                self.thumb_queue.append(photo)
        if self.thumb_queue and not self.thumb_timer.isActive():
            self.thumb_timer.start()

    def thumb_step(self):
        for _ in range(10):
            if not self.thumb_queue:
                self.thumb_timer.stop()
                return
            photo = self.thumb_queue.pop(0)
            if photo not in self.strip_tiles:
                continue
            try:
                mini = photo.mini
                if mini is None:
                    photo.thumb = photo.original_thumb
                else:
                    photo.thumb = to_pixmap(E.render(mini, photo.settings, photo.place,
                                                     photo.bg.tiny if photo.bg else None, max_side=132))
            except Exception:
                photo.thumb = photo.original_thumb
            self.strip_tiles[photo].update()

    # ------------------------------------------------------------ cut queue

    def pump(self):
        """Cut out the next waiting photo. One runs at a time."""
        if self.cutting or self.job is not None or self.queue_paused:
            self.update_queue_bar()
            return
        waiting = [ph for ph in self.photos if not ph.has_mask and not ph.error]
        if not waiting:
            self.update_queue_bar()
            return
        photo = self.current if self.current in waiting else waiting[0]
        self.cutting = True
        self.set_working(photo)
        self.update_queue_bar()
        worker = CutWorker(photo, self.holder)
        worker.signals.touched.connect(self.cut_finished)
        self._worker = worker
        QThreadPool.globalInstance().start(worker)

    def cut_finished(self, photo: Photo):
        self.cutting = False
        self.queue_done += 1
        self.set_working(None)
        self.photo_touched(photo)
        self.pump()

    def update_queue_bar(self):
        """The bottom strip also reports the automatic background removal after an import."""
        if self.job is not None or self.importing:
            return
        left = len([ph for ph in self.photos if not ph.has_mask and not ph.error])
        if left == 0 and not self.cutting:
            if self.job_bar.title.text() == QUEUE_TITLE:
                self.job_bar.end()
            self.queue_done = 0
            self.queue_paused = False
            return
        total = self.queue_done + left
        if total < 2 and not self.queue_paused:
            return                      # a single photo needs no progress strip
        if not self.job_bar.isVisible() or self.job_bar.title.text() != QUEUE_TITLE:
            self.job_bar.begin(QUEUE_TITLE, total, pause=True, stop=False)
        self.job_bar.total = total
        self.job_bar.set_paused(self.queue_paused)
        photo = self.working_photo
        if self.queue_paused:
            detail = f'Paused · {plural(left, "photo")} left'
        else:
            detail = f'{min(self.queue_done + 1, total)} of {total}' + (f' · {Path(photo.path).name}' if photo else '')
        self.job_bar.step(self.queue_done, detail, photo.original_thumb if photo else None)

    def photo_touched(self, photo: Photo):
        """A photo got its cutout. Older undo steps learn about it, so undo never removes a cutout."""
        if photo.has_mask:
            for snap in self.history:
                for entry in snap:
                    if entry[0] is photo and entry[2] is None:
                        entry[2] = photo.mask_token
        self.mark_thumbs([photo])
        if photo in self.strip_tiles:
            self.strip_tiles[photo].update()
        if photo is self.current:
            self.update_buttons()
            self.refresh()

    def cut_again(self):
        photo = self.current
        if photo is None or self.blocked():
            return
        if self.cutting and self.working_photo is photo:
            self.say('Already working on this photo.', kind='warning')
            return
        photo.restore_mask(None)
        photo.error = ''
        self.mark_thumbs([photo])
        self.update_buttons()
        self.refresh()
        self.pump()

    # ------------------------------------------------------------------- jobs

    def run_job(self, title: str, photos: list, step, finished):
        """Run `step` over photos in the background. Every photo shows its own state in the
        strip, and the bottom strip shows the total, with pause and stop."""
        if not photos or self.blocked():
            return
        control = JobControl()
        job = Job(photos, step, control)
        self.job = (job, control, title, finished)
        self.job_pending, self.job_done, self.job_failed = set(photos), set(), set()
        job.signals.started.connect(self.job_started)
        job.signals.progress.connect(self.job_progress)
        job.signals.touched.connect(self.job_touched)
        job.signals.failed.connect(lambda photo, _error: self.job_failed.add(photo))
        job.signals.finished.connect(self.job_finished)
        self.job_bar.begin(title, len(photos), pause=True, stop=True)
        self.update_rail()
        self.update_buttons()
        QThreadPool.globalInstance().start(job)

    def job_started(self, photo: Photo):
        self.job_pending.discard(photo)
        self.set_working(photo)
        self.job_bar.thumb.setPixmap(photo.thumb)
        tile = self.strip_tiles.get(photo)
        if tile is not None:
            self.strip_area.ensureWidgetVisible(tile, 0, 40)

    def job_touched(self, photo: Photo):
        if photo not in self.job_failed:
            self.job_done.add(photo)
        self.set_working(None)
        self.photo_touched(photo)

    def job_progress(self, done: int, total: int, name: str):
        if self.job is not None and not self.job[1].stopped:
            self.job_bar.step(done, f'{done + 1} of {total} · {name}')

    def toggle_pause(self):
        if self.job is not None:
            control = self.job[1]
            if control.paused:
                control.resume()
                self.job_bar.set_paused(False, 'Resuming…')
            else:
                control.pause()
                self.job_bar.set_paused(True, 'Paused. The photo in progress finishes first.')
        elif not self.importing:
            self.queue_paused = not self.queue_paused
            self.pump()

    def stop_job(self):
        if self.importing:
            self.import_stop = True
        elif self.job is not None:
            self.job[1].stop()
            self.job_bar.set_paused(False, 'Stopping after the current photo…')

    def job_finished(self, summary: dict):
        finished = self.job[3]
        self.job = None
        self.job_pending = set()
        self.set_working(None)
        self.job_bar.end()
        self.update_rail()
        self.update_buttons()
        finished(summary)
        QTimer.singleShot(4000, self.clear_job_marks)
        self.pump()

    def clear_job_marks(self):
        if self.job is None:
            self.job_done = set()
            self.update_rail()

    def job_report(self, verb: str, summary: dict):
        """One short closing message for a batch job. Hover it to see which photos failed."""
        text = f'{verb} {summary["done"]} of {summary["total"]}'
        if summary['skipped']:
            text += f', {summary["skipped"]} skipped'
        if summary['stopped']:
            text += ' (stopped)'
        failed = summary['failed']
        if failed:
            self.say(f'{text}. {len(failed)} failed.', kind='error', detail='\n'.join(failed[:12]))
        else:
            self.say(text + '.', kind='warning' if summary['stopped'] else 'success')

    def apply_look(self):
        """Copy the current photo's whole look to the selected photos, or to all of them when only
        one is selected: backdrop, shadow, canvas shape, watermark, size and position."""
        sample = self.current
        targets = [ph for ph in self.batch_targets() if ph is not sample]
        if sample is None or self.blocked():
            return
        if not targets:
            self.say('No other photos to apply to.', kind='warning')
            return
        if self.still_cutting([sample] + targets):
            return
        self.commit()
        for photo in targets:
            photo.settings = copy.deepcopy(sample.settings)
            if photo.settings.size_mode == 'original':       # "original" means each photo's own size
                photo.settings.width, photo.settings.height = self.fit_size(photo.size)
            photo.place = replace(sample.place)
            photo.bg = sample.bg
        self.commit()
        holder = self.holder
        self.run_job('Applying this look', targets, lambda photo: ensure_cutout(photo, holder),
                     lambda summary: self.job_report('Applied to', summary))

    # ---------------------------------------------------------------- preview

    def schedule(self):
        self.timer.start(0)
        self.thumb_later.start()

    def refresh(self):
        photo = self.current
        if photo is None:
            self.canvas.clear()
            return
        if not photo.has_mask:
            note = 'Could not remove the background' if photo.error else 'Removing background…'
            self.canvas.show_plain(photo.original_pixmap(), note)
            return
        s = self.settings
        try:
            product = photo.product(s.feather, True)
            picture = E.render(product, s, photo.place, self.background_small, max_side=PREVIEW_SIDE)
            box = E.product_box(product.size, photo.place, s.width, s.height) if product else None
            bright = E.auto_mark_color(picture) != (255, 255, 255)
            self.canvas.show_frame(to_pixmap(picture), (s.width, s.height), box, photo.place, bright)
        except Exception as error:
            self.say('Could not show this photo.', kind='error', detail=str(error))

    # ----------------------------------------------------------------- refine

    def open_refine(self):
        if self.current and self.current.has_mask and not self.blocked():
            self.refine_page.load(self.current.image, self.current.mask)
            self.pages.setCurrentIndex(2)

    def finish_refine(self, mask):
        self.current.set_mask(mask)
        self.pages.setCurrentIndex(1)
        self.say('Edges updated.', kind='success')
        self.commit()
        self.refresh()

    # ----------------------------------------------------------------- export

    def save_cutout(self):
        photo = self.current
        if not photo or not photo.has_mask:
            return
        path, _ = QFileDialog.getSaveFileName(self, 'Save cutout', f'{Path(photo.path).stem}-cutout.png', 'PNG image (*.png)')
        if not path:
            return
        try:
            write_cutout(photo, path, self.settings.feather)
            self.say('Cutout saved.', kind='success')
        except Exception as error:
            self.say('Could not save the cutout.', kind='error', detail=str(error))

    def open_export(self):
        if self.current is None or not self.current.has_mask or self.blocked():
            return
        targets = self.batch_targets()
        size = f'{self.settings.width} × {self.settings.height} px'
        if len({(ph.settings.width, ph.settings.height) for ph in targets}) > 1:
            size += ' for this photo. Other photos keep their own size.'
        dialog = ExportDialog(self, len(targets), Path(self.current.path).name, size, self.export_memory,
                              selected=len(self.selected) > 1)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        self.export_memory = dict(dialog.o)
        if dialog.o['scope'] == 'all':
            self.export_all(dialog.o)
        else:
            self.export_one(dialog.o)

    def export_one(self, o: dict):
        """One photo, saved wherever the user points, under any name."""
        photo = self.current
        suffix = '.jpg' if o['format'] == 'JPEG' else '.png'
        kinds = 'JPEG image (*.jpg *.jpeg)' if o['format'] == 'JPEG' else 'PNG image (*.png)'
        start = str(Path(photo.path).with_name(f'{Path(photo.path).stem}-studio{suffix}'))
        path, _ = QFileDialog.getSaveFileName(self, 'Export image', start, kinds)
        if not path:
            return
        if not Path(path).suffix:
            path += suffix
        if os.path.normcase(os.path.abspath(path)) == os.path.normcase(os.path.abspath(photo.path)):
            self.say('That is the original. Pick another name.', kind='warning')
            return
        try:
            picture = E.render(photo.product(self.settings.feather, False), self.settings, photo.place, self.background)
            save_picture(picture, path, o['quality'])
            if o['cutout']:
                write_cutout(photo, Path(path).with_name(Path(path).stem + '-cutout.png'), self.settings.feather)
            self.say(f'Exported {Path(path).name}.', kind='success')
        except Exception as error:
            self.say('Could not export the image.', kind='error', detail=str(error))

    def export_all(self, o: dict):
        """Every photo, with the naming rules from the export window."""
        holder = self.holder
        suffix = '.jpg' if o['format'] == 'JPEG' else '.png'
        targets = self.batch_targets()
        if self.blocked() or self.still_cutting(targets):
            return
        # Each photo is exported with its own look, frozen now so later edits cannot change a running export.
        looks = {id(ph): (copy.deepcopy(ph.settings), ph.bg.full if ph.bg else None, replace(ph.place)) for ph in targets}
        total = len(targets)
        number = {id(photo): i + 1 for i, photo in enumerate(targets)}
        protected = {os.path.normcase(os.path.abspath(photo.path)) for photo in self.photos}
        taken: set = set()

        def step(photo: Photo):
            settings, background, place = looks[id(photo)]
            ensure_cutout(photo, holder)
            folder = Path(o['folder']) if o['dest'] == 'folder' else Path(photo.path).parent
            base = build_name(o['pattern'], Path(photo.path).stem, number[id(photo)], total)
            target = unique_path(folder, base, suffix, taken, o['on_exists'], protected)
            if target is None:
                return False
            picture = E.render(photo.product(settings.feather, False), settings, place, background)
            save_picture(picture, target, o['quality'])
            if o['cutout']:
                cut = unique_path(folder, base + '-cutout', '.png', taken, o['on_exists'], protected)
                if cut is not None:
                    write_cutout(photo, cut, settings.feather)
            return True

        self.run_job('Exporting', targets, step, lambda summary: self.job_report('Exported', summary))

    # ------------------------------------------------------------ drag + drop

    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dropEvent(self, e):
        self.add_paths([u.toLocalFile() for u in e.mimeData().urls() if u.isLocalFile()])


def self_test(source: str, report: Path) -> int:
    """Used after packaging: proves the bundled model, rendering and window all work."""
    try:
        image = E.load_image(source)
        mask = E.Segmenter().mask(image)
        settings = E.Settings(width=320, height=320, mark=E.template_spec('badge', E.DEFAULT_KIT))
        result = E.render(E.cutout(image, mask), settings, E.Placement())
        window = MainWindow()
        window.show()
        QApplication.processEvents()
        assert mask.size == image.size and mask.getextrema()[0] < mask.getextrema()[1] and result.size == (320, 320)
        report.write_text('PASS: packaged model, CPU inference, composition, watermark and window', encoding='utf-8')
        window.close()
        return 0
    except Exception:
        report.write_text(traceback.format_exc(), encoding='utf-8')
        return 1


def main():
    app = QApplication(sys.argv)
    app.setApplicationName('Product Studio')
    app.setOrganizationName('Product Studio')
    app.setStyleSheet(QSS)
    app.setWindowIcon(QIcon(str(E.asset_path('assets/icon.ico'))))
    if '--self-test' in sys.argv:
        index = sys.argv.index('--self-test')
        sys.exit(self_test(sys.argv[index + 1], Path(sys.argv[index + 2])))
    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == '__main__':
    main()
