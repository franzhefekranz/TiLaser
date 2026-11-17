#!/usr/bin/env bash
# Baut unter Linux (x86_64 oder aarch64/arm64): PyInstaller-Ordner, AppImage und .deb-Paket.
# Die Zielarchitektur ist die des Build-Rechners (kein Cross-Build).
# Aufruf aus dem Repository-Wurzelordner:  bash packaging/build_linux.sh
#
# Umgebungsvariablen:
#   SKIP_PIP=1        keine pip-Installation
#   SKIP_TESTS=1      Tests überspringen
#   SKIP_BUILD=1      vorhandenen Build in dist/TiLaser verwenden
#   SKIP_APPIMAGE=1   kein AppImage
#   SKIP_DEB=1        kein .deb
#   APPIMAGETOOL=…    Pfad zu appimagetool (sonst wird es heruntergeladen)
#
# Für einen möglichst kompatiblen Build auf einer älteren Distribution bauen (z. B. Ubuntu 22.04):
# das Ergebnis läuft dann auch auf neueren Systemen.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
PY="${PYTHON:-python3}"
VERSION="$($PY -c 'import tilaser; print(tilaser.__version__)')"
ARCH="$(uname -m)"
case "$ARCH" in
    x86_64)        DEB_ARCH="amd64"; LDARCH="x86-64";  MIN_GLIBC="2.35" ;;
    aarch64|arm64) ARCH="aarch64"; DEB_ARCH="arm64"; LDARCH="AArch64"; MIN_GLIBC="2.39" ;;
    *) echo "Nicht unterstützte Architektur: $ARCH"; exit 1 ;;
esac
# MIN_GLIBC: Mindestversion, die die PyQt6-Wheels voraussetzen (x86_64: 2.35, aarch64: 2.39)
RELEASE="$ROOT/dist/release"
LINUX="$ROOT/packaging/linux"
mkdir -p "$RELEASE"
echo "== TiLaser $VERSION ($ARCH) =="

if [[ "${SKIP_PIP:-0}" != 1 ]]; then
    $PY -m pip install --upgrade pip
    $PY -m pip install -r requirements.txt pyinstaller
fi
if [[ "${SKIP_TESTS:-0}" != 1 ]]; then
    $PY tests/test_core.py
fi
if [[ "${SKIP_BUILD:-0}" != 1 ]]; then
    rm -rf build dist/TiLaser
    $PY -m PyInstaller --noconfirm --clean packaging/tilaser.spec
fi
[[ -x dist/TiLaser/tilaser ]] || { echo "dist/TiLaser/tilaser fehlt"; exit 1; }

# Bibliotheken, die das Qt-xcb-Plugin braucht, aber nicht mit PyQt6 ausgeliefert werden
XCB_LIBS=(libxcb-cursor.so.0 libxcb-icccm.so.4 libxcb-image.so.0 libxcb-keysyms.so.1 libxcb-render-util.so.0
          libxcb-shape.so.0 libxcb-xkb.so.1 libxkbcommon-x11.so.0 libxcb-util.so.1)

find_lib() { ldconfig -p 2>/dev/null | awk -v l="$1" -v a="$LDARCH" '$1 == l && index($0, a) {print $NF; exit}'; }

# ------------------------------------------------------------------ AppImage
if [[ "${SKIP_APPIMAGE:-0}" != 1 ]]; then
    APPDIR="$ROOT/dist/TiLaser.AppDir"
    rm -rf "$APPDIR"
    mkdir -p "$APPDIR/usr/lib/tilaser" "$APPDIR/usr/lib/extra" "$APPDIR/usr/share/applications" \
             "$APPDIR/usr/share/icons/hicolor/256x256/apps" "$APPDIR/usr/share/mime/packages"
    cp -a dist/TiLaser/. "$APPDIR/usr/lib/tilaser/"
    for lib in "${XCB_LIBS[@]}"; do
        p="$(find_lib "$lib")"
        if [[ -n "$p" ]]; then cp -L "$p" "$APPDIR/usr/lib/extra/"; else echo "Hinweis: $lib nicht gefunden (nicht gebündelt)"; fi
    done
    cp "$LINUX/tilaser.desktop" "$APPDIR/tilaser.desktop"
    cp "$LINUX/tilaser.desktop" "$APPDIR/usr/share/applications/"
    cp packaging/icon/tilaser-256.png "$APPDIR/tilaser.png"
    cp packaging/icon/tilaser-256.png "$APPDIR/usr/share/icons/hicolor/256x256/apps/tilaser.png"
    cp packaging/icon/tilaser-256.png "$APPDIR/.DirIcon"
    cp "$LINUX/tilaser-mime.xml" "$APPDIR/usr/share/mime/packages/tilaser.xml"
    cat > "$APPDIR/AppRun" <<'EOF'
