# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller spec file for Fellow (Windows standalone executable & macOS .app bundle).
Run with:
    pyinstaller fellow.spec --clean --noconfirm
"""
import os
import sys
from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs, collect_submodules

block_cipher = None
is_mac = sys.platform == "darwin"

datas = [
    ("static", "static"),
    ("assets", "assets"),
]
datas += collect_data_files("webview")
datas += collect_data_files("rapidocr_onnxruntime")

binaries = []
binaries += collect_dynamic_libs("faiss")
binaries += collect_dynamic_libs("onnxruntime")

hiddenimports = [
    "faiss",
    "fitz",
    "pytesseract",
    "rapidocr_onnxruntime",
    "onnxruntime",
    "webview",
    "psutil",
    "pypdf",
    "docx",
    "engineio.async_drivers.threading",
]
if is_mac:
    hiddenimports += [
        "webview.platforms.cocoa",
        "objc",
        "Cocoa",
        "WebKit",
        "Foundation",
        "AppKit",
    ]
else:
    hiddenimports += [
        "clr_loader",
        "pythonnet",
    ]
hiddenimports += collect_submodules("webview")

if is_mac:
    app_icon = "assets/icon.icns" if os.path.exists("assets/icon.icns") else None
else:
    app_icon = "assets/icon.ico"

a = Analysis(
    ["app.py"],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "matplotlib",
        "scipy",
        "pandas",
        "torch",
        "torchaudio",
        "torchvision",
        "transformers",
        "spacy",
        "nltk",
        "whisper",
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Fellow",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=not is_mac,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=app_icon,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=not is_mac,
    upx_exclude=[],
    name="Fellow",
)

if is_mac:
    app = BUNDLE(
        coll,
        name="Fellow.app",
        icon=app_icon,
        bundle_identifier="com.aymanboumait.fellow",
        info_plist={
            "CFBundleName": "Fellow",
            "CFBundleDisplayName": "Fellow",
            "CFBundleShortVersionString": "1.0.0",
            "NSHighResolutionCapable": True,
            "NSPrincipalClass": "NSApplication",
            "NSAppTransportSecurity": {
                "NSAllowsLocalNetworking": True,
            },
        },
    )
