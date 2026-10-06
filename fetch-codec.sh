#!/usr/bin/env bash
# Fetch the GStreamer libav plugin (H.264/AAC decoders) that SteamOS omits,
# into ./gst so the native app can use it WITHOUT touching the read-only system.
set -e
APP_DIR="$(cd "$(dirname "$0")" && pwd)"
DEST="$APP_DIR/gst"
URL="https://steamdeck-packages.steamos.cloud/archlinux-mirror/extra-3.8/os/x86_64/gst-libav-1.26.4-1-x86_64.pkg.tar.zst"
mkdir -p "$DEST"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
echo "downloading $URL"
curl -fsSL -o "$TMP/pkg.tar.zst" "$URL"
if command -v bsdtar >/dev/null 2>&1; then
  bsdtar -xf "$TMP/pkg.tar.zst" -C "$TMP"
else
  tar --zstd -xf "$TMP/pkg.tar.zst" -C "$TMP"
fi
cp "$TMP"/usr/lib/gstreamer-1.0/libgstlibav.so "$DEST/"
echo "installed: $DEST/libgstlibav.so"
echo "verify decoders:"
GST_PLUGIN_PATH="$DEST" gst-inspect-1.0 2>/dev/null | grep -E "avdec_h264|avdec_aac" | head
