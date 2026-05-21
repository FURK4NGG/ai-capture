#!/bin/bash
[ -f "$HOME/.config/capture-ai/env.sh" ] && source "$HOME/.config/capture-ai/env.sh"

set -e

BASE="$HOME/.cache/capture-ai"
IMG_DIR="$BASE/images"
CHAT_DIR="$BASE/chats"

mkdir -p "$IMG_DIR"
mkdir -p "$CHAT_DIR"

MODE="$1"

case "$MODE" in

  image)
    SCREENSHOT_SCRIPT="$HOME/.config/scripts/screenprint.sh"

    if [ ! -f "$SCREENSHOT_SCRIPT" ]; then
      echo "Screenshot script not found: $SCREENSHOT_SCRIPT" >&2
      exit 1
    fi

    IMG="$("$SCREENSHOT_SCRIPT" only-one | tail -n 1)"

    [ -z "$IMG" ] && exit 0
    [ ! -f "$IMG" ] && exit 0

    python "$HOME/capture-ai/ui.py" "$IMG"
    ;;

  text)
    python "$HOME/capture-ai/ui.py"
    ;;

  cli)
    python3 "$HOME/capture-ai/cli.py"
    ;;

  *)
    echo "Usage:"
    echo "  capture-ai.sh image"
    echo "  capture-ai.sh text"
    echo "  capture-ai.sh cli"
    ;;
esac
