"""The export window: one photo or all of them, where they go and how files are named."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QFileDialog, QHBoxLayout, QLineEdit, QVBoxLayout,
                               QWidget)

from jobs import build_name
from ui_kit import C, SliderRow, button, label, segmented

DEFAULTS = {'scope': 'one', 'format': 'PNG', 'quality': 95, 'cutout': False, 'dest': 'source', 'folder': '',
            'pattern': '{name}-studio', 'on_exists': 'number'}
PRESETS = [('Original name + "-studio"', '{name}-studio'), ('Original name + "-edited"', '{name}-edited'),
           ('Original name + number', '{name}-{n}'), ('Numbered: product-001', 'product-{n}')]
EXISTS = {'Add a number': 'number', 'Replace': 'replace', 'Skip': 'skip'}


class ExportDialog(QDialog):
    """After accept(), `self.o` holds the chosen options."""

    def __init__(self, parent, count: int, sample_name: str, size_text: str, memory: dict, selected: bool = False):
        super().__init__(parent)
        self.setWindowTitle('Export')
        self.setMinimumWidth(460)
        self.count, self.sample = count, sample_name
        self.o = dict(DEFAULTS, **memory)
        if count < 2:
            self.o['scope'] = 'one'
        box = QVBoxLayout(self)
        box.setContentsMargins(24, 22, 24, 22)
        box.setSpacing(12)
        box.addWidget(label('Export', 'h2'))
        box.addWidget(label(size_text, 'muted'))

        self.all_name = f'Selected {count} photos' if selected else f'All {count} photos'
        scope_row, self.scope_buttons = segmented(['This photo', self.all_name],
                                                  lambda n: self.set('scope', 'all' if n == self.all_name else 'one'))
        scope_row.setVisible(count > 1)
        box.addWidget(scope_row)

        format_row, self.format_buttons = segmented(['PNG', 'JPEG'], lambda n: self.set('format', n))
        box.addWidget(format_row)
        self.quality = SliderRow('JPEG quality', 50, 100, self.o['quality'])
        self.quality.moved.connect(lambda v: self.set('quality', v))
        box.addWidget(self.quality)
        self.cutout = QCheckBox('Also save a transparent cutout of each photo')
        self.cutout.setChecked(self.o['cutout'])
        self.cutout.toggled.connect(lambda v: self.set('cutout', v))
        box.addWidget(self.cutout)

        self.batch = QWidget()
        many = QVBoxLayout(self.batch)
        many.setContentsMargins(0, 6, 0, 0)
        many.setSpacing(10)
        many.addWidget(label('Save to', 'h2'))
        dest_row, self.dest_buttons = segmented(['Next to each original', 'One folder'],
                                                lambda n: self.set('dest', 'folder' if n == 'One folder' else 'source'))
        many.addWidget(dest_row)
        self.folder_row = QWidget()
        row = QHBoxLayout(self.folder_row)
        row.setContentsMargins(0, 0, 0, 0)
        self.folder_label = label('', 'muted')
        row.addWidget(self.folder_label, 1)
        row.addWidget(button('Choose folder…', self.choose_folder))
        many.addWidget(self.folder_row)

        many.addWidget(label('File names', 'h2'))
        self.presets = QComboBox()
        for text, _pattern in PRESETS:
            self.presets.addItem(text)
        self.presets.addItem('Custom')
        self.presets.activated.connect(self.pick_preset)
        many.addWidget(self.presets)
        self.pattern = QLineEdit(self.o['pattern'])
        self.pattern.setAccessibleName('File name pattern')
        self.pattern.textChanged.connect(lambda v: self.set('pattern', v))
        many.addWidget(self.pattern)
        many.addWidget(label('{name} is the original file name. {n} is a counter: 001, 002, 003.', 'muted', wrap=True))
        self.example = label('', None, wrap=True)
        self.example.setStyleSheet(f'color: {C["lime"]};')
        many.addWidget(self.example)

        many.addWidget(label('If a file already exists', 'h2'))
        exists_row, self.exists_buttons = segmented(list(EXISTS), lambda n: self.set('on_exists', EXISTS[n]))
        many.addWidget(exists_row)
        many.addWidget(label('Your original photos are never overwritten.', 'muted'))
        box.addWidget(self.batch)

        box.addSpacing(6)
        buttons = QHBoxLayout()
        buttons.addStretch(1)
        buttons.addWidget(button('Cancel', self.reject))
        self.go = button('', self.finish, 'primary', 'export')
        buttons.addWidget(self.go)
        box.addLayout(buttons)
        self.refresh()

    def set(self, key: str, value):
        self.o[key] = value
        self.refresh()

    def pick_preset(self, index: int):
        if index < len(PRESETS):
            self.pattern.setText(PRESETS[index][1])

    def choose_folder(self):
        folder = QFileDialog.getExistingDirectory(self, 'Choose a folder for the exported photos', self.o['folder'])
        if folder:
            self.set('folder', folder)

    def refresh(self):
        o = self.o
        every = o['scope'] == 'all'
        self.scope_buttons[self.all_name if every else 'This photo'].setChecked(True)
        self.format_buttons[o['format']].setChecked(True)
        self.quality.setVisible(o['format'] == 'JPEG')
        self.batch.setVisible(every)
        self.dest_buttons['One folder' if o['dest'] == 'folder' else 'Next to each original'].setChecked(True)
        self.folder_row.setVisible(o['dest'] == 'folder')
        self.folder_label.setText(o['folder'] or 'No folder chosen yet')
        patterns = [p for _t, p in PRESETS]
        self.presets.setCurrentIndex(patterns.index(o['pattern']) if o['pattern'] in patterns else len(PRESETS))
        for text, key in EXISTS.items():
            if key == o['on_exists']:
                self.exists_buttons[text].setChecked(True)
        suffix = '.jpg' if o['format'] == 'JPEG' else '.png'
        result = build_name(o['pattern'], Path(self.sample).stem, 1, self.count) + suffix
        note = '' if ('{name}' in o['pattern'] or '{n}' in o['pattern']) else \
            '   Add {n} or {name}, or every file gets the same name plus (2), (3)…'
        self.example.setText(f'{self.sample}  →  {result}{note}')
        self.go.setText(f'Export {self.count} photos' if every else 'Choose where to save…')
        self.adjustSize()

    def finish(self):
        if self.o['scope'] == 'all' and self.o['dest'] == 'folder' and not self.o['folder']:
            self.choose_folder()
            if not self.o['folder']:
                return
        if not self.o['pattern'].strip():
            self.o['pattern'] = DEFAULTS['pattern']
        self.accept()
