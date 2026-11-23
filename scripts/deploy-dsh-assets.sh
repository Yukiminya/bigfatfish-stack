#!/usr/bin/env bash
# 把 DSH / DSHA 那套资产（profile 配置、skills、自写插件）铺到目标机器上。
# 只做文件层面的安置 + 备份，不猜 DSH 的安装命令；跑完按最后打印的下一步走。
#
#   bash scripts/deploy-dsh-assets.sh --profile-dir ~/.dsh/profiles/web
#   bash scripts/deploy-dsh-assets.sh --dry-run
#
# 默认 profile 目录：~/.dsh/profiles/desktop（电脑）。手机上 DSHA 的 profile 一般叫 web。
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DSH_HOME="${DSH_HOME:-$HOME/.dsh}"
PROFILE_DIR="$DSH_HOME/profiles/desktop"
SKILLS_DIR="$DSH_HOME/skills"
DRY_RUN=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --profile-dir) PROFILE_DIR="$2"; shift 2 ;;
    --skills-dir)  SKILLS_DIR="$2";  shift 2 ;;
    --dry-run)     DRY_RUN=1; shift ;;
    *) echo "未知参数 $1"; exit 2 ;;
  esac
done

log() { printf '[dsh] %s\n' "$*"; }
run() { if [[ $DRY_RUN -eq 1 ]]; then printf '  DRY: %s\n' "$*"; else "$@"; fi; }

STAMP="$(date +%Y%m%d-%H%M%S)"

# ---------- 1. profile 配置 ----------
log "1/3 profile 配置 -> $PROFILE_DIR"
if [[ -f "$PROFILE_DIR/package.json" ]]; then
  run cp "$PROFILE_DIR/package.json" "$PROFILE_DIR/package.json.bak-$STAMP"
  log "    备份 package.json -> package.json.bak-$STAMP"
fi
run mkdir -p "$PROFILE_DIR"
for f in package.json cordis.patch.yml cordis.yml pnpm-workspace.yaml; do
  run cp "$REPO_ROOT/dsha/dsh-profile/$f" "$PROFILE_DIR/$f"
  log "    写入 $f"
done
log "    注意：cordis.patch.yml 里有绝对路径（gh / git / node），换机器要按新机器改"

# ---------- 2. skills ----------
log "2/3 skills -> $SKILLS_DIR"
run mkdir -p "$SKILLS_DIR"
for d in "$REPO_ROOT"/dsha/skills/*/; do
  name="$(basename "$d")"
  if [[ -d "$SKILLS_DIR/$name" && $DRY_RUN -eq 0 ]]; then
    run mv "$SKILLS_DIR/$name" "$SKILLS_DIR/$name.bak-$STAMP"
    log "    已有同名技能，旧目录改名 .bak-$STAMP"
  fi
  run cp -a "$d" "$SKILLS_DIR/$name"
  log "    $name"
done

# ---------- 3. 自写插件 ----------
log "3/3 自写插件"
log "    仓库里带了两份源码："
for d in "$REPO_ROOT"/dsha/plugins/*/; do
  log "      $(basename "$d")  ->  $d"
done
log "    它们不是直接丢进 profile 就能用，要按 profile 的依赖方式装："
log "      dsh-persona-manage : package.json 里写 \"dsh-persona-manage\": \"github:null119/dsh-persona-manage\""
log "      voice-wake         : package.json 里写 \"@local/voice-wake\": \"link:<本机路径>\",并加进 dsh.profile.bundles"
log "    装完把这两行放进 $PROFILE_DIR/package.json 的 dependencies / dsh.profile.bundles，再启动 DSH。"

cat <<EOF

下一步（按顺序）：
  1. 打开 $PROFILE_DIR/package.json，确认 dsh.profile.bundles 里有你需要的插件；
  2. 启动 DSH（DSHA 里就是启动那个 App），它自己会把 profile 依赖装上；
  3. 装完看 WebUI 的插件页，persona-manage / voice-wake 应该都在；
  4. 人设：persona-manage 管理页粘贴 astrbot/persona/desktop-qq-persona.txt（或 default.txt）。
EOF
