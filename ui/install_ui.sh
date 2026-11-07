#!/bin/sh
# Usage : ./install_ui.sh sao /chemin/vers/sao      |   ./install_ui.sh gtk
set -e
here=$(cd "$(dirname "$0")" && pwd)
case "$1" in
  sao) [ -f "$2/index.html" ] || { echo "Dossier SAO invalide (index.html absent)"; exit 1; }
       cp "$here/sao/custom.css" "$2/custom.css"; echo "custom.css installé dans $2" ;;
  gtk) d="${XDG_CONFIG_HOME:-$HOME/.config}/tryton/7.0"; mkdir -p "$d"
       cp "$here/gtk/theme.css" "$d/theme.css"
       grep -q '^\[tree\]' "$d/tryton.conf" 2>/dev/null || cat "$here/gtk/tryton.conf.sample" | grep -v '^;' >> "$d/tryton.conf"
       echo "Thème installé dans $d (redémarrer le client)" ;;
  *) echo "Usage: $0 sao <dossier_sao> | gtk"; exit 1 ;;
esac
