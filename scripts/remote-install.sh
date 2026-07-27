#!/usr/bin/env bash
# Run on the customer Mac after the product kit has been transferred.
set -euo pipefail

APP_SOURCE=""
CUSTOMER_NAME=""
CUSTOMER_CONFIG=""
INSTALL_DEPS=0
BIND_ZSPACE=0
SMOKE_JOB=0
PORT="${SUYING_PORT:-8766}"

usage() {
  cat <<'EOF'
用法:
  remote-install.sh --app-source /path/to/速影.app --customer-name NAME [选项]

选项:
  --customer-config DIR   私下传入的正式客户配置（keyword-pack.json + brand/）
  --install-deps         用已有 Homebrew 安装缺失的 Ollama / FFmpeg
  --bind-active-zspace   绑定极空间客户端当前登录账号与 nas_id
  --smoke-job            片库有素材时创建 1 条真实任务并等待接单
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --app-source) APP_SOURCE="${2:-}"; shift 2 ;;
    --customer-name) CUSTOMER_NAME="${2:-}"; shift 2 ;;
    --customer-config) CUSTOMER_CONFIG="${2:-}"; shift 2 ;;
    --install-deps) INSTALL_DEPS=1; shift ;;
    --bind-active-zspace) BIND_ZSPACE=1; shift ;;
    --smoke-job) SMOKE_JOB=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "未知参数: $1" >&2; usage; exit 2 ;;
  esac
done

if [[ -z "$APP_SOURCE" || ! -d "$APP_SOURCE" || -z "$CUSTOMER_NAME" ]]; then
  usage
  exit 2
fi

log() { printf '==> %s\n' "$*"; }
fail() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }

export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:${PATH:-}"
INSTALL_ROOT="${HOME}/Applications"
APP="${INSTALL_ROOT}/速影.app"
BACKUP_ROOT="${HOME}/Suying/backups/apps"
STAGE="${INSTALL_ROOT}/.速影.app.installing"
mkdir -p "$INSTALL_ROOT" "$BACKUP_ROOT" "${HOME}/Suying/logs"

log "安装 App（保留可回滚旧版本）"
rm -rf "$STAGE"
ditto "$APP_SOURCE" "$STAGE"
xattr -dr com.apple.quarantine "$STAGE" 2>/dev/null || true
codesign --verify --deep --strict "$STAGE" || fail "App 签名校验失败"

PY="${STAGE}/Contents/Resources/runtime/python/bin/python3"
STUDIO="${STAGE}/Contents/Resources/runtime/studio"
[[ -x "$PY" && -d "$STUDIO/engine" ]] || fail "一体包缺少内嵌 Python 或引擎"
if [[ -f "${STAGE}/Contents/Resources/runtime/python/pyvenv.cfg" ]] &&
   grep -Eq '^home = /(Users|opt)/' "${STAGE}/Contents/Resources/runtime/python/pyvenv.cfg"; then
  fail "内嵌 Python 仍绑定打包机；必须使用 standalone CPython 重打包"
fi
"$PY" -c 'import encodings,fastapi,uvicorn; print("standalone python ok")' ||
  fail "内嵌 Python 不可迁移"

if [[ -d "$APP" ]]; then
  stamp="$(date -u +%Y%m%dT%H%M%SZ)"
  mv "$APP" "${BACKUP_ROOT}/速影.app.${stamp}"
fi
mv "$STAGE" "$APP"
rm -rf "${HOME}/Desktop/速影.app"
ln -s "$APP" "${HOME}/Desktop/速影.app"

if [[ -w /Applications ]]; then
  rm -rf "/Applications/速影.app"
  ln -s "$APP" "/Applications/速影.app"
fi

log "检查本机依赖"
if ! command -v brew >/dev/null 2>&1 && [[ "$INSTALL_DEPS" == "1" ]]; then
  fail "缺少 Homebrew；为避免静默修改系统，请先人工安装 https://brew.sh"
