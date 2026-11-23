#!/usr/bin/env bash
# 在手机（AstrBot 的 Ubuntu proot 里，root 身份）部署「大肥鱼」全套。
#
# 幂等：重复跑不会破坏已有插件目录；默认不覆盖已存在的配置，只按 .env 合并关键字段。
#
# 用法：
#   cd /path/to/bigfatfish-stack
#   cp .env.example .env && vi .env           # 填 DEEPSEEK_API_KEY 等
#   bash scripts/deploy-phone.sh              # root 身份
#   bash scripts/deploy-phone.sh --dry-run    # 只看会做什么
#   FORCE=1 bash scripts/deploy-phone.sh      # 用仓库里的配置覆盖手机上已有的
#
# 假设（与 AstrBot for Android 一致）：
#   ASTRBOT_HOME=/persistent/astrbot
#   NAPCAT_HOME=/persistent/napcat
#   共享目录 /android-share -> /sdcard/Android/data/io.astrbot.android/files/shared
set -euo pipefail

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
FORCE="${FORCE:-0}"
DRY_RUN=0
[[ "${1:-}" == "--dry-run" ]] && DRY_RUN=1

log()  { printf '[deploy] %s\n' "$*"; }
warn() { printf '[deploy] %s\n' "$*"; }
die()  { printf '[deploy][ERROR] %s\n' "$*" >&2; exit 1; }
run()  # 打印并执行；dry-run 时只打印
{
  if [[ $DRY_RUN -eq 1 ]]; then printf '  DRY: %s\n' "$*"; else "$@"; fi
}

# ---------- 0. 环境 ----------
if [[ -f "$REPO_ROOT/.env" ]]; then
  set -a; . "$REPO_ROOT/.env"; set +a
else
  if [[ -f "$REPO_ROOT/.env.example" ]]; then
    cp "$REPO_ROOT/.env.example" "$REPO_ROOT/.env"
    warn "没有 .env，已按 .env.example 生成一份，记得改成你自己的值"
    set -a; . "$REPO_ROOT/.env"; set +a
  else
    die "找不到 .env / .env.example"
  fi
fi

[[ -d "$DATA_DIR" ]] || die "没有 $DATA_DIR：先在 App 里把 AstrBot 启动一次再跑本脚本"
[[ -n "$PY" && -x "$PY" ]] || die "找不到 AstrBot 的 python：设 ASTRBOT_PY 指向它（App 里一般是 /opt/astrbot-venv/bin/python）"

log "REPO_ROOT=$REPO_ROOT"
log "ASTRBOT_HOME=$ASTRBOT_HOME  NAPCAT_HOME=$NAPCAT_HOME  FORCE=$FORCE  DRY_RUN=$DRY_RUN"

# ---------- 1. 插件 ----------
log "1/6 安装 AstrBot 插件 -> $DATA_DIR/plugins"
mkdir -p "$DATA_DIR/plugins"
for pdir in "$REPO_ROOT"/astrbot/plugins/*/; do
  name="$(basename "$pdir")"
  target="$DATA_DIR/plugins/$name"
  if [[ -d "$target" ]]; then
    if [[ "$FORCE" == "1" ]]; then
      run rm -rf "$target"
      run cp -a "$pdir" "$target"
      log "    覆盖 $name"
    else
      log "    已存在，跳过 $name（FORCE=1 可覆盖）"
    fi
  else
    run cp -a "$pdir" "$target"
    log "    新增 $name"
  fi
done
run find "$DATA_DIR/plugins" -name '__pycache__' -type d -prune -exec rm -rf {} +

# ---------- 2. 插件配置 ----------
log "2/6 安装插件配置 -> $DATA_DIR/config"
run mkdir -p "$DATA_DIR/config"
for cfg in "$REPO_ROOT"/astrbot/plugin-configs/*.json; do
  base="$(basename "$cfg")"
  target="$DATA_DIR/config/$base"
  if [[ -f "$target" && "$FORCE" != "1" ]]; then
    log "    已存在，保留原配置 $base"
    continue
  fi
  run "$PY" "$REPO_ROOT/scripts/render_template.py" "$cfg" "$target"
done

# ---------- 3. 主配置 ----------
log "3/6 合并主配置 cmd_config.json"
run "$PY" "$REPO_ROOT/scripts/apply-config.py" "$REPO_ROOT" "$DATA_DIR/cmd_config.json"

# ---------- 4. 人设 ----------
log "4/6 写入人设 default / dafeiyu"
run "$PY" "$REPO_ROOT/scripts/apply-persona.py" "$REPO_ROOT" "$DATA_DIR/data_v4.db"

# ---------- 5. skills 清单 ----------
log "5/6 写入 AstrBot skills 清单"
run cp "$REPO_ROOT/astrbot/skills.json" "$DATA_DIR/skills.json"

# ---------- 6. NapCat ----------
log "6/6 安装 NapCat 配置 -> $NAPCAT_HOME/config"
run mkdir -p "$NAPCAT_HOME/config"
for f in "$REPO_ROOT"/napcat/config/phone/*.example; do
  base="$(basename "$f" .example)"
  # 文件名里的号段按 QQ_SELF 改名
  if [[ -n "${QQ_SELF:-}" && "$base" == *3325646322* ]]; then
    base="${base//3325646322/$QQ_SELF}"
  fi
  target="$NAPCAT_HOME/config/$base"
  if [[ -f "$target" && "$FORCE" != "1" ]]; then
    log "    已存在，保留原配置 $base"
    continue
  fi
  run "$PY" "$REPO_ROOT/scripts/render_template.py" "$f" "$target"
done

# 账号号段统一换成 QQ_SELF（只在 FORCE 或新写入时发生）
if [[ -n "${QQ_SELF:-}" && "${QQ_SELF}" != "3325646322" ]]; then
  log "    把配置里的 3325646322 替换成 $QQ_SELF"
  run grep -rl '3325646322' "$NAPCAT_HOME/config" \
      --include='*.json' \
      | while read -r f; do
          run sed -i "s/3325646322/$QQ_SELF/g" "$f"
        done || true
fi

log "完成。重启 AstrBot 与 NapCat，然后跑 scripts/verify-phone.sh 验收。"
