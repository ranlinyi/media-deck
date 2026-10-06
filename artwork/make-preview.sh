#!/usr/bin/env bash
# 生成 README 顶部的配图总览（preview.png）。
# 依赖：ImageMagick 7（magick）。在 artwork/ 目录下执行：./make-preview.sh
set -euo pipefail
cd "$(dirname "$0")"

OUT=preview.png
BG="#05070d"
W=1936
H=733

command -v magick >/dev/null 2>&1 || { echo "需要 ImageMagick 7 的 magick 命令" >&2; exit 1; }

magick -size "$W"x"$H" xc:"$BG" \
  \( grid.png -resize 476x714 \) -geometry +14+10 -composite \
  \( wide.png -resize 460x215 \) -geometry +524+250 -composite \
  \( hero.png -resize 460x149 \) -geometry +1010+286 -composite \
  \( logo.png -resize 400x126 \) -geometry +1496+300 -composite \
  -strip -depth 8 "$OUT"

identify "$OUT"
echo "已生成 $OUT"
