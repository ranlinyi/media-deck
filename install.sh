#!/usr/bin/env bash
# media-deck: Desktop-Mode one-click installer for Steam Deck / SteamOS.
#
#  1) install the app into ~/.local/share/media-deck
#  2) fetch the missing GStreamer codec (H.264/AAC) into ./gst
#  3) create a desktop entry so it appears in "Add a Non-Steam Game"
#  4) install the 4 Steam artwork slots for the shortcut (when it exists)
#
# Usage:  ./install.sh
set -euo pipefail

SRC="$(cd "$(dirname "$0")" && pwd)"
DEST="$HOME/.local/share/media-deck"
STEAM="$HOME/.local/share/Steam"
APPS="$HOME/.local/share/applications"

say() { printf '%s\n' "$*"; }

say "== media-deck 安装 =="

# 1) files
mkdir -p "$DEST"
for f in app.py media.py launch.sh fetch-codec.sh steamdeck-touchfix README.md LICENSE; do
  if [ -f "$SRC/$f" ]; then cp -a "$SRC/$f" "$DEST/$f"; fi
done
chmod +x "$DEST/launch.sh" "$DEST/fetch-codec.sh" "$DEST/steamdeck-touchfix"
say "1/4  程序 -> $DEST"

# 2) codec
if [ -f "$DEST/gst/libgstlibav.so" ]; then
  say "2/4  解码器已存在"
else
  say "2/4  下载缺失解码器 ..."
  "$DEST/fetch-codec.sh" >/dev/null 2>&1 || say "     下载失败，可稍后重跑 $DEST/fetch-codec.sh"
fi

# 3) desktop entry
mkdir -p "$APPS"
cat > "$APPS/media-deck.desktop" <<'EOF'
[Desktop Entry]
Type=Application
Name=媒体库
Comment=图片 / 视频 / Steam 录像 浏览器
Exec=/home/deck/.local/share/media-deck/launch.sh --fullscreen
Terminal=false
Categories=AudioVideo;Player;Graphics;
Icon=multimedia-player
EOF
say "3/4  桌面项 -> $APPS/media-deck.desktop"

# 4) artwork
appid_line=""
if [ -d "$SRC/artwork" ]; then
  appid_line=$(python3 - "$STEAM" <<'PY' || true
import struct, sys, os, glob

def read_cstr(d, i):
    j = d.index(b"\x00", i)
    return d[i:j].decode("utf-8", "replace"), j + 1

def parse_obj(d, i):
    o = {}
    while i < len(d):
        t = d[i]; i += 1
        if t == 0x08:
            return o, i
        k, i = read_cstr(d, i)
        if t == 0x00:
            v, i = parse_obj(d, i)
        elif t == 0x01:
            v, i = read_cstr(d, i)
        elif t == 0x02:
            v = struct.unpack_from("<I", d, i)[0]; i += 4
        else:
            return o, i
        o[k] = v
    return o, i

steam = sys.argv[1]
for p in sorted(glob.glob(os.path.join(steam, "userdata", "*", "config", "shortcuts.vdf"))):
    try:
        d = open(p, "rb").read()
        i = 0; _t = d[i]; i += 1; _k, i = read_cstr(d, i)
        root, _ = parse_obj(d, i)
    except Exception:
        continue
    for sc in root.values():
        if isinstance(sc, dict) and "媒体库" in str(sc.get("AppName", "")):
            a = sc.get("appid")
            if isinstance(a, int) and a >= 2 ** 31:
                a -= 2 ** 32
            acct = os.path.basename(os.path.dirname(os.path.dirname(p)))
            print(acct, a)
            raise SystemExit(0)
PY
)
  if [ -n "$appid_line" ]; then
    acct=$(printf '%s' "$appid_line" | cut -d' ' -f1)
    appid=$(printf '%s' "$appid_line" | cut -d' ' -f2)
    gdir="$STEAM/userdata/$acct/config/grid"
    mkdir -p "$gdir"
    cp -f "$SRC/artwork/grid.png" "$gdir/$appid""p.png"
    cp -f "$SRC/artwork/wide.png" "$gdir/$appid"".png"
    cp -f "$SRC/artwork/hero.png" "$gdir/$appid""_hero.png"
    cp -f "$SRC/artwork/logo.png" "$gdir/$appid""_logo.png"
    say "4/4  四卡槽配图 -> $gdir (appid=$appid)"
  else
    say "4/4  还没找到「媒体库」快捷方式，配图暂未安装"
  fi
fi

if [ -z "$appid_line" ]; then
  cat <<'EOF'

还差一步（只做一次）：
  1. 打开 Steam（桌面模式）
  2. 左上「游戏」菜单 ->「添加非 Steam 游戏」
  3. 在列表里勾选「媒体库」->「添加」
  4. 再运行一次本脚本，会自动装好四卡槽配图

之后在游戏模式里打开「媒体库」即可。
EOF
fi
say "== 完成 =="
