#!/bin/sh
set -e
# Les répertoires montés (bind mounts compose/Coolify) appartiennent souvent à root
# côté hôte : on les rend à l'utilisateur applicatif, puis on droppe les privilèges.
chown -R scalper:scalper /app/data /app/out 2>/dev/null || true
# Une fois les privilèges droppés (setpriv), HOME doit pointer vers le home de
# `scalper` : sinon PaddleX tente d'écrire dans /root/.paddlex -> Permission denied.
export HOME=/home/scalper
# Cache PaddleX (modèles OCR + temp) dans le volume monté /app/data :
# les modèles ne sont téléchargés qu'une seule fois, même après recréation du conteneur.
export PADDLE_PDX_CACHE_HOME=/app/data/.paddlex
# Idem pour le modèle LaMa d'inpainting (big-lama ONNX, ~208 Mo, téléchargé au premier run).
export SCALPER_LAMA_CACHE=/app/data/.lama
exec setpriv --reuid scalper --regid scalper --init-groups "$@"
