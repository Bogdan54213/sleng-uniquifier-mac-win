#!/bin/bash
set -e
cd "$(dirname "$0")/../.."

echo "============================================"
echo "  Sleng Uniquifier - Mac Build"
echo "============================================"
echo

# ── 0. Generate icon ──────────────────────────────────────────────────────────
echo "[0/4] Generating icons..."
python3 -c "import PIL" 2>/dev/null || pip3 install pillow -q
python3 builds/electron/generate_icon.py

# Convert icon.png → icon.icns for Mac
mkdir -p builds/electron/icon.iconset
for size in 16 32 64 128 256 512; do
  sips -z $size $size builds/electron/icon.png \
    --out builds/electron/icon.iconset/icon_${size}x${size}.png 2>/dev/null
done
iconutil -c icns builds/electron/icon.iconset -o builds/electron/icon.icns
rm -rf builds/electron/icon.iconset
echo "[OK] icon.icns created"

# ── 1. Build server binary with PyInstaller ───────────────────────────────────
echo "[1/4] Building server binary..."
mkdir -p builds/electron/resources
python3 -m PyInstaller builds/electron/server_entry.spec \
    --distpath builds/electron/resources_tmp \
    --workpath build_server \
    --noconfirm
cp builds/electron/resources_tmp/server builds/electron/resources/server
chmod +x builds/electron/resources/server
rm -rf builds/electron/resources_tmp build_server
echo "[OK] server binary built"

# Copy ffmpeg
FFMPEG_PATH=$(which ffmpeg 2>/dev/null || echo "")
FFPROBE_PATH=$(which ffprobe 2>/dev/null || echo "")
if [ -n "$FFMPEG_PATH" ]; then
    cp "$FFMPEG_PATH"  builds/electron/resources/ffmpeg
    cp "$FFPROBE_PATH" builds/electron/resources/ffprobe
    chmod +x builds/electron/resources/ffmpeg builds/electron/resources/ffprobe
    echo "[OK] FFmpeg copied"
else
    echo "[WARN] ffmpeg not found - install via: brew install ffmpeg"
fi

# ── 2. npm install ────────────────────────────────────────────────────────────
echo "[2/4] npm install..."
cd builds/electron
npm install --silent
cd ../..

# ── 3. electron-packager ──────────────────────────────────────────────────────
echo "[3/4] Packaging with electron-packager..."

# Copy resources into app folder
mkdir -p builds/electron/app_resources
cp builds/electron/resources/server     builds/electron/app_resources/ 2>/dev/null || true
cp builds/electron/resources/ffmpeg     builds/electron/app_resources/ 2>/dev/null || true
cp builds/electron/resources/ffprobe    builds/electron/app_resources/ 2>/dev/null || true

cd builds/electron
npx electron-packager . "Sleng Uniquifier" \
    --platform=darwin \
    --arch=x64,arm64 \
    --out=../../dist/mac-packed \
    --icon=icon.icns \
    --overwrite \
    --asar=false \
    --ignore=node_modules/.cache
cd ../..

# Copy server + ffmpeg into packed app resources
for ARCH in x64 arm64; do
    PACKED="dist/mac-packed/Sleng Uniquifier-darwin-${ARCH}"
    if [ -d "$PACKED" ]; then
        cp builds/electron/resources/server   "$PACKED/Sleng Uniquifier.app/Contents/Resources/" 2>/dev/null || true
        cp builds/electron/resources/ffmpeg   "$PACKED/Sleng Uniquifier.app/Contents/Resources/" 2>/dev/null || true
        cp builds/electron/resources/ffprobe  "$PACKED/Sleng Uniquifier.app/Contents/Resources/" 2>/dev/null || true
        chmod +x "$PACKED/Sleng Uniquifier.app/Contents/Resources/server"   2>/dev/null || true
        chmod +x "$PACKED/Sleng Uniquifier.app/Contents/Resources/ffmpeg"   2>/dev/null || true
        chmod +x "$PACKED/Sleng Uniquifier.app/Contents/Resources/ffprobe"  2>/dev/null || true
    fi
done

# ── 4. Create DMG ─────────────────────────────────────────────────────────────
echo "[4/4] Creating DMG..."
mkdir -p dist/mac

for ARCH in x64 arm64; do
    PACKED="dist/mac-packed/Sleng Uniquifier-darwin-${ARCH}"
    APP="$PACKED/Sleng Uniquifier.app"
    if [ -d "$APP" ]; then
        DMG="dist/mac/SlengUniquifier_v1.0.0_${ARCH}.dmg"
        hdiutil create \
            -volname "Sleng Uniquifier" \
            -srcfolder "$APP" \
            -ov -format UDZO \
            "$DMG"
        echo "[OK] $DMG"
    fi
done

echo
echo "============================================"
echo "  Done! dist/mac/"
echo "============================================"
