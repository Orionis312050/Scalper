#!/bin/sh
set -e
# Les répertoires montés (bind mounts compose/Coolify) appartiennent souvent à root
# côté hôte : on les rend à l'utilisateur applicatif, puis on droppe les privilèges.
chown -R scalper:scalper /app/data /app/out 2>/dev/null || true
exec setpriv --reuid scalper --regid scalper --init-groups "$@"
