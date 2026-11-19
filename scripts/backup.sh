#!/usr/bin/env bash
set -euo pipefail
umask 077
source_db=${1:-/var/lib/pastel-de-nata/pastel.sqlite}
destination=${2:-/var/backups/pastel-de-nata}
install -d -m 700 "$destination"
python3 - "$source_db" "$destination" <<'PY'
import os, sqlite3, sys, tempfile
from datetime import datetime, timezone
from pathlib import Path
source = Path(sys.argv[1]).resolve(strict=True)
directory = Path(sys.argv[2])
name = datetime.now(timezone.utc).strftime("pastel-%Y%m%dT%H%M%S.%fZ.sqlite")
fd, temp = tempfile.mkstemp(dir=directory)
os.close(fd)
try:
    with sqlite3.connect(source.as_uri() + '?mode=ro', uri=True) as original, sqlite3.connect(temp) as backup:
        original.backup(backup)
        if backup.execute("PRAGMA quick_check").fetchone()[0] != "ok":
            raise RuntimeError("A cópia de segurança falhou na verificação de integridade.")
    os.replace(temp, directory / name)
    print(directory / name)
finally:
    Path(temp).unlink(missing_ok=True)
PY
