#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

if [[ "$(uname -s)" != "Darwin" ]]; then
  echo "Error: build_mac.sh must be run on macOS (Darwin)." >&2
  exit 1
fi

ARCH="$(uname -m)"
echo "=== Building Fellow All-in-One macOS App & DMG ($ARCH) ==="

# 1. Generate assets/icon.icns from assets/icon.png (or assets/logo.png)
ICON_SRC="assets/icon.png"
if [[ ! -f "$ICON_SRC" ]]; then
  ICON_SRC="assets/logo.png"
fi

if [[ -f "$ICON_SRC" ]] && command -v iconutil >/dev/null 2>&1 && command -v sips >/dev/null 2>&1; then
  echo "Generating assets/icon.icns from $ICON_SRC ..."
  ICONSET_WORK="$(mktemp -d)"
  ICONSET_DIR="$ICONSET_WORK/Fellow.iconset"
  mkdir -p "$ICONSET_DIR"
  sips -z 16 16     "$ICON_SRC" --out "$ICONSET_DIR/icon_16x16.png"      >/dev/null
  sips -z 32 32     "$ICON_SRC" --out "$ICONSET_DIR/icon_16x16@2x.png"   >/dev/null
  sips -z 32 32     "$ICON_SRC" --out "$ICONSET_DIR/icon_32x32.png"      >/dev/null
  sips -z 64 64     "$ICON_SRC" --out "$ICONSET_DIR/icon_32x32@2x.png"   >/dev/null
  sips -z 128 128   "$ICON_SRC" --out "$ICONSET_DIR/icon_128x128.png"    >/dev/null
  sips -z 256 256   "$ICON_SRC" --out "$ICONSET_DIR/icon_128x128@2x.png" >/dev/null
  sips -z 256 256   "$ICON_SRC" --out "$ICONSET_DIR/icon_256x256.png"    >/dev/null
  sips -z 512 512   "$ICON_SRC" --out "$ICONSET_DIR/icon_256x256@2x.png" >/dev/null
  sips -z 512 512   "$ICON_SRC" --out "$ICONSET_DIR/icon_512x512.png"    >/dev/null
  sips -z 1024 1024 "$ICON_SRC" --out "$ICONSET_DIR/icon_512x512@2x.png" >/dev/null
  iconutil -c icns "$ICONSET_DIR" -o "assets/icon.icns"
  rm -rf "$ICONSET_WORK"
fi

# 2. Clean old build outputs and run PyInstaller
rm -rf build dist
pyinstaller fellow.spec --clean --noconfirm

APP_BUNDLE="dist/Fellow.app"
if [[ ! -d "$APP_BUNDLE" ]]; then
  echo "Error: PyInstaller did not produce $APP_BUNDLE" >&2
  exit 1
fi

# 3. Download official Ollama macOS archive (ollama-darwin.tgz) and bundle inside Fellow.app
mkdir -p installer_assets
OLLAMA_TGZ="installer_assets/ollama-darwin.tgz"
OLLAMA_URL="https://github.com/ollama/ollama/releases/latest/download/ollama-darwin.tgz"

if [[ -f "$OLLAMA_TGZ" ]] && [[ $(wc -c < "$OLLAMA_TGZ") -ge 20000000 ]]; then
  echo "Using cached $OLLAMA_TGZ ..."
else
  echo "Downloading official Ollama macOS archive from $OLLAMA_URL ..."
  rm -f "$OLLAMA_TGZ.downloading"
  curl -fL --retry 3 --progress-bar -o "$OLLAMA_TGZ.downloading" "$OLLAMA_URL"
  mv "$OLLAMA_TGZ.downloading" "$OLLAMA_TGZ"
fi

RESOURCES_OLLAMA="$APP_BUNDLE/Contents/Resources/ollama"
mkdir -p "$RESOURCES_OLLAMA"
tar -xzf "$OLLAMA_TGZ" -C "$RESOURCES_OLLAMA"

FOUND_OLLAMA="$(find "$RESOURCES_OLLAMA" -type f -name 'ollama' | head -n 1)"
if [[ -z "$FOUND_OLLAMA" ]]; then
  echo "Error: Could not find ollama binary inside $RESOURCES_OLLAMA after extraction." >&2
  exit 1
fi
chmod +x "$FOUND_OLLAMA"
echo "Smoke-testing bundled Ollama binary: $FOUND_OLLAMA"
"$FOUND_OLLAMA" --version || true

# 4. Copy third-party license notice into app bundle Resources
if [[ -f "THIRD_PARTY_LICENSES.md" ]]; then
  cp "THIRD_PARTY_LICENSES.md" "$APP_BUNDLE/Contents/Resources/THIRD_PARTY_LICENSES.md"
fi

# 5. Ad-hoc sign the completed bundle (required on Apple Silicon macOS)
echo "Ad-hoc signing $APP_BUNDLE ..."
codesign --force --deep --sign - "$APP_BUNDLE"

# 6. Package compressed drag-and-drop DMG (Fellow.app + /Applications symlink)
DMG_PATH="dist/Fellow-macOS-${ARCH}.dmg"
DMG_STAGE="$(mktemp -d)"
cp -R "$APP_BUNDLE" "$DMG_STAGE/Fellow.app"
ln -s /Applications "$DMG_STAGE/Applications"

rm -f "$DMG_PATH"
echo "Creating compressed DMG at $DMG_PATH ..."
hdiutil create -volname "Fellow" -srcfolder "$DMG_STAGE" -ov -format UDZO "$DMG_PATH"
rm -rf "$DMG_STAGE"

echo "Build complete: $DMG_PATH"
ls -lh "$DMG_PATH"
