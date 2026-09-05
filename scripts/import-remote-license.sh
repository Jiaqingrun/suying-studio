#!/usr/bin/env bash
# Light-weight remote license import (renewal path). Does not redeploy App/models.
set -euo pipefail

HOST=""
LICENSE=""

usage() {
  cat <<'EOF'
Usage: import-remote-license.sh --host <ssh> --license <file.suying-license>

Copies the signed license onto the remote machine under:
  ~/Suying/runtime/security/license.suying-license
Does not push App, models, or offline tools.
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --host) HOST="${2:-}"; shift 2 ;;
    --license) LICENSE="${2:-}"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "unknown arg: $1" >&2; usage; exit 2 ;;
  esac
done

[[ -n "$HOST" ]] || { echo "缺少 --host" >&2; exit 2; }
[[ -f "$LICENSE" ]] || { echo "许可证不存在: $LICENSE" >&2; exit 2; }

STAGE="/tmp/suying-license-import-$$.suying-license"
scp -q "$LICENSE" "${HOST}:${STAGE}"
ssh "$HOST" "mkdir -p \"\$HOME/Suying/runtime/security\" && chmod 700 \"\$HOME/Suying/runtime/security\" && install -m 600 ${STAGE} \"\$HOME/Suying/runtime/security/license.suying-license\" && rm -f ${STAGE} && echo LICENSE_IMPORTED path=\$HOME/Suying/runtime/security/license.suying-license"

echo "import-remote-license: host=$HOST ok"
