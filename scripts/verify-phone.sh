#!/usr/bin/env bash
# 手机上跑一遍只读自检，输出 PASS / FAIL 表。不改任何东西。
#
#   bash scripts/verify-phone.sh
set -uo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ASTRBOT_HOME="${ASTRBOT_HOME:-/persistent/astrbot}"
NAPCAT_HOME="${NAPCAT_HOME:-/persistent/napcat}"
DATA_DIR="$ASTRBOT_HOME/data"
# AstrBot 的 python：优先 ASTRBOT_PY，其次 App 自带的 venv，最后 profile 内的 .venv
PY="${ASTRBOT_PY:-}"
if [[ -z "$PY" ]]; then
  for cand in /opt/astrbot-venv/bin/python "$ASTRBOT_HOME/.venv/bin/python"; do
    [[ -x "$cand" ]] && { PY="$cand"; break; }
  done
fi
[[ -n "$PY" && -x "$PY" ]] || { echo "找不到 AstrBot 的 python：设 ASTRBOT_PY 指向它" >&2; exit 2; }
PASS=0; FAIL=0
ok()   { PASS=$((PASS+1)); printf '  PASS  %s\n' "$*"; }
bad()  { FAIL=$((FAIL+1)); printf '  FAIL  %s\n' "$*"; }
skip() { printf '  SKIP  %s\n' "$*"; }

[[ -f "$REPO_ROOT/.env" ]] && { set -a; . "$REPO_ROOT/.env"; set +a; }
WEB_PORT="${ASTRBOT_WEB_PORT:-6185}"
WS_PORT="${ASTRBOT_WS_PORT:-6199}"

# 端口探测：容器里没有 ss/lsof，netstat 在 proot 下读不到 /proc/net，只能用 socket
port_open() {
  "$PY" - "$1" <<'PY'
import socket, sys
port = int(sys.argv[1])
s = socket.socket(); s.settimeout(2)
sys.exit(0 if s.connect_ex(("127.0.0.1", port)) == 0 else 1)
PY
}

echo "== 1. 进程与端口 =="
if curl -fsS -m 5 -o /dev/null "http://127.0.0.1:${WEB_PORT}/" 2>/dev/null; then
  ok "AstrBot WebUI 在 ${WEB_PORT} 上有响应"
else
  bad "AstrBot WebUI ${WEB_PORT} 不通（没启动 / 端口不对）"
fi

if port_open "$WS_PORT"; then
  ok "反向 WS 服务在 ${WS_PORT} 监听（NapCat 要连它）"
else
  bad "反向 WS ${WS_PORT} 没在监听：AstrBot 还没起，或 platform 配置没生效"
fi

echo "== 2. 配置文件 =="
[[ -f "$DATA_DIR/cmd_config.json" ]] && ok "cmd_config.json 存在" || bad "cmd_config.json 不存在"
if [[ -f "$DATA_DIR/cmd_config.json" ]]; then
  "$PY" - "$DATA_DIR/cmd_config.json" <<'PY'
import json,sys
c=json.load(open(sys.argv[1],encoding='utf-8-sig'))
srcs=c.get('provider_sources') or [{}]
keys=srcs[0].get('key') or []
print('  ' + ('PASS' if keys and not str(keys[0]).startswith('sk-REPLACE') else 'FAIL') + '  DeepSeek API Key ' + ('已配置' if keys else '为空'))
pf=c.get('platform') or []
p=pf[0] if pf else {}
print('  ' + ('PASS' if p.get('enable') else 'FAIL') + '  OneBot 平台启用 (port=%s)' % p.get('ws_reverse_port'))
PY
fi

echo "== 3. 插件 =="
expect=(android_hands android_observer coo_memory execrelay feiyu_voice hb libido memprobe persona_manage proactive probe qq_voice_call)
for p in "${expect[@]}"; do
  d="$DATA_DIR/plugins/astrbot_plugin_$p"
  if [[ -d "$d" ]]; then
    if [[ -f "$d/main.py" && -f "$d/metadata.yaml" ]]; then ok "插件 astrbot_plugin_$p"; else bad "插件 astrbot_plugin_$p 缺 main.py / metadata.yaml"; fi
  else
    bad "插件 astrbot_plugin_$p 不存在"
  fi
done

echo "== 4. 人设 =="
if [[ -f "$DATA_DIR/data_v4.db" ]]; then
  "$PY" - "$DATA_DIR/data_v4.db" <<'PY'
import sqlite3,sys
c=sqlite3.connect(sys.argv[1])
rows=list(c.execute("select persona_id,length(system_prompt) from personas"))
ids={r[0]:r[1] for r in rows}
for pid in ('default','dafeiyu'):
    print('  ' + ('PASS' if ids.get(pid) else 'FAIL') + f'  人设 {pid} 长度 {ids.get(pid,0)}')
PY
else
  bad "data_v4.db 不存在"
fi

echo "== 5. NapCat =="
OB=$(ls "$NAPCAT_HOME"/config/onebot11_*.json 2>/dev/null | head -1)
if [[ -n "$OB" ]]; then
  ok "NapCat onebot 配置存在：$(basename "$OB")"
  "$PY" - "$OB" <<'PY'
import json,sys
c=json.load(open(sys.argv[1],encoding='utf-8-sig'))
cl=(c.get('network') or {}).get('websocketClients') or []
if cl:
    print('  PASS  有 websocketClient ->', cl[0].get('url'))
else:
    print('  FAIL  没有 websocketClient，NapCat 不会连 AstrBot')
PY
else
  bad "找不到 $NAPCAT_HOME/config/onebot11_*.json"
fi

echo
echo "PASS=$PASS FAIL=$FAIL"
[[ "$FAIL" -eq 0 ]] && echo "全部通过" || echo "有 FAIL 项，按上面逐条修"
exit $(( FAIL > 0 ? 1 : 0 ))
