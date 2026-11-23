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

# 4. 自己的东西：把「绝对不该出现」的字面量写进一个本地文件，脚本会帮你查
#    .secrets-baseline.local（已在 .gitignore 里）每行一个字符串，不参与提交
say "== 4. 本地基线（.secrets-baseline.local）=="
if [[ -f .secrets-baseline.local ]]; then
  hit=0
  while IFS= read -r pat; do
    [[ -z "$pat" || "$pat" == \#* ]] && continue
    if grep -rInF --exclude-dir=.git --exclude=.secrets-baseline.local -- "$pat" . 2>/dev/null; then
      hit=1
    fi
  done < .secrets-baseline.local
  if [[ $hit -eq 1 ]]; then say "  ^^ 有命中"; HITS=$((HITS+1)); else say "  none"; fi
else
  say "  none（没有基线文件，跳过）"
fi

say ""
if [[ $HITS -eq 0 ]]; then
  say "干净：没有发现密钥残留"
  exit 0
fi
say "发现 $HITS 类命中，先处理再提交"
exit 1
