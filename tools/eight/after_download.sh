#!/bin/bash
# Tras la descarga de la carpeta alias (bajar_todo.py: fichas → imágenes → vías con trazo → vias.csv): importar los
# trazos en la base del proxy y bajar las fotos sin líneas. Se lanza con nohup y se puede repetir.
#   nohup tools/eight/after_download.sh ~/tmp/8a-alias > ~/.local/share/8anu-proxy/after_download.log 2>&1 &
set -u
SRC=${1:-$HOME/tmp/8a-alias}
cd "$(dirname "$0")/../.."
while ! test -s "$SRC/datos/vias.csv"; do sleep 300; done
echo "$(date +%T) vias.csv listo: importando trazos"
uv run --frozen tools/eight/import_topos.py "$SRC" --only-routes
echo "$(date +%T) bajando fotos sin líneas"
uv run --frozen tools/eight/fetch_blank.py --cookie "$SRC/ck.txt"
echo "$(date +%T) fin"
