#!/usr/bin/env bash
set -euo pipefail
if [[ $EUID != 0 ]]; then echo 'Executa com sudo.' >&2; exit 1; fi
exec 9>/run/lock/pastel-de-nata-update.lock
flock -n 9
app=/opt/pastel-de-nata
cd "$app"
if [[ $(git remote get-url origin) != https://github.com/costinhabtw/pastel-de-nata.git ]] || [[ -n $(git status --porcelain) ]]; then
    echo 'A cópia de instalação tem alterações locais ou uma origem inesperada.' >&2
    exit 1
fi
git fetch origin main
previous=$(git rev-parse HEAD)
target=$(git rev-parse origin/main)
if [[ $previous == "$target" ]]; then
    echo 'O código já está atualizado.'
    exit 0
fi
git merge-base --is-ancestor "$previous" "$target"
backup=$(bash scripts/backup.sh)
printf 'Cópia anterior à atualização: %s\n' "$backup"
git merge --ff-only "$target"
if ! .venv/bin/python -m pip install -r requirements.txt || ! .venv/bin/python -m unittest discover -s tests; then
    git checkout --detach "$previous"
    .venv/bin/python -m pip install -r requirements.txt
    echo 'A atualização falhou nos testes. Foi reposto o código anterior.' >&2
    exit 1
fi
systemctl restart pastel-de-nata
systemctl status pastel-de-nata --no-pager
