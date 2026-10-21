import re
import sqlite3
import sys

sys.stdout.reconfigure(encoding="utf-8")
conn = sqlite3.connect(r"export/wxwork_decrypted/message.db")
row = conn.execute(
    "select content, extra_content, local_extra_content from message_table where content_type=14 limit 1"
).fetchone()
for name, raw in zip(("content", "extra", "local"), row):
    if not raw:
        continue
    b = bytes(raw)
    print(name, "len", len(b))
    for m in re.finditer(rb"[\x20-\x7e]{4,}", b):
        s = m.group().decode("ascii", errors="ignore")
        if len(s) >= 4:
            print(" ascii", s[:120])
    for m in re.finditer(rb"<[^>]+>", b):
        print(" xml", m.group()[:200])
