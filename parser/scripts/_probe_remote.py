import paramiko
import sys

sys.stdout.reconfigure(encoding="utf-8")
ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
ssh.connect(
    "192.168.2.14",
    username="mache",
    password="machefu666",
    timeout=20,
    allow_agent=False,
    look_for_keys=False,
)
cmds = [
    "dir C:\\wxwork_pull\\decrypted",
    "type C:\\wxwork_pull\\tools\\config.json",
]
for c in cmds:
    _, o, e = ssh.exec_command(c, timeout=60)
    o.channel.recv_exit_status()
    print(">", c)
    print((o.read() + e.read()).decode("utf-8", errors="replace"))
    print()
ssh.close()
