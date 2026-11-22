# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller spec file for Fellow (Windows standalone desktop executable).
Run with:
    pyinstaller fellow.spec --clean --noconfirm
"""
from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs, collect_submodules

block_cipher = None

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
    "clr_loader",
    "pythonnet",
    "psutil",
    "pypdf",
    "docx",
    "engineio.async_drivers.threading",
]
hiddenimports += collect_submodules("webview")

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
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon="assets/icon.ico",
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="Fellow",
)
