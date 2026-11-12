# -*- mode: python ; coding: utf-8 -*-
from pathlib import Path

a = Analysis(
    ['app.py'],
    pathex=[],
    binaries=[],
    datas=[(str(Path(SPECPATH) / 'models' / 'isnet-general-use.onnx'), 'models'),
           (str(Path(SPECPATH) / 'assets'), 'assets')],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['torch', 'scipy', 'matplotlib', 'pandas', 'sympy', 'pytest', 'IPython'],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='ProductStudio',
    icon=str(Path(SPECPATH) / 'assets' / 'icon.ico'),
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name='ProductStudio')
