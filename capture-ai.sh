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
    GEOM="$(slurp 2>/dev/null || true)"
    [ -z "$GEOM" ] && exit 0

    POS_PART="${GEOM%% *}"
    SIZE_PART="${GEOM#* }"

    X="${POS_PART%,*}"
    Y="${POS_PART#*,}"
    W="${SIZE_PART%x*}"
    H="${SIZE_PART#*x}"

    case "$X" in ''|*[!0-9]*) exit 0 ;; esac
    case "$Y" in ''|*[!0-9]*) exit 0 ;; esac
    case "$W" in ''|*[!0-9]*) exit 0 ;; esac
    case "$H" in ''|*[!0-9]*) exit 0 ;; esac

    # Tek tıklama -> tıklanan monitörün tamamı
    if [ "$W" -le 5 ] || [ "$H" -le 5 ]; then
      IMG="$IMG_DIR/tam-ekran-$(date +%Y%m%d-%H%M%S).png"

      MON_GEOM="$(
        hyprctl monitors -j 2>/dev/null | python3 -c '
import sys, json

x = int(sys.argv[1])
y = int(sys.argv[2])

try:
    mons = json.load(sys.stdin)
except Exception:
    sys.exit(1)

for m in mons:
    mx = int(m.get("x", 0))
    my = int(m.get("y", 0))
    mw = int(m.get("width", 0))
    mh = int(m.get("height", 0))

    if mx <= x < mx + mw and my <= y < my + mh:
        print(f"{mx},{my} {mw}x{mh}")
        sys.exit(0)

sys.exit(1)
' "$X" "$Y"
      )"

      [ -z "$MON_GEOM" ] && exit 0

      grim -g "$MON_GEOM" "$IMG"
    else
      IMG="$IMG_DIR/secili-alan-$(date +%Y%m%d-%H%M%S).png"
      grim -g "$GEOM" "$IMG"
    fi

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