fi
if ! command -v ffmpeg >/dev/null 2>&1; then
  if [[ "$INSTALL_DEPS" != "1" ]]; then
    fail "缺少 FFmpeg；重跑时加 --install-deps"
  fi
  HOMEBREW_NO_AUTO_UPDATE=1 HOMEBREW_NO_ENV_HINTS=1 brew install ffmpeg
fi
ffmpeg -version | sed -n '1p'

if ! curl -fsS --max-time 3 "http://127.0.0.1:11434/api/tags" >/dev/null 2>&1; then
  if ! command -v ollama >/dev/null 2>&1; then
    if [[ "$INSTALL_DEPS" != "1" ]]; then
      fail "缺少 Ollama；重跑时加 --install-deps"
    fi
    HOMEBREW_NO_AUTO_UPDATE=1 HOMEBREW_NO_ENV_HINTS=1 brew install --cask ollama
  fi
  open -a Ollama >/dev/null 2>&1 || true
  for _ in $(seq 1 60); do
    curl -fsS --max-time 2 "http://127.0.0.1:11434/api/tags" >/dev/null 2>&1 && break
    sleep 1
  done
fi
curl -fsS --max-time 3 "http://127.0.0.1:11434/api/tags" >/dev/null ||
  fail "Ollama 未启动；请登录桌面后打开 Ollama"

log "启动内嵌引擎"
old_pids="$(lsof -t -iTCP:"$PORT" -sTCP:LISTEN 2>/dev/null || true)"
[[ -z "$old_pids" ]] || kill $old_pids 2>/dev/null || true
sleep 1
PY="${APP}/Contents/Resources/runtime/python/bin/python3"
STUDIO="${APP}/Contents/Resources/runtime/studio"
ENGINE_LOG="${HOME}/Suying/logs/remote-install-engine.log"
(
  cd "$STUDIO"
  HOME="$HOME" PYTHONPATH="$STUDIO" PYTHONDONTWRITEBYTECODE=1 \
    PATH="$PATH" MONTAGE_PORT="$PORT" nohup "$PY" -m engine.main \
    >"$ENGINE_LOG" 2>&1 < /dev/null &
  echo $! > "${HOME}/Suying/engine.pid"
)
API="http://127.0.0.1:${PORT}"
for _ in $(seq 1 80); do
  curl -fsS --max-time 2 "${API}/health" >/dev/null 2>&1 && break
  sleep 0.5
done
curl -fsS --max-time 5 "${API}/health" >/dev/null || {
  tail -80 "$ENGINE_LOG" >&2 || true
  fail "引擎 health 未就绪"
}

log "检查 Ollama 必需模型"
MODEL_LINES="$(
  curl -fsS "${API}/health" | "$PY" -c '
import json,sys
d=json.load(sys.stdin)
r=((d.get("ollama") or {}).get("recommended") or {})
for model in (r.get("embed_model"), r.get("vision_model")):
    if model:
        print(model)
'
)"
while IFS= read -r model; do
  [[ -n "$model" ]] || continue
  if ! ollama list 2>/dev/null | awk 'NR>1 {print $1}' | grep -Fxq "$model"; then
    [[ "$INSTALL_DEPS" == "1" ]] ||
      fail "缺少 Ollama 模型 ${model}；重跑时加 --install-deps"
    ollama pull "$model"
  fi
done <<< "$MODEL_LINES"

log "创建并激活客户"
CUSTOMER_NAME="$CUSTOMER_NAME" API="$API" "$PY" - <<'PY'
import json
import os
import urllib.error
import urllib.request

api = os.environ["API"]
name = os.environ["CUSTOMER_NAME"]

def request(method, path, body=None):
    data = None if body is None else json.dumps(body, ensure_ascii=False).encode()
    req = urllib.request.Request(
        api + path,
        data=data,
        method=method,
        headers={"Content-Type": "application/json"} if body is not None else {},
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as response:
            raw = response.read()
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"{method} {path}: {exc.code} {exc.read().decode()}") from exc

