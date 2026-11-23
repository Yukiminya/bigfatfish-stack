#!/usr/bin/env python3
"""把仓库里的 cmd_config.json.example 合并进手机上正在用的 cmd_config.json。

只改「跟部署有关」的字段，不整份覆盖 —— 整份覆盖会把你后台里点过的其它设置抹掉。

用法：apply-config.py <repo_root> <cmd_config.json 路径>
环境变量：DEEPSEEK_API_KEY / ASTRBOT_WS_PORT / ASTRBOT_WS_TOKEN / QQ_OWNER /
         ASTRBOT_WEB_USER / ASTRBOT_WEB_PASSWORD
"""
import json
import os
import shutil
import sys
import time

REPO, TARGET = sys.argv[1], sys.argv[2]
TEMPLATE = os.path.join(REPO, "astrbot", "cmd_config.json.example")


def env(name: str, default: str = "") -> str:
    return os.environ.get(name, default)


def load(path: str) -> dict:
    with open(path, encoding="utf-8-sig") as f:
        return json.load(f)


def main() -> int:
    tpl = load(TEMPLATE)
    cfg = load(TARGET) if os.path.exists(TARGET) else {}

    # 1) 模型：DeepSeek，key 从环境变量来
    key = env("DEEPSEEK_API_KEY")
    sources = tpl.get("provider_sources") or []
    if sources:
        src = dict(sources[0])
        src["key"] = [key] if key and not key.startswith("sk-REPLACE") else []
        cfg["provider_sources"] = [src]
    cfg["provider"] = tpl.get("provider", cfg.get("provider", []))

    # 2) 平台：NapCat 反向 WS
    cfg["platform"] = [
        {
            "id": "napcat",
            "type": "aiocqhttp",
            "enable": True,
            "ws_reverse_host": "127.0.0.1",
            "ws_reverse_port": int(env("ASTRBOT_WS_PORT", "6199")),
            "ws_reverse_token": env("ASTRBOT_WS_TOKEN"),
        }
    ]

    # 3) 行为设置：整组从模板拿（分段回复、限速、白名单开关……）
    for k in ("platform_settings", "provider_settings", "provider_ltm_settings",
              "content_safety", "provider_stt_settings", "provider_tts_settings"):
        if k in tpl:
            cfg[k] = tpl[k]

    # 4) 用哪个模型 / 哪个人设
    runner = cfg.setdefault("agent_runner", {})
    rcfg = runner.setdefault("config", {})
    rcfg["model"] = {
        "provider_id": "deepseek",
        "fallback_provider_ids": [],
        "request_max_retries": 5,
    }
    rcfg.setdefault("persona", {})
    rcfg["persona"]["persona_id"] = "default"

    # 5) 管理员 / 唤醒前缀 / 时区 / 日志
    owner = env("QQ_OWNER")
    if owner:
        cfg["admins_id"] = [owner]
    cfg["wake_prefix"] = tpl.get("wake_prefix", ["/"])
    cfg["timezone"] = tpl.get("timezone", "Asia/Shanghai")
    cfg["log_level"] = tpl.get("log_level", "INFO")
    cfg["plugin_set"] = tpl.get("plugin_set", ["*"])

    # 6) WebUI：不动已有账号密码，只在缺失时写用户名
    dash = cfg.setdefault("dashboard", {})
    dash.setdefault("enable", True)
    dash.setdefault("host", "127.0.0.1")
    dash.setdefault("port", int(env("ASTRBOT_WEB_PORT", "6185")))
    if not dash.get("username"):
        dash["username"] = env("ASTRBOT_WEB_USER", "astrbot")
    # 密码留空 = 首次登录时按 AstrBot 自己的流程设置；不要在这里写死

    # 7) 备份后写回
    if os.path.exists(TARGET):
        bak = f"{TARGET}.bak-{time.strftime('%Y%m%d-%H%M%S')}"
        shutil.copy2(TARGET, bak)
        print(f"    备份 -> {bak}")
    os.makedirs(os.path.dirname(os.path.abspath(TARGET)), exist_ok=True)
    with open(TARGET, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
        f.write("\n")
    print(f"    -> {TARGET}")
    if not key or key.startswith("sk-REPLACE"):
        print("    [注意] DEEPSEEK_API_KEY 没填，模型会不可用；填好 .env 再跑一次")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
