# -*- mode: python ; coding: utf-8 -*-
from pathlib import Path

ROOT = Path(SPECPATH)
ICON = ROOT / "build_assets" / "attendance.ico"
VERSION = ROOT / "build_assets" / "version_info.txt"

a = Analysis(
    [str(ROOT / "app.py")],
    pathex=[str(ROOT)],
    binaries=[],
    datas=[
        (str(ROOT / "attendance_app" / "templates"), "attendance_app/templates"),
        (str(ROOT / "attendance_app" / "static"), "attendance_app/static"),
        (str(ROOT / "attendance_app" / "locales"), "attendance_app/locales"),
    ],
    hiddenimports=[
        "tkinter",
        "tkinter.messagebox",
        "openpyxl",
        "xlrd",
        "bs4",
        "waitress",
        "barcode",
        "barcode.writer",
        "segno",
        "requests",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["pytest", "PyInstaller"],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="SmartAttendance",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(ICON),
    version=str(VERSION),
)