ensured = request("POST", "/ops/zspace-sync/ensure-customer", {"name": name, "register": True})
p = ensured["paths"]
customers = request("GET", "/customers")
customer = next((item for item in customers if item["name"] == name), None)
payload = {
    "name": name,
    "library_root": p["library_root"],
    "output_root": p["output_root"],
    "keyword_pack_path": p.get("keyword_pack_path") or None,
}
if customer is None:
    customer = request("POST", "/customers", payload)
else:
    customer = request("PATCH", f"/customers/{customer['id']}", payload)
request("POST", "/customers/activate", {"name": name})
request("PUT", "/settings", {"onboarded": True, "active_customer": name})
print(json.dumps({"customer_id": customer["id"], "paths": p}, ensure_ascii=False))
PY

CUSTOMER_ROOT="${HOME}/Suying/sync/速影客户/${CUSTOMER_NAME}"
if [[ -n "$CUSTOMER_CONFIG" ]]; then
  [[ -d "$CUSTOMER_CONFIG" ]] || fail "客户配置目录不存在: $CUSTOMER_CONFIG"
  log "导入正式客户配置"
  if [[ -f "${CUSTOMER_CONFIG}/keyword-pack.json" ]]; then
    mkdir -p "${CUSTOMER_ROOT}/03-词池"
    cp -f "${CUSTOMER_CONFIG}/keyword-pack.json" "${CUSTOMER_ROOT}/03-词池/keyword-pack.json"
  fi
  if [[ -d "${CUSTOMER_CONFIG}/brand" ]]; then
    mkdir -p "${CUSTOMER_ROOT}/05-品牌"
    rsync -a --exclude '*.mp4' --exclude '*.mov' --exclude '.env*' \
      "${CUSTOMER_CONFIG}/brand/" "${CUSTOMER_ROOT}/05-品牌/"
  fi
  if [[ -f "${CUSTOMER_CONFIG}/profile.sample.json" ]]; then
    cp -f "${CUSTOMER_CONFIG}/profile.sample.json" "${CUSTOMER_ROOT}/profile.sample.json"
  fi
fi

log "重载词池并绑定当前极空间"
CUSTOMER_NAME="$CUSTOMER_NAME" CUSTOMER_ROOT="$CUSTOMER_ROOT" API="$API" \
  BIND_ZSPACE="$BIND_ZSPACE" "$PY" - <<'PY'
import json
import os
import urllib.error
import urllib.request
from pathlib import Path

api = os.environ["API"]
name = os.environ["CUSTOMER_NAME"]
root = Path(os.environ["CUSTOMER_ROOT"])

def request(method, path, body=None):
    data = None if body is None else json.dumps(body, ensure_ascii=False).encode()
    req = urllib.request.Request(
        api + path,
        data=data,
        method=method,
        headers={"Content-Type": "application/json"} if body is not None else {},
    )
    with urllib.request.urlopen(req, timeout=30) as response:
        raw = response.read()
        return json.loads(raw) if raw else {}

customers = request("GET", "/customers")
customer = next(item for item in customers if item["name"] == name)
pack = root / "03-词池" / "keyword-pack.json"
profile_file = root / "profile.sample.json"
patch = {}
if pack.is_file():
    patch["keyword_pack_path"] = str(pack)
if profile_file.is_file():
    profile = json.loads(profile_file.read_text(encoding="utf-8"))
    if isinstance(profile, dict):
        patch["profile_json"] = profile
if patch:
    request("PATCH", f"/customers/{customer['id']}", patch)
if pack.is_file():
    request("POST", f"/keywords/reload-from-path?customer_id={customer['id']}")

