#!/usr/bin/env bash
# media-deck launcher: native GTK4 single-window app.
# Wraps the app in steamdeck-touchfix so game mode passes real multi-touch.
# stdout/stderr (and MEDIA_DECK_DEBUG event log) go to ~/.cache/media-deck/app.log
APP_DIR="$(cd "$(dirname "$0")" && pwd)"
export GDK_BACKEND="wayland,x11"
if [ -z "$LANG" ]; then export LANG="zh_CN.UTF-8"; fi
if [ -z "$MEDIA_DECK_DEBUG" ]; then export MEDIA_DECK_DEBUG="0"; fi
LOG="$HOME/.cache/media-deck/app.log"
mkdir -p "$HOME/.cache/media-deck"
: > "$LOG"
TOUCHFIX="$APP_DIR/steamdeck-touchfix"
if [ -x "$TOUCHFIX" ]; then
  exec "$TOUCHFIX" -- python3 "$APP_DIR/app.py" "$@" >>"$LOG" 2>&1
fi
exec python3 "$APP_DIR/app.py" "$@" >>"$LOG" 2>&1
