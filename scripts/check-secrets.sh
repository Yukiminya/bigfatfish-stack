#!/usr/bin/env bash
# 提交前扫一遍：仓库里不该有真密钥。
#   bash scripts/check-secrets.sh          # 有命中就退出码 1
# 只匹配「看起来是真的」的模式，占位符（${VAR} / CHANGE_ME / REPLACE）不算。
set -uo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."

HITS=0
say() { printf '%s\n' "$*"; }

# 1. DeepSeek / OpenAI 形态的 key：sk- + 32 位以上十六进制
say "== 1. sk- 形态的 API Key =="
if grep -rInE 'sk-[A-Za-z0-9]{24,}' --include='*' \
     --exclude-dir=.git --exclude='.env' . 2>/dev/null \
   | grep -vE 'sk-REPLACE|\$\{DEEPSEEK'; then
  say "  ^^ 有命中"; HITS=$((HITS+1))
else
  say "  none"
fi

# 2. 32/48/64 位纯十六进制（token / secret 常见形态），出现在 *token* / *secret* / *key* 字段里
say "== 2. token/secret 字段里的长十六进制串 =="
if grep -rInE '"[A-Za-z_]*(token|secret|key)[A-Za-z_]*"[[:space:]]*:[[:space:]]*"[0-9a-f]{32,}"' \
     --include='*.json' --include='*.example' --include='*.yml' --include='*.yaml' \
     --exclude-dir=.git . 2>/dev/null; then
  say "  ^^ 有命中"; HITS=$((HITS+1))
else
  say "  none"
fi

# 3. 密码哈希 / JWT secret
say "== 3. pbkdf2 哈希与 jwt_secret =="
if grep -rInE 'pbkdf2_sha256\$|"jwt_secret"[[:space:]]*:[[:space:]]*"[0-9a-f]{16,}"' \
     --exclude-dir=.git --exclude='*.md' . 2>/dev/null; then
  say "  ^^ 有命中"; HITS=$((HITS+1))
else
  say "  none"
fi

# 4. 已知的历史泄露值（本项目里出现过的那几个，防止手滑贴回来）
say "== 4. 已知的历史值 =="
if grep -rInE '845402bec2f54d7c|199dfa281eb78d49|c9f1c207d2126581|29a33fc992aac87f|coovoice2026|72b97f42ccd810d7' \
     --exclude-dir=.git . 2>/dev/null; then
  say "  ^^ 有命中"; HITS=$((HITS+1))
else
  say "  none"
fi

say ""
if [[ $HITS -eq 0 ]]; then
  say "干净：没有发现密钥残留"
  exit 0
fi
say "发现 $HITS 类命中，先处理再提交"
exit 1
