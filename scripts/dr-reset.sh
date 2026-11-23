#!/usr/bin/env bash
# 灾难恢复演练第二步：删掉这套栈里「可重建」的部分，制造一个接近裸机的状态。
#
#   bash scripts/dr-reset.sh              # 删除
#   bash scripts/dr-reset.sh --dry-run    # 只打印会删什么
#
# 安全设计（fail-closed）：
#   * /android-share/backup 下必须至少有一个含 astrbot-data.tgz 的备份目录，否则直接退出，--force 也不绕过
#   * 默认保留 astrbot_plugin_execrelay（否则删完就没有远程执行通道，得靠人在手机上点）
#     要连它一起删：KEEP_RELAY=0 bash scripts/dr-reset.sh
#   * /android-share/qq-turns.jsonl 不删，改名 .pre-dr 保留
#
# 不删的：logs/、attachments/、knowledge_base/、temp/、webchat/、workspaces/、site-packages/
set -uo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ASTRBOT_HOME="${ASTRBOT_HOME:-/persistent/astrbot}"
NAPCAT_HOME="${NAPCAT_HOME:-/persistent/napcat}"
SHARED="${ANDROID_SHARED_DIR:-/android-share}"
DATA="$ASTRBOT_HOME/data"
KEEP_RELAY="${KEEP_RELAY:-1}"
DRY_RUN=0
[[ "${1:-}" == "--dry-run" ]] && DRY_RUN=1

run() { if [[ $DRY_RUN -eq 1 ]]; then printf '  DRY: %s\n' "$*"; else "$@"; fi }

echo "== 0. 备份检查（fail-closed）=="
BK=""
for d in "$SHARED"/backup/*/; do
  [[ -f "$d/astrbot-data.tgz" ]] || continue
  BK="$d"
done
if [[ -z "$BK" ]]; then
  echo "  没有找到任何含 astrbot-data.tgz 的备份目录，拒绝删除。" >&2
  echo "  先跑：bash scripts/dr-backup.sh" >&2
  exit 1
fi
echo "  最新备份：$BK"
echo "  大小：$(du -sh "$BK" 2>/dev/null | cut -f1)"

echo "== 1. 删除前基线 =="
echo "  plugins   = $(ls "$DATA/plugins" 2>/dev/null | wc -l)"
echo "  configs   = $(ls "$DATA"/config/*.json 2>/dev/null | wc -l)"
echo "  db        = $(ls -l "$DATA/data_v4.db" 2>/dev/null | awk '{print $5" bytes"}' || echo missing)"
echo "  napcat    = $(ls "$NAPCAT_HOME"/config/*.json 2>/dev/null | wc -l)"

echo "== 2. 删除插件 =="
for d in "$DATA"/plugins/*/; do
  [[ -d "$d" ]] || continue
  n="$(basename "$d")"
  if [[ "$KEEP_RELAY" == "1" && "$n" == "astrbot_plugin_execrelay" ]]; then
    echo "  keep $n（远程通道）"
    continue
  fi
  run rm -rf "$d"
  echo "  rm $n"
done

echo "== 3. 删除插件配置 =="
for f in "$DATA"/config/*.json; do
  [[ -e "$f" ]] || continue
  run rm -f "$f"
  echo "  rm $(basename "$f")"
done

echo "== 4. 删除主配置与数据库 =="
for f in cmd_config.json data_v4.db data_v4.db-wal data_v4.db-shm plugins.json skills.json; do
  if [[ -e "$DATA/$f" ]]; then
    run rm -f "$DATA/$f"
    echo "  rm $f"
  fi
done

echo "== 5. 删除插件状态目录 =="
if [[ -d "$DATA/plugin_data" ]]; then
  if [[ $DRY_RUN -eq 1 ]]; then
    echo "  DRY: 清空 $DATA/plugin_data/*"
  else
    find "$DATA/plugin_data" -mindepth 1 -maxdepth 1 -exec rm -rf {} + 2>/dev/null
    echo "  清空 plugin_data/*"
  fi
fi

echo "== 6. 删除 NapCat 配置 =="
for f in "$NAPCAT_HOME"/config/*.json; do
  [[ -e "$f" ]] || continue
  run rm -f "$f"
  echo "  rm $(basename "$f")"
done
echo "  （保留 $NAPCAT_HOME/config/plugins/ 里的运行时数据）"

echo "== 7. 隔离旧对话流水 =="
if [[ -f "$SHARED/qq-turns.jsonl" ]]; then
  run mv "$SHARED/qq-turns.jsonl" "$SHARED/qq-turns.jsonl.pre-dr"
  echo "  qq-turns.jsonl -> qq-turns.jsonl.pre-dr（旧上下文不参与新会话）"
else
  echo "  没有 qq-turns.jsonl，跳过"
fi

echo "== 8. 删除后 =="
echo "  plugins   = $(ls "$DATA/plugins" 2>/dev/null | wc -l)"
echo "  configs   = $(ls "$DATA"/config/*.json 2>/dev/null | wc -l)"
echo "  db        = $(ls "$DATA/data_v4.db" 2>/dev/null || echo missing)"
echo "  napcat    = $(ls "$NAPCAT_HOME"/config/*.json 2>/dev/null | wc -l)"

cat <<'EOF'

下一步（按顺序）：
  1. 重启 AstrBot 让它重建空的 data 目录与 data_v4.db：
       kill -TERM $(pid)          # pid 在 /manager/astrbot-instance.json 的 identity.pid
     App 可能停在「AstrBot 异常退出」页，需要在 App 里点一次「启动服务」。
  2. 等 6185 重新监听后，跑：bash scripts/deploy-phone.sh
  3. 再重启一次让插件加载，然后：bash scripts/dr-verify.sh
EOF
echo DONE
