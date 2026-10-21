import sqlite3
import sys

sys.stdout.reconfigure(encoding="utf-8")

conn = sqlite3.connect("export/wxwork_decrypted/message.db")
rows = conn.execute(
    "select message_id, content_type, content, extra_content, local_extra_content "
    "from message_table where conversation_id=? and content_type=2 limit 20",
    ("R:10696241516362395",),
).fetchall()

import importlib.util
import os

path = os.path.join("core-wecom", "message_decode.py")
spec = importlib.util.spec_from_file_location("d", path)
d = importlib.util.module_from_spec(spec)
spec.loader.exec_module(d)

for mid, ctype, content, extra, local in rows:
    text = d.display_message_content(ctype, content, extra, local)
    if d._is_garbage_text(text) or "레" in text[:4]:
        print("===", mid, "ctype", ctype)
        print("decoded:", repr(text[:100]))
        print("hex:", bytes(content)[:40].hex() if content else None)
        texts = d._parse_protobuf_strings(bytes(content) if content else b"")
        print("pb strings:", texts[:5])
        conn.close()
        break
