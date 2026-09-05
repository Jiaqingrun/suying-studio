#!/usr/bin/env bash
# Seed advanced/ops password verifier into macOS Keychain (salted SHA-256 only).
# Reads plaintext from a local 0600 file — never from the git repo.
# Default file: ~/Suying/runtime/security/ops-password
set -euo pipefail

SERVICE="com.qr.suying"
ACCOUNT="advanced-settings-password"
PASSWORD_FILE="${SUYING_OPS_PASSWORD_FILE:-$HOME/Suying/runtime/security/ops-password}"

if [[ "$(uname -s)" != "Darwin" ]]; then
  echo "macOS Keychain only" >&2
  exit 1
fi

if [[ ! -f "$PASSWORD_FILE" ]]; then
  echo "missing password file: $PASSWORD_FILE" >&2
  echo "create it with chmod 600; one-line plaintext; do not commit" >&2
  exit 1
fi

perm=$(stat -f '%Lp' "$PASSWORD_FILE" 2>/dev/null || echo "")
if [[ "$perm" != "600" && "$perm" != "400" ]]; then
  echo "password file mode must be 600 or 400 (now $perm): $PASSWORD_FILE" >&2
  exit 1
fi

PASSWORD="$(tr -d '\r\n' <"$PASSWORD_FILE")"
if [[ ${#PASSWORD} -lt 6 ]]; then
  echo "password must be at least 6 chars" >&2
  exit 1
fi

RECORD="$(
  PASSWORD="$PASSWORD" /usr/bin/python3 - <<'PY'
import hashlib, os, secrets
password = os.environ["PASSWORD"].encode("utf-8")
salt = secrets.token_bytes(16)
h = hashlib.sha256()
h.update(b"suying-advanced-v1")
h.update(salt)
h.update(password)
print(f"{salt.hex()}:{h.hexdigest()}")
PY
)"

/usr/bin/security delete-generic-password -s "$SERVICE" -a "$ACCOUNT" >/dev/null 2>&1 || true
/usr/bin/security add-generic-password -U -s "$SERVICE" -a "$ACCOUNT" -w "$RECORD"

echo "seeded keychain hash for ${SERVICE}/${ACCOUNT}; plaintext not in repo"
unset PASSWORD RECORD
