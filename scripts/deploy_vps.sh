#!/usr/bin/env bash
# Publica una nueva version en el VPS: sube el codigo, reconstruye la imagen y actualiza el servicio de Easypanel.
#   ./scripts/deploy_vps.sh root@IP_DEL_VPS     (o define VPS_HOST=root@IP_DEL_VPS)
# Los datos (/opt/landing-agent/data) no se tocan.
set -euo pipefail
HOST="${1:-${VPS_HOST:?Indica el servidor: ./scripts/deploy_vps.sh root@IP_DEL_VPS}}"
SERVICE="${SERVICE:-}"  # nombre del servicio en Swarm (ej: embudos_landing-agent); vacio = se detecta solo
cd "$(dirname "$0")/.."

./venv/bin/python -m pytest -q
tar czf - --exclude='__pycache__' Dockerfile requirements.txt main.py app automations README.md DEMO.md .env.example \
  | ssh "$HOST" "mkdir -p /opt/landing-agent/src && rm -rf /opt/landing-agent/src/* && tar xzf - -C /opt/landing-agent/src"
ssh "$HOST" "set -e; cd /opt/landing-agent/src && docker build -q -t landing-agent:latest . >/dev/null
  svc=\"${SERVICE}\"; [ -n \"\$svc\" ] || svc=\$(docker service ls --format '{{.Name}} {{.Image}}' | awk '\$2 ~ /^landing-agent/ {print \$1; exit}')
  if [ -n \"\$svc\" ]; then docker service update --force --image landing-agent:latest \"\$svc\" >/dev/null && echo \"Servicio \$svc actualizado\";
  else echo 'No encontre el servicio landing-agent (ver DEPLOY.md)'; fi"
