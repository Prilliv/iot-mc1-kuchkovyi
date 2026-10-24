#!/usr/bin/env bash
# Створює .env із випадковими паролями на основі .env.example
set -euo pipefail
cd "$(dirname "$0")/.."
if [ -f .env ]; then echo ".env уже існує — не перезаписую."; exit 0; fi
while IFS= read -r line; do
  case "$line" in
    *=change_me) echo "${line%=change_me}=$(openssl rand -hex 12)" ;;
    HOST_UID=*)  echo "HOST_UID=$(id -u)" ;;
    HOST_GID=*)  echo "HOST_GID=$(id -g)" ;;
    *)           echo "$line" ;;
  esac
done < .env.example > .env
chmod 600 .env
echo "Створено .env (паролі випадкові)."
