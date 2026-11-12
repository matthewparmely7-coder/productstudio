"""Batch work for ProductStudio: a pausable job runner and safe file naming."""
from __future__ import annotations

import os
import threading
from pathlib import Path

from PySide6.QtCore import QObject, QRunnable, Signal

PHOTO_TYPES = ('.jpg', '.jpeg', '.png', '.webp', '.bmp', '.tif', '.tiff')
_RESERVED = {'CON', 'PRN', 'AUX', 'NUL'} | {f'COM{i}' for i in range(1, 10)} | {f'LPT{i}' for i in range(1, 10)}


class Signals(QObject):
    started = Signal(object)             # work on this photo begins
    touched = Signal(object)             # work on this photo ended
    failed = Signal(object, str)         # this photo could not be done
    progress = Signal(int, int, str)     # finished so far, total, name of the photo being worked on
    finished = Signal(object)            # a summary dict


class JobControl:
    """Lets the window pause, resume or stop a running job from another thread."""

    def __init__(self):
        self._go = threading.Event()
        self._go.set()
        self.stopped = False

    @property
    def paused(self) -> bool:
        return not self._go.is_set()

    def pause(self):
        self._go.clear()

    def resume(self):
        self._go.set()

    def stop(self):
        self.stopped = True
        self._go.set()

    def checkpoint(self) -> bool:
        """Called between photos. Waits while paused. Returns False once stopped."""
        self._go.wait()
        return not self.stopped


class Job(QRunnable):
    """Runs `step(photo)` for each photo. A step returns False to mean "skipped"."""

    def __init__(self, photos: list, step, control: JobControl):
        super().__init__()
        self.photos, self.step, self.control, self.signals = photos, step, control, Signals()

    def run(self):
        done, skipped, failed = 0, 0, []
        total = len(self.photos)
        for i, photo in enumerate(self.photos):
            if not self.control.checkpoint():
                break
            name = Path(photo.path).name
            self.signals.progress.emit(i, total, name)
            self.signals.started.emit(photo)
            try:
                if self.step(photo) is False:
                    skipped += 1
                else:
                    done += 1
            except Exception as error:
                failed.append(f'{name}: {error}')
                self.signals.failed.emit(photo, str(error))
            self.signals.touched.emit(photo)
        self.signals.finished.emit({'done': done, 'skipped': skipped, 'failed': failed, 'total': total,
                                    'stopped': self.control.stopped})


# ------------------------------------------------------------------- file names

def find_photos(folder: str) -> tuple:
    """Photos directly in `folder`, and photos in its subfolders, each sorted by name."""
    here, deeper = [], []
    for root, dirs, files in os.walk(folder):
        dirs[:] = sorted(d for d in dirs if not d.startswith('.'))
        found = sorted((os.path.join(root, f) for f in files if f.lower().endswith(PHOTO_TYPES)), key=str.lower)
        if os.path.normcase(os.path.abspath(root)) == os.path.normcase(os.path.abspath(folder)):
            here = found
        else:
            deeper += found
    return here, deeper


def clean_name(text: str) -> str:
    """Make text safe as a Windows file name."""
    text = ''.join('-' if (c in '<>:"/\\|?*' or ord(c) < 32) else c for c in text).strip(' .')[:120]
    if not text:
        return 'photo'
    return '_' + text if text.split('.')[0].upper() in _RESERVED else text


def build_name(pattern: str, stem: str, number: int, total: int) -> str:
    """Fill a naming pattern. {name} is the original file name, {n} is a counter padded with zeros."""
    digits = max(3, len(str(total)))
    filled = (pattern or '{name}-studio').replace('{name}', stem).replace('{n}', str(number).zfill(digits))
    return clean_name(filled)


def unique_path(folder: Path, base: str, suffix: str, taken: set, on_exists: str, protected: set) -> Path | None:
    """Pick the file to write.

    `taken` holds names already used in this export, so two photos never share a file.
    `protected` holds the original photos, which are never overwritten.
    `on_exists` says what to do with other existing files: 'number', 'replace' or 'skip'.
    Returns None when the photo should be skipped.
    """
    candidate, counter = Path(folder) / (base + suffix), 2
    while True:
        key = os.path.normcase(os.path.abspath(candidate))
        blocked = key in taken or key in protected
        exists = candidate.exists()
        if not blocked and (not exists or on_exists == 'replace'):
            taken.add(key)
            return candidate
        if not blocked and on_exists == 'skip':
            return None
        candidate = Path(folder) / f'{base} ({counter}){suffix}'
        counter += 1
