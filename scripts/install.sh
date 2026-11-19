#!/usr/bin/env bash
set -euo pipefail
if [[ $EUID != 0 ]]; then echo 'Executa com sudo.' >&2; exit 1; fi
. /etc/os-release
if [[ $ID != ubuntu || $VERSION_ID != 24.04 ]]; then echo 'Utiliza Ubuntu 24.04 LTS.' >&2; exit 1; fi
if command -v docker >/dev/null && docker ps --filter label=com.docker.compose.project=pastel-de-nata --format '{{.Names}}' | grep -q .; then
    echo 'A instância Docker está ativa. Segue a migração documentada no README antes de arrancar systemd.' >&2
    exit 1
fi
apt-get update
apt-get install -y git python3 python3-venv
if ! id pastel-de-nata >/dev/null 2>&1; then
    useradd --system --user-group --home-dir /var/lib/pastel-de-nata --shell /usr/sbin/nologin pastel-de-nata
fi
install -d -m 0750 -o pastel-de-nata -g pastel-de-nata /var/lib/pastel-de-nata
install -d -m 0750 /etc/pastel-de-nata
app=/opt/pastel-de-nata
if [[ ! -d $app/.git ]]; then
    git clone --branch main https://github.com/costinhabtw/pastel-de-nata.git "$app"
fi
if [[ $(git -C "$app" remote get-url origin) != https://github.com/costinhabtw/pastel-de-nata.git ]]; then
    echo 'O diretório da aplicação contém outro repositório.' >&2
    exit 1
fi
python3 -m venv "$app/.venv"
"$app/.venv/bin/python" -m pip install -r "$app/requirements.txt"
if [[ ! -f /etc/pastel-de-nata/bot.env ]]; then
    install -m 0600 "$app/.env.example" /etc/pastel-de-nata/bot.env
fi
install -m 0644 "$app/systemd/pastel-de-nata.service" /etc/systemd/system/pastel-de-nata.service
systemctl daemon-reload
if ! grep -Eq '^DISCORD_TOKEN=.+$' /etc/pastel-de-nata/bot.env || ! grep -Eq '^DISCORD_GUILD_ID=[0-9]{17,20}$' /etc/pastel-de-nata/bot.env; then
    echo 'Preenche /etc/pastel-de-nata/bot.env e volta a executar este instalador.'
    exit 0
fi
systemctl enable --now pastel-de-nata
systemctl status pastel-de-nata --no-pager
