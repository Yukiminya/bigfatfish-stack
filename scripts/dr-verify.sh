#!/usr/bin/env bash
# 灾难恢复演练第三步：比 verify-phone.sh 更严的只读验收。
#
#   bash scripts/dr-verify.sh                  # 检查，含「必须是干净重装」的判定
#   bash scripts/dr-verify.sh --allow-context  # 允许 conversations 非空（日常检查时用这个）
#
# 除了 verify-phone.sh 的检查，额外要求：
#   * 人设 default 长度 > 1500（防止只写了空壳）
#   * 插件配置齐全（6 个 json 都在）
#   * conversations 表为 0 行（证明旧上下文没被注入）
#   * NapCat 的 websocketClient 指向 6199
set -uo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ASTRBOT_HOME="${ASTRBOT_HOME:-/persistent/astrbot}"
NAPCAT_HOME="${NAPCAT_HOME:-/persistent/napcat}"
DATA="$ASTRBOT_HOME/data"
ALLOW_CONTEXT=0
[[ "${1:-}" == "--allow-context" ]] && ALLOW_CONTEXT=1

PY="${ASTRBOT_PY:-}"
if [[ -z "$PY" ]]; then
  for cand in /opt/astrbot-venv/bin/python "$ASTRBOT_HOME/.venv/bin/python"; do
    [[ -x "$cand" ]] && { PY="$cand"; break; }
  done
fi
[[ -n "$PY" && -x "$PY" ]] || { echo "找不到 AstrBot 的 python：设 ASTRBOT_PY" >&2; exit 2; }
[[ -f "$REPO_ROOT/.env" ]] && { set -a; . "$REPO_ROOT/.env"; set +a; }
WS_PORT="${ASTRBOT_WS_PORT:-6199}"

PASS=0; FAIL=0
ok()  { PASS=$((PASS+1)); printf '  PASS  %s\n' "$*"; }
bad() { FAIL=$((FAIL+1)); printf '  FAIL  %s\n' "$*"; }

port_open() {
  "$PY" - "$1" <<'PY'
import socket, sys
s = socket.socket(); s.settimeout(2)
sys.exit(0 if s.connect_ex(("127.0.0.1", int(sys.argv[1]))) == 0 else 1)
PY
}

echo "== 1. 服务 =="
curl -fsS -m 5 -o /dev/null "http://127.0.0.1:${ASTRBOT_WEB_PORT:-6185}/" 2>/dev/null \
  && ok "AstrBot WebUI 有响应" || bad "AstrBot WebUI 不通"
port_open "$WS_PORT" && ok "反向 WS ${WS_PORT} 在监听" || bad "反向 WS ${WS_PORT} 没在监听"

echo "== 2. 插件（12 个 + 源码完整）=="
for p in android_hands android_observer coo_memory execrelay feiyu_voice hb libido memprobe persona_manage proactive probe qq_voice_call; do
  d="$DATA/plugins/astrbot_plugin_$p"
  if [[ -f "$d/main.py" && -f "$d/metadata.yaml" ]]; then ok "插件 $p"; else bad "插件 $p 缺失或不全"; fi
done

echo "== 3. 插件配置（6 个）=="
for c in android_hands coo_memory feiyu_voice libido proactive qq_voice_call; do
  f="$DATA/config/astrbot_plugin_${c}_config.json"
  [[ -f "$f" ]] && ok "配置 $c" || bad "配置 $c 缺失"
done

echo "== 4. 主配置 =="
"$PY" - "$DATA/cmd_config.json" "$ALLOW_CONTEXT" <<'PY'
import json, os, sys
path = sys.argv[1]
if not os.path.exists(path):
    print("  FAIL  cmd_config.json 不存在"); raise SystemExit
c = json.load(open(path, encoding="utf-8-sig"))
keys = (c.get("provider_sources") or [{}])[0].get("key") or []
print("  " + ("PASS" if keys and not str(keys[0]).startswith("sk-REPLACE") else "FAIL") + "  DeepSeek Key 已配置")
pf = (c.get("platform") or [{}])[0]
print("  " + ("PASS" if pf.get("enable") and int(pf.get("ws_reverse_port") or 0) == 6199 else "FAIL")
      + f"  OneBot 平台 port={pf.get('ws_reverse_port')}")
pid = ((c.get("agent_runner") or {}).get("config") or {}).get("persona", {}).get("persona_id")
print("  " + ("PASS" if pid else "FAIL") + f"  persona_id={pid}")
PY

echo "== 5. 人设与上下文 =="
"$PY" - "$DATA/data_v4.db" "$ALLOW_CONTEXT" <<'PY'
import sqlite3, sys
db, allow = sys.argv[1], sys.argv[2] == "1"
con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
rows = dict(con.execute("select persona_id, length(system_prompt) from personas"))
n_def = rows.get("default", 0)
print("  " + ("PASS" if n_def > 1500 else "FAIL") + f"  default 人设长度 {n_def}（要求 >1500）")
conv = con.execute("select count(*) from conversations").fetchone()[0]
if allow:
    print(f"  SKIP  conversations={conv}（--allow-context，不判）")
else:
    print("  " + ("PASS" if conv == 0 else "FAIL") + f"  conversations={conv}（干净重装要求 0）")
PY

echo "== 6. NapCat =="
OB=$(ls "$NAPCAT_HOME"/config/onebot11_*.json 2>/dev/null | head -1)
if [[ -n "$OB" ]]; then
  ok "onebot11 配置：$(basename "$OB")"
  "$PY" - "$OB" <<'PY'
import json, sys
c = json.load(open(sys.argv[1], encoding="utf-8-sig"))
cl = (c.get("network") or {}).get("websocketClients") or []
url = cl[0].get("url") if cl else ""
print("  " + ("PASS" if url.endswith(":6199/ws") else "FAIL") + f"  websocketClient -> {url or '无'}")
PY
else
  bad "没有 onebot11_*.json"
fi

echo
echo "PASS=$PASS FAIL=$FAIL"
[[ "$FAIL" -eq 0 ]] && { echo "全部通过：这套栈是干净重装且完整的状态"; exit 0; }
echo "有 FAIL，按上面逐条修"; exit 1
