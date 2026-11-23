#!/usr/bin/env python3
"""把人设写进 AstrBot 的 personas 表（data_v4.db）。

不是覆盖式迁移：按 persona_id 做 UPDATE，没有就 INSERT。
写之前把整张 personas 表导出一份到旁边，写坏了能捡回来。

用法：apply-persona.py <repo_root> <data_v4.db 路径>
环境变量：PERSONA_IDS  要写哪几个人设，默认 "default,dafeiyu"
"""
import json
import os
import sqlite3
import sys
import time

REPO = sys.argv[1]
DB = sys.argv[2]
PERSONA_DIR = os.path.join(REPO, "astrbot", "persona")
IDS = [s.strip() for s in os.environ.get("PERSONA_IDS", "default,dafeiyu").split(",") if s.strip()]
NOW = time.strftime("%Y-%m-%d %H:%M:%S") + ".%06d" % (int(time.time() * 1e6) % 1000000)


def main() -> int:
    if not os.path.exists(DB):
        raise SystemExit(f"没有 {DB}：先把 AstrBot 启动一次，让它在 data 目录建库")

    files = {}
    for pid in IDS:
        p = os.path.join(PERSONA_DIR, f"{pid}.txt")
        if not os.path.exists(p):
            raise SystemExit(f"找不到人设文件 {p}")
        with open(p, encoding="utf-8") as f:
            files[pid] = f.read().rstrip() + "\n"

    con = sqlite3.connect(DB)
    try:
        # 备份
        dump = os.path.join(os.path.dirname(DB), f"personas-backup-{time.strftime('%Y%m%d-%H%M%S')}.json")
        rows = con.execute(
            "select created_at, updated_at, id, persona_id, system_prompt, begin_dialogs, tools, skills,"
            " custom_error_message, folder_id, sort_order from personas"
        ).fetchall()
        cols = ["created_at", "updated_at", "id", "persona_id", "system_prompt", "begin_dialogs",
                "tools", "skills", "custom_error_message", "folder_id", "sort_order"]
        with open(dump, "w", encoding="utf-8") as f:
            json.dump([dict(zip(cols, r)) for r in rows], f, ensure_ascii=False, indent=2)
        print(f"    备份 -> {dump}")

        for pid, prompt in files.items():
            cur = con.execute("select id from personas where persona_id = ?", (pid,)).fetchone()
            if cur:
                con.execute(
                    "update personas set system_prompt = ?, updated_at = ? where persona_id = ?",
                    (prompt, NOW, pid),
                )
                print(f"    更新人设 {pid}（{len(prompt)} 字）")
            else:
                con.execute(
                    "insert into personas (created_at, updated_at, persona_id, system_prompt,"
                    " begin_dialogs, tools, skills, sort_order) values (?, ?, ?, ?, '[]', null, null, 0)",
                    (NOW, NOW, pid, prompt),
                )
                print(f"    新增人设 {pid}（{len(prompt)} 字）")
        con.commit()
    finally:
        con.close()

    print("完成。人设要生效：确认 agent_runner.config.persona.persona_id 指向它，然后重启 AstrBot")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
