import os
import re
import sqlite3
import sys

sys.stdout.reconfigure(encoding="utf-8")

msg_db = sqlite3.connect("export/wxwork_decrypted/message.db")
file_db = sqlite3.connect("export/wxwork_decrypted/file.db")

row = msg_db.execute(
    "select message_id, content_type, content, local_extra_content from message_table "
    "where content_type in (4,14,15) and local_extra_content is not null limit 1"
).fetchone()
print("message", row[0], "type", row[1])
print("local_extra", bytes(row[3])[:200])

frow = file_db.execute(
    "select name, size, md5, message_id from file_table4 where message_id=?",
    (row[0],),
).fetchone()
print("file_table", frow)

base = r"C:\Software\企业微信\聊天记录\WXWork\1688855370846072"
name = "企业微信截图_1787300001766.png"
found = []
for root, _, files in os.walk(os.path.join(base, "Cache")):
    for f in files:
        if name in f or f.endswith(name.split("_")[-1]):
            found.append(os.path.join(root, f))
print("cache matches", found[:5])

# match by filename from file_table
fname = frow[0] if frow else name
for root, _, files in os.walk(os.path.join(base, "Cache")):
    for f in files:
        if f == fname or fname in f:
            print("by name", os.path.join(root, f))

msg_db.close()
file_db.close()