#!/bin/sh
HERE="$(dirname "$(readlink -f "$0")")"
export LD_LIBRARY_PATH="$HERE/usr/lib/extra${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
exec "$HERE/usr/lib/tilaser/tilaser" "$@"
EOF
    chmod +x "$APPDIR/AppRun"

    TOOL="${APPIMAGETOOL:-}"
    if [[ -z "$TOOL" ]]; then
        TOOL="$ROOT/build/appimagetool-$ARCH.AppImage"
        if [[ ! -x "$TOOL" ]]; then
            mkdir -p build
            curl -fsSL -o "$TOOL" \
              "https://github.com/AppImage/appimagetool/releases/download/continuous/appimagetool-$ARCH.AppImage"
            chmod +x "$TOOL"
        fi
    fi
    OUT="$RELEASE/TiLaser-$VERSION-$ARCH.AppImage"
    # --appimage-extract-and-run: funktioniert auch ohne FUSE (z. B. in CI-Containern)
    ARCH=$ARCH APPIMAGE_EXTRACT_AND_RUN=1 "$TOOL" --no-appstream "$APPDIR" "$OUT"
    echo "AppImage: $OUT"
fi

# ------------------------------------------------------------------ Debian-Paket
if [[ "${SKIP_DEB:-0}" != 1 ]]; then
    PKG="$ROOT/dist/deb/tilaser_${VERSION}_${DEB_ARCH}"
    rm -rf "$PKG"
    mkdir -p "$PKG/DEBIAN" "$PKG/opt/tilaser" "$PKG/usr/bin" "$PKG/usr/share/applications" \
             "$PKG/usr/share/icons/hicolor/256x256/apps" "$PKG/usr/share/icons/hicolor/512x512/apps" \
             "$PKG/usr/share/mime/packages" "$PKG/usr/share/metainfo" "$PKG/usr/share/doc/tilaser"
    cp -a dist/TiLaser/. "$PKG/opt/tilaser/"
    cp -a beispiele "$PKG/usr/share/doc/tilaser/beispiele"
    ln -s /opt/tilaser/tilaser "$PKG/usr/bin/tilaser"
    cp "$LINUX/tilaser.desktop" "$PKG/usr/share/applications/tilaser.desktop"
    cp packaging/icon/tilaser-256.png "$PKG/usr/share/icons/hicolor/256x256/apps/tilaser.png"
    cp packaging/icon/tilaser.png "$PKG/usr/share/icons/hicolor/512x512/apps/tilaser.png"
    cp "$LINUX/tilaser-mime.xml" "$PKG/usr/share/mime/packages/tilaser.xml"
    cp "$LINUX/tilaser.appdata.xml" "$PKG/usr/share/metainfo/io.github.franzhefekranz.tilaser.metainfo.xml"
    cp README.md "$PKG/usr/share/doc/tilaser/"
    {
        echo "Format: https://www.debian.org/doc/packaging-manuals/copyright-format/1.0/"
        echo "Upstream-Name: TiLaser"
        echo "Source: https://github.com/franzhefekranz/TiLaser"
        echo
        echo "Files: *"
        echo "License: GPL-3.0-or-later"
        echo " See /usr/share/common-licenses/GPL-3"
    } > "$PKG/usr/share/doc/tilaser/copyright"
    SIZE="$(du -sk "$PKG" | cut -f1)"
    cat > "$PKG/DEBIAN/control" <<EOF
Package: tilaser
Version: $VERSION
Section: graphics
Priority: optional
Architecture: $DEB_ARCH
Maintainer: franzhefekranz <franzhefekranz@users.noreply.github.com>
Installed-Size: $SIZE
Depends: libc6 (>= $MIN_GLIBC), libgl1, libegl1, libfontconfig1, libdbus-1-3, libxkbcommon0, libxkbcommon-x11-0, libxcb-cursor0, libxcb-icccm4, libxcb-image0, libxcb-keysyms1, libxcb-render-util0, libxcb-shape0, libxcb-xkb1
Homepage: https://github.com/franzhefekranz/TiLaser
Description: Laser cutting and engraving for G-code based lasers
 TiLaser imports SVG, DXF, images and text, assigns cut, fill and raster
 layers with material presets, previews the toolpath and streams G-code
 to GRBL/FluidNC, Marlin or Smoothieware lasers via USB.
EOF
    cat > "$PKG/DEBIAN/postinst" <<'EOF'
#!/bin/sh
set -e
command -v update-desktop-database >/dev/null && update-desktop-database -q /usr/share/applications || true
command -v update-mime-database >/dev/null && update-mime-database /usr/share/mime || true
command -v gtk-update-icon-cache >/dev/null && gtk-update-icon-cache -q -t -f /usr/share/icons/hicolor || true
echo "TiLaser: Für den Zugriff auf den Laser (USB-Seriell) den Benutzer der Gruppe 'dialout' hinzufügen:"
echo "         sudo usermod -aG dialout \$USER   (danach ab- und wieder anmelden)"
exit 0
EOF
    cat > "$PKG/DEBIAN/postrm" <<'EOF'
#!/bin/sh
set -e
command -v update-desktop-database >/dev/null && update-desktop-database -q /usr/share/applications || true
command -v update-mime-database >/dev/null && update-mime-database /usr/share/mime || true
command -v gtk-update-icon-cache >/dev/null && gtk-update-icon-cache -q -t -f /usr/share/icons/hicolor || true
exit 0
EOF
    chmod 755 "$PKG/DEBIAN/postinst" "$PKG/DEBIAN/postrm"
    find "$PKG/opt" "$PKG/usr" -type d -exec chmod 755 {} +
    dpkg-deb --root-owner-group --build "$PKG" "$RELEASE/tilaser_${VERSION}_${DEB_ARCH}.deb"
    echo "Deb: $RELEASE/tilaser_${VERSION}_${DEB_ARCH}.deb"
fi