if os.environ["BIND_ZSPACE"] == "1":
    vuex = Path.home() / "Library/Application Support/zspace/vuex.json"
    if not vuex.is_file():
        raise RuntimeError("NEED_HUMAN: 极空间客户端未登录")
    state = (json.loads(vuex.read_text(encoding="utf-8")).get("state") or {})
    user = state.get("user") or {}
    nas = state.get("nas") or {}
    body = {
        "username": str(user.get("username") or "").strip(),
        "nas_id": str(nas.get("nasId") or "").strip(),
        "nas_name": str(nas.get("nasName") or "").strip(),
    }
    if not body["username"] or not body["nas_id"] or not user.get("token"):
        raise RuntimeError("NEED_HUMAN: 极空间账号或设备未处于登录状态")
    request("POST", "/ops/zspace-sync/bind", body)
print(json.dumps(request("GET", "/keywords/active-summary"), ensure_ascii=False))
PY

if [[ "$BIND_ZSPACE" == "1" ]]; then
  bash "${APP}/Contents/Resources/runtime/studio/scripts/install-sync-service.sh" \
    --nas-user "$(
      "$PY" -c 'import json,pathlib; s=json.loads((pathlib.Path.home()/"Library/Application Support/zspace/vuex.json").read_text())["state"]; print(s["user"]["username"])'
    )" \
    --nas-id "$(
      "$PY" -c 'import json,pathlib; s=json.loads((pathlib.Path.home()/"Library/Application Support/zspace/vuex.json").read_text())["state"]; print(s["nas"]["nasId"])'
    )" \
    --nas-name "$(
      "$PY" -c 'import json,pathlib; s=json.loads((pathlib.Path.home()/"Library/Application Support/zspace/vuex.json").read_text())["state"]; print(s["nas"].get("nasName") or "")'
    )"
  curl -fsS -X POST "${API}/ops/carrier/install-update-agent" >/dev/null
fi

log "执行部署门禁"
HEALTH_JSON="$(curl -fsS "${API}/health")"
HEALTH_JSON="$HEALTH_JSON" CUSTOMER_NAME="$CUSTOMER_NAME" "$PY" - <<'PY'
import json
import os
d = json.loads(os.environ["HEALTH_JSON"])
assert d.get("status") == "ok", d
host = ((d.get("ollama") or {}).get("host") or {})
assert host.get("ffmpeg_ok") is True, host
assert host.get("ollama_cli") is True, host
assert (d.get("ollama") or {}).get("ready") is True, d.get("ollama")
assert d.get("active_customer") == os.environ.get("CUSTOMER_NAME", d.get("active_customer"))
print("health gate ok")
PY

CORS_HEADERS="$(
  curl -si -X OPTIONS "${API}/jobs" \
    -H 'Origin: http://tauri.localhost' \
    -H 'Access-Control-Request-Method: POST' \
    -H 'Access-Control-Request-Headers: content-type'
)"
printf '%s\n' "$CORS_HEADERS" | tr -d '\r' |
  grep -Eqi '^access-control-allow-origin: http://tauri\.localhost$' ||
  fail "Tauri CORS 预检未放行"

if [[ "$SMOKE_JOB" == "1" ]]; then
  media_count="$(
    find "${CUSTOMER_ROOT}/01-片库" -type f \
      \( -iname '*.mp4' -o -iname '*.mov' -o -iname '*.m4v' -o -iname '*.mkv' \) |
      wc -l | tr -d ' '
  )"
  [[ "$media_count" -gt 0 ]] || fail "NEED_MEDIA: 片库为空，无法做真实建任务验收"
  JOB_JSON="$(
    curl -fsS -X POST "${API}/jobs" \
      -H 'Origin: http://tauri.localhost' \
      -H 'Content-Type: application/json' \
      -d '{"mode":"count","target_count":1,"template_name":"default-vertical","theme":"default"}'
  )"
  JOB_JSON="$JOB_JSON" "$PY" -c \
    'import json,os; d=json.loads(os.environ["JOB_JSON"]); assert d.get("id"), d; print("job gate ok", d["id"])'
fi

open "$APP" >/dev/null 2>&1 || true
log "远程部署完成"
