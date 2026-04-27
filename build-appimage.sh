#!/usr/bin/env bash
# Build AppImage for TUNA
set -e

APP="TUNA"
VERSION=$(python3 -c "import tomllib; print(tomllib.load(open('pyproject.toml', 'rb'))['project']['version'])" 2>/dev/null) || VERSION="1.1.0"
ARCH="$(uname -m)"
APPIMAGE="${APP}-${VERSION}-${ARCH}.AppImage"

echo "Building $APPIMAGE..."

# Clean old build
rm -rf "$APP.AppDir" "$APPIMAGE"

# Create AppDir structure
mkdir -p "$APP.AppDir/usr/bin"
mkdir -p "$APP.AppDir/usr/lib"
mkdir -p "$APP.AppDir/usr/share/applications"
mkdir -p "$APP.AppDir/usr/share/icons/hicolor/256x256/apps"

# Copy icon
cp tuna.png "$APP.AppDir/" 2>/dev/null || true
cp tuna.png "$APP.AppDir/usr/share/icons/hicolor/256x256/apps/" 2>/dev/null || true

# Create desktop file
cat > "$APP.AppDir/tuna.desktop" <<'EOF'
[Desktop Entry]
Name=TUNA
Comment=Terminal music player with audio visualizer
Exec=tuna %U
Icon=tuna
Terminal=true
Type=Application
Categories=AudioVideo;Audio;Player;Music;
Keywords=music;player;terminal;audio;
EOF

# Copy desktop file
cp "$APP.AppDir/tuna.desktop" "$APP.AppDir/usr/share/applications/"

# Install Python package and deps
pip install --target="$APP.AppDir/usr/lib/python3" -e . --prefix ""

# Copy tuna package
cp -r tuna "$APP.AppDir/usr/lib/python3/"

# Create AppRun
cat > "$APP.AppDir/AppRun" <<'EOF'
#!/bin/bash
HERE="$(dirname "$(readlink -f "${0}")")"
export PYTHONPATH="${HERE}/usr/lib/python3:${PYTHONPATH:-}"
export PATH="${HERE}/usr/bin:${PATH}"
exec python3 -m tuna "$@"
EOF
chmod +x "$APP.AppDir/AppRun"

# Download appimagetool if not present
if ! command -v appimagetool &>/dev/null; then
    echo "Downloading appimagetool..."
    wget -q "https://github.com/AppImage/AppImageKit/releases/download/continuous/appimagetool-x86_64.AppImage" \
         -O /tmp/appimagetool
    chmod +x /tmp/appimagetool
    APPIMAGETOOL=/tmp/appimagetool
else
    APPIMAGETOOL=appimagetool
fi

# Build AppImage
ARCH=x86_64 "$APPIMAGETOOL" "$APP.AppDir" "$APPIMAGE"

echo ""
echo "Built: $APPIMAGE"
ls -lh "$APPIMAGE"