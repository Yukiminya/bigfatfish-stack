#!/usr/bin/env bash
# 灾难恢复演练第一步：把手机上这套东西整体备份到 /android-share/backup/<时间戳>/
#
#   bash scripts/dr-backup.sh              # 直接备份
#   bash scripts/dr-backup.sh --dry-run    # 只看会做什么
#
# 产物（都在 /android-share/backup/<TS>/ 下）：
#   astrbot-data.tgz   /persistent/astrbot/data 整份（含 data_v4.db、插件、配置、人设）
#   napcat-config.tgz  /persistent/napcat/config 整份
#   qq-turns.jsonl     和电脑上的桌宠 Coo 共用的对话流水
#   proactive-log.jsonl / coo_memory_debug.log / persona*.txt / memprobe.json（存在才拷）
#   SHA256SUMS         上面所有 tgz 的校验
set -uo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ASTRBOT_HOME="${ASTRBOT_HOME:-/persistent/astrbot}"
NAPCAT_HOME="${NAPCAT_HOME:-/persistent/napcat}"
SHARED="${ANDROID_SHARED_DIR:-/android-share}"
TS="${TS:-$(date +%Y%m%d-%H%M%S)}"
DIR="$SHARED/backup/$TS"
DRY_RUN=0
[[ "${1:-}" == "--dry-run" ]] && DRY_RUN=1

run() { if [[ $DRY_RUN -eq 1 ]]; then printf '  DRY: %s\n' "$*"; else "$@"; fi; }

echo "== 备份目标 =="
echo "  时间戳   $TS"
echo "  落盘目录 $DIR"
[[ $DRY_RUN -eq 0 ]] && mkdir -p "$DIR"

echo "== 原始体积 =="
du -sh "$ASTRBOT_HOME/data" 2>/dev/null || echo "  （$ASTRBOT_HOME/data 不存在）"

echo "== 打包 AstrBot 数据 =="
if [[ -d "$ASTRBOT_HOME/data" ]]; then
  run tar czf "$DIR/astrbot-data.tgz" -C "$ASTRBOT_HOME" data
  echo "  astrbot-data.tgz"
else
  echo "  SKIP：没有 $ASTRBOT_HOME/data"
fi

echo "== 打包 NapCat 配置 =="
if [[ -d "$NAPCAT_HOME/config" ]]; then
  run tar czf "$DIR/napcat-config.tgz" -C "$NAPCAT_HOME" config
  echo "  napcat-config.tgz"
else
  echo "  SKIP：没有 $NAPCAT_HOME/config"
fi

echo "== 拷上下文文件 =="
for f in qq-turns.jsonl coo_memory_debug.log proactive-log.jsonl persona.txt persona_db_default.txt persona_db_dafeiyu.txt memprobe.json; do
  if [[ -f "$SHARED/$f" ]]; then
    run cp -a "$SHARED/$f" "$DIR/"
    echo "  $f"
  fi
done

echo "== 生成校验 =="
if [[ $DRY_RUN -eq 0 ]]; then
  ( cd "$DIR" && sha256sum ./*.tgz > SHA256SUMS 2>/dev/null )
  cat "$DIR/SHA256SUMS" 2>/dev/null
  echo "== 清单 =="
  ls -l "$DIR"
  du -sh "$DIR"
fi

echo "DONE backup -> $DIR"
echo "下一步：adb pull '$DIR' <电脑目录>，然后在电脑上核对 SHA256SUMS"
