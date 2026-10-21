# -*- coding: utf-8 -*-
"""整机迁移：旧机打包、新机还原、放行 8767、自检。

用法:
  python scripts/migrate.py pack [--out DIR]
  python scripts/migrate.py restore --from DIR [--dest DIR] [--check]
  python scripts/migrate.py firewall
  python scripts/migrate.py smoke [--base URL] [--pack DIR]
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LISTEN_PORTS = (3777, 3778, 8767)
SKIP_DIR_NAMES = (
    ".venv",
    "venv",
    "node_modules",
    "__pycache__",
    ".mypy_cache",
    ".pytest_cache",
    "logs",
)
SKIP_FILES = ("*.pyc", "附件.zip")
MYSQL_BIN_CANDIDATES = (
    Path(r"C:\Program Files\MySQL\MySQL Server 8.4\bin"),
    Path(r"C:\Program Files\MySQL\MySQL Server 8.0\bin"),
    Path(r"C:\Program Files\MySQL\MySQL Server 5.7\bin"),
    Path(r"C:\xampp\mysql\bin"),
    Path(r"C:\Program Files\MariaDB 11.4\bin"),
    Path(r"C:\Program Files\MariaDB 10.11\bin"),
)


def _print(msg: str) -> None:
    print(msg, flush=True)


def is_reparse(path: Path) -> bool:
    try:
        if hasattr(path, "is_junction") and path.is_junction():
            return True
    except OSError:
        pass
    try:
        return path.is_symlink()
    except OSError:
        return False


def export_source(root: Path) -> Path:
    exp = root / "parser" / "export"
    if not exp.exists():
        raise FileNotFoundError("缺少 parser/export")
    if is_reparse(exp):
        return exp.resolve()
    return exp


def find_mysql_bin(exe: str) -> Path | None:
    name = exe if exe.lower().endswith(".exe") else exe + ".exe"
    for folder in MYSQL_BIN_CANDIDATES:
        cand = folder / name
        if cand.is_file():
            return cand
    found = shutil.which(exe) or shutil.which(name)
    return Path(found) if found else None


def lan_ipv4() -> list[str]:
    found: list[str] = []

    def add(ip: str) -> None:
        ip = str(ip or "").strip()
        if not ip or ip.startswith("127.") or ip in found:
            return
        found.append(ip)

    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(0.4)
        s.connect(("8.8.8.8", 80))
        add(s.getsockname()[0])
        s.close()
    except OSError:
        pass
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            add(info[4][0])
    except OSError:
        pass
    return found


def mysql_settings() -> dict:
    sys.path.insert(0, str(ROOT))
    from app.config import load_config

    c = load_config()
    return {
        "host": str(c.get("mysqlHost") or "127.0.0.1"),
        "port": int(c.get("mysqlPort") or 3306),
        "user": str(c.get("mysqlUser") or "root"),
        "password": str(c.get("mysqlPassword") or ""),
        "database": str(c.get("mysqlDatabase") or "shenbaoshu"),
        "wecomDatabase": str(c.get("mysqlWecomDatabase") or "shenbaoshu_wecom"),
    }


def _ps_quote(s: str) -> str:
    return "'" + str(s).replace("'", "''") + "'"


def stop_project(root: Path) -> None:
    root_s = str(root)
    ps = f"""
$root = {_ps_quote(root_s)}
Get-CimInstance Win32_Process | Where-Object {{
  $_.Name -eq 'cmd.exe' -and $_.CommandLine -and
  ($_.CommandLine -like '*start.cmd*') -and ($_.CommandLine -like ('*' + $root + '*'))
}} | ForEach-Object {{
  try {{ Stop-Process -Id $_.ProcessId -Force -ErrorAction Stop }} catch {{}}
}}
$ports = @(3777, 3778, 8767)
foreach ($port in $ports) {{
  $cs = @(Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue)
  foreach ($c in $cs) {{
    try {{
      $p = Get-Process -Id $c.OwningProcess -ErrorAction Stop
      if ($p.ProcessName -match 'python|uvicorn') {{
        Stop-Process -Id $p.Id -Force
        Write-Host ('stopped pid=' + $p.Id + ' port=' + $port)
      }}
    }} catch {{}}
  }}
}}
"""
    subprocess.run(
        ["powershell", "-NoProfile", "-Command", ps],
        check=False,
        cwd=str(root),
    )
    time.sleep(1.2)


def start_project(root: Path) -> None:
    start = root / "start.cmd"
    if not start.is_file():
        raise FileNotFoundError("缺少 start.cmd")
    flags = getattr(subprocess, "CREATE_NEW_CONSOLE", 0) if os.name == "nt" else 0
    subprocess.Popen(
        ["cmd.exe", "/c", str(start)],
        cwd=str(root),
        creationflags=flags,
    )


def port_listening(port: int, host: str = "127.0.0.1") -> bool:
    s = socket.socket()
    try:
        s.settimeout(0.4)
        return s.connect_ex((host, int(port))) == 0
    except OSError:
        return False
    finally:
        s.close()


def wait_port(port: int, seconds: float = 45) -> bool:
    deadline = time.time() + seconds
    while time.time() < deadline:
        if port_listening(port):
            return True
        time.sleep(0.5)
    return False


def robocopy(src: Path, dst: Path, *, exclude_junctions: bool = True, extra_xd: list[str] | None = None) -> None:
    dst.mkdir(parents=True, exist_ok=True)
    cmd = [
        "robocopy",
        str(src),
        str(dst),
        "/E",
        "/R:2",
        "/W:2",
        "/MT:8",
        "/NFL",
        "/NDL",
        "/NP",
        "/XD",
        *SKIP_DIR_NAMES,
        "/XF",
        *SKIP_FILES,
    ]
    if extra_xd:
        cmd.extend(["/XD", *extra_xd])
    if exclude_junctions:
        cmd.append("/XJ")
    _print("robocopy " + str(src) + " -> " + str(dst))
    p = subprocess.run(cmd)
    if p.returncode >= 8:
        raise RuntimeError("robocopy 失败，退出码 " + str(p.returncode))


def dump_mysql(sql_path: Path, settings: dict | None = None) -> None:
    dump = find_mysql_bin("mysqldump")
    if dump is None:
        raise FileNotFoundError("找不到 mysqldump，请先安装 MySQL 客户端并把 bin 加入 PATH")
    cfg = settings or mysql_settings()
    sql_path.parent.mkdir(parents=True, exist_ok=True)
    fd, cnf = tempfile.mkstemp(prefix="migrate_mysql_", suffix=".cnf")
    os.close(fd)
    cnf_path = Path(cnf)
    try:
        cnf_path.write_text(
            "[client]\nuser={user}\npassword={password}\nhost={host}\nport={port}\n"
            "default-character-set=utf8mb4\n".format(
                user=cfg["user"],
                password=cfg["password"],
                host=cfg["host"],
                port=cfg["port"],
            ),
            encoding="utf-8",
        )
        cmd = [
            str(dump),
            "--defaults-extra-file=" + str(cnf_path),
            "--databases",
            cfg["database"],
            cfg["wecomDatabase"],
            "--default-character-set=utf8mb4",
            "--single-transaction",
            "--routines",
            "--events",
            "--hex-blob",
            "--result-file=" + str(sql_path),
        ]
        _print("mysqldump " + cfg["database"] + " " + cfg["wecomDatabase"])
        p = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
        if p.returncode != 0:
            err = (p.stderr or p.stdout or "").strip()[:400]
            raise RuntimeError("mysqldump 失败: " + err)
    finally:
        try:
            cnf_path.unlink()
        except OSError:
            pass
    if not sql_path.is_file() or sql_path.stat().st_size < 200:
        raise RuntimeError("SQL 导出文件为空")
    text = sql_path.read_text(encoding="utf-8", errors="ignore")[:8000]
    if "CREATE DATABASE" not in text and "CREATE TABLE" not in text:
        raise RuntimeError("SQL 导出内容异常")


def import_mysql(sql_path: Path, settings: dict) -> None:
    mysql = find_mysql_bin("mysql")
    if mysql is None:
        raise FileNotFoundError("找不到 mysql 客户端")
    if not sql_path.is_file():
        raise FileNotFoundError("缺少 " + str(sql_path))
    fd, cnf = tempfile.mkstemp(prefix="migrate_mysql_", suffix=".cnf")
    os.close(fd)
    cnf_path = Path(cnf)
    try:
        cnf_path.write_text(
            "[client]\nuser={user}\npassword={password}\nhost={host}\nport={port}\n"
            "default-character-set=utf8mb4\n".format(
                user=settings["user"],
                password=settings["password"],
                host=settings["host"],
                port=settings["port"],
            ),
            encoding="utf-8",
        )
        cmd = [
            str(mysql),
            "--defaults-extra-file=" + str(cnf_path),
            "--default-character-set=utf8mb4",
        ]
        _print("mysql < " + sql_path.name)
        with sql_path.open("rb") as fh:
            p = subprocess.run(cmd, stdin=fh, capture_output=True)
        if p.returncode != 0:
            err = (p.stderr or p.stdout or b"").decode("utf-8", "replace").strip()[:400]
            raise RuntimeError("mysql 导入失败: " + err)
    finally:
        try:
            cnf_path.unlink()
        except OSError:
            pass


def rewrite_abs_paths(cfg_path: Path, old_root: str, new_root: str) -> list[str]:
    notes: list[str] = []
    if not cfg_path.is_file():
        return ["缺少 config.json"]
    data = json.loads(cfg_path.read_text(encoding="utf-8"))
    old = str(old_root or "").rstrip("\\/")
    new = str(new_root or "").rstrip("\\/")

    def walk(obj):
        if isinstance(obj, dict):
            return {k: walk(v) for k, v in obj.items()}
        if isinstance(obj, list):
            return [walk(v) for v in obj]
        if isinstance(obj, str) and old and obj.replace("/", "\\").lower().startswith(old.replace("/", "\\").lower()):
            suffix = obj[len(old):]
            return new + suffix
        return obj

    if old and new and old.lower() != new.lower():
        data = walk(data)
        notes.append("已把配置里的旧项目路径换成新目录")
    hybrid = ((data.get("scanPdf") or {}).get("hybrid") or {})
    tess = str(hybrid.get("tesseractCmd") or "").strip()
    if tess and not Path(tess).is_file():
        hybrid["tesseractCmd"] = ""
        data.setdefault("scanPdf", {}).setdefault("hybrid", hybrid)
        notes.append("新机没有 Tesseract，已清空 tesseractCmd")
    cb = ((data.get("projectProof") or {}).get("codebuddy") or {})
    cmd = str(cb.get("cmd") or "").strip()
    if cmd and cmd not in ("codebuddy", "codebuddy.cmd") and not Path(cmd).is_file():
        if shutil.which("codebuddy.cmd") or shutil.which("codebuddy"):
            cb["cmd"] = "codebuddy.cmd"
            notes.append("codebuddy 改用 PATH 中的命令")
        else:
            cb["cmd"] = ""
            notes.append("新机没有 codebuddy，已清空命令路径")
        data.setdefault("projectProof", {}).setdefault("codebuddy", cb)
    cfg_path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return notes


def required_missing(pack: Path) -> list[str]:
    project = pack / "project"
    root = project if (project / "run.py").is_file() else pack
    need = [
        root / "run.py",
        root / "start.cmd",
        root / "config.json",
        root / "requirements.txt",
        root / "parser" / "api" / "server.py",
        root / "parser" / "config.jsonc",
        root / "data" / "agent_review.sqlite",
    ]
    missing = [str(p.relative_to(root) if p.is_relative_to(root) else p) for p in need if not p.exists()]
    sql = pack / "shenbaoshu.sql"
    if not sql.is_file():
        sql2 = root / "shenbaoshu.sql"
        if not sql2.is_file():
            missing.append("shenbaoshu.sql")
    exp = root / "parser" / "export"
    if is_reparse(exp):
        missing.append("parser/export 仍是联接，应拷成普通目录")
    synced = exp / "synced"
    if not synced.is_dir() or not any(synced.iterdir()):
        missing.append("parser/export/synced 为空")
    attach = root / "附件"
    if not attach.is_dir():
        missing.append("附件/")
    tasks = root / "tasks"
    if not tasks.is_dir():
        missing.append("tasks/")
    return missing


def write_guide(pack: Path, info: dict) -> None:
    ips = info.get("lanIps") or []
    ip = ips[0] if ips else "新机局域网IP"
    text = """整机迁移包

本目录用 U 盘或局域网拷到新电脑后，在新电脑上：

1. 安装 Python 3.10+（现网 3.12）、MySQL 5.7/8、本机 Office。可选 Tesseract、Clash。
2. 打开「项目」文件夹（project\\）。路径可以和旧机不同。
3. 导入数据库（新机 MySQL root 密码若不同，把下面的 -p 改成实际密码）：

   mysql -u root -p --default-character-set=utf8mb4 < shenbaoshu.sql

   或在项目根执行：
   python scripts\\migrate.py restore --from {pack} --dest 新目录

4. 打开 project\\config.json：
   - mysql.host 仍用 127.0.0.1，只改新机密码
   - wecomChat.intent.proxy 改成新机 Clash 混合端口（国内访问 TypeSafe 需要）
   - papers / ps / pool / portal 仍是旧局域网地址，新机不在同一网则那些功能不可用
5. 管理员 PowerShell 放行解析端口：

   powershell -ExecutionPolicy Bypass -File project\\scripts\\migrate_open_firewall.ps1

6. 各电脑企微助手的服务器地址改成 http://{ip}:8767（以新机实际 IP 为准）
7. 项目根运行 start.cmd，浏览器打开 http://127.0.0.1:3777
8. 自检：python scripts\\migrate.py smoke --base http://127.0.0.1:3777

不要只 git clone。不要把 .venv 拷过去。parser\\export 必须是普通目录（本包已展开联接）。
""".format(pack=str(pack), ip=ip)
    (pack / "MIGRATE.txt").write_text(text.replace("\n", "\r\n"), encoding="utf-8")


def default_pack_dir() -> Path:
    stamp = datetime.now().strftime("%Y%m%d-%H%M")
    return ROOT.parent / ("shenbaoshu-migrate-" + stamp)


def cmd_pack(args: argparse.Namespace) -> int:
    out = Path(args.out).resolve() if args.out else default_pack_dir()
    if out.exists() and any(out.iterdir()) and not args.force:
        _print("目标目录已存在且非空: " + str(out))
        return 2
    out.mkdir(parents=True, exist_ok=True)
    project = out / "project"
    sql_path = out / "shenbaoshu.sql"
    _print("打包到 " + str(out))
    cfg = mysql_settings()
    exp_real = export_source(ROOT)
    info = {
        "packedAt": datetime.now().isoformat(timespec="seconds"),
        "oldRoot": str(ROOT),
        "exportWasJunction": is_reparse(ROOT / "parser" / "export"),
        "exportSource": str(exp_real),
        "lanIps": lan_ipv4(),
        "mysqlHost": cfg["host"],
        "mysqlPort": cfg["port"],
        "mysqlUser": cfg["user"],
        "mysqlDatabase": cfg["database"],
        "mysqlWecomDatabase": cfg["wecomDatabase"],
        "listenPorts": list(LISTEN_PORTS),
    }
    stopped = False
    try:
        _print("停止本项目 3777/3778/8767 …")
        stop_project(ROOT)
        stopped = True
        dump_mysql(sql_path, cfg)
        robocopy(ROOT, project, exclude_junctions=True)
        dest_export = project / "parser" / "export"
        if dest_export.exists():
            shutil.rmtree(dest_export, ignore_errors=True)
        robocopy(exp_real, dest_export, exclude_junctions=False)
        (out / "migrate-info.json").write_text(
            json.dumps(info, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        write_guide(out, info)
        shutil.copy2(ROOT / "scripts" / "migrate_open_firewall.ps1", out / "migrate_open_firewall.ps1")
        missing = required_missing(out)
        if missing:
            raise RuntimeError("打包后缺项: " + "; ".join(missing))
        _print("打包完成 " + str(out))
        _print("SQL " + str(sql_path.stat().st_size) + " bytes")
        return 0
    finally:
        if stopped and not args.no_restart:
            _print("重新启动本机服务 …")
            start_project(ROOT)


def _settings_from_dest(dest: Path, password: str | None) -> dict:
    sys.path.insert(0, str(dest))
    try:
        from app.config import load_config

        c = load_config()
        cfg = {
            "host": str(c.get("mysqlHost") or "127.0.0.1"),
            "port": int(c.get("mysqlPort") or 3306),
            "user": str(c.get("mysqlUser") or "root"),
            "password": str(c.get("mysqlPassword") or ""),
            "database": str(c.get("mysqlDatabase") or "shenbaoshu"),
            "wecomDatabase": str(c.get("mysqlWecomDatabase") or "shenbaoshu_wecom"),
        }
    except Exception:
        cfg = {
            "host": "127.0.0.1",
            "port": 3306,
            "user": "root",
            "password": "",
            "database": "shenbaoshu",
            "wecomDatabase": "shenbaoshu_wecom",
        }
    if password is not None:
        cfg["password"] = password
    env_pw = os.environ.get("MYSQL_PWD")
    if env_pw and not password:
        cfg["password"] = env_pw
    return cfg


def cmd_restore(args: argparse.Namespace) -> int:
    pack = Path(args.src).resolve()
    if not pack.exists():
        _print("找不到迁移包 " + str(pack))
        return 2
    missing = required_missing(pack)
    if missing:
        _print("迁移包不完整:")
        for m in missing:
            _print("  - " + m)
        return 2
    info = {}
    info_path = pack / "migrate-info.json"
    if info_path.is_file():
        info = json.loads(info_path.read_text(encoding="utf-8"))
    src_project = pack / "project"
    if not (src_project / "run.py").is_file():
        src_project = pack
    dest = Path(args.dest).resolve() if args.dest else src_project
    sql = pack / "shenbaoshu.sql"
    if not sql.is_file():
        sql = src_project / "shenbaoshu.sql"
    if args.check:
        _print("迁移包检查通过: " + str(pack))
        _print("project " + str(src_project))
        _print("sql " + str(sql) + " " + str(sql.stat().st_size) + " bytes")
        dump = find_mysql_bin("mysqldump")
        mysql = find_mysql_bin("mysql")
        _print("mysqldump " + ("ok " + str(dump) if dump else "MISSING"))
        _print("mysql " + ("ok " + str(mysql) if mysql else "MISSING"))
        py = shutil.which("python") or shutil.which("python3")
        _print("python " + (py or "MISSING"))
        _print("新机实际还原: python scripts/migrate.py restore --from " + str(pack) + " --dest 新目录")
        return 0 if mysql and py else 3
    dest.mkdir(parents=True, exist_ok=True)
    if dest.resolve() != src_project.resolve():
        robocopy(src_project, dest, exclude_junctions=False, extra_xd=[".venv"])
    notes = rewrite_abs_paths(dest / "config.json", str(info.get("oldRoot") or ""), str(dest))
    for n in notes:
        _print(n)
    if not args.skip_mysql:
        cfg = _settings_from_dest(dest, args.mysql_password)
        import_mysql(sql, cfg)
    if not args.skip_venv:
        py = sys.executable
        venv_py = dest / ".venv" / "Scripts" / "python.exe"
        if not venv_py.is_file():
            _print("创建 .venv")
            subprocess.run([py, "-m", "venv", str(dest / ".venv")], check=True)
        pip = dest / ".venv" / "Scripts" / "pip.exe"
        _print("pip install -r requirements.txt")
        subprocess.run([str(pip), "install", "-r", str(dest / "requirements.txt")], check=True)
    if args.open_firewall:
        cmd_firewall(argparse.Namespace())
    ips = lan_ipv4()
    ip = ips[0] if ips else "新机局域网IP"
    _print("还原完成 " + str(dest))
    _print("放行 TCP 8767 后，各企微助手改为 http://{0}:8767".format(ip))
    _print("然后运行 " + str(dest / "start.cmd"))
    if args.start:
        start_project(dest)
    return 0


def cmd_firewall(_args: argparse.Namespace) -> int:
    ps1 = ROOT / "scripts" / "migrate_open_firewall.ps1"
    p = subprocess.run(
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(ps1)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    out = ((p.stdout or "") + (p.stderr or "")).strip()
    if out:
        _print(out)
    ips = lan_ipv4()
    _print("本机局域网 IP: " + (" ".join(ips) if ips else "(未检测到)"))
    _print("各电脑企微助手请改为 http://{0}:8767".format(ips[0] if ips else "新机IP"))
    return 0 if p.returncode == 0 else p.returncode


def _http_ok(url: str, timeout: float = 8.0) -> tuple[int, str]:
    try:
        import urllib.request

        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read(200).decode("utf-8", "replace")
            return int(resp.status), body
    except Exception as e:
        return 0, str(e)[:200]


def cmd_smoke(args: argparse.Namespace) -> int:
    errors: list[str] = []
    pack = Path(args.pack).resolve() if args.pack else None
    if pack:
        miss = required_missing(pack)
        if miss:
            errors.extend(miss)
        else:
            _print("pack ok " + str(pack))

    sys.path.insert(0, str(ROOT))
    from unittest.mock import patch
    from fastapi.testclient import TestClient

    from app.main import app
    from app import auth
    from app.parser_proc import BUNDLED_PARSER, find_parser_root
    from app.wecom_local import data_root
    from app.attachments import LOCAL_ATTACH_ROOT

    def fake(token: str):
        if token == "t-admin":
            return {"id": 1, "role": "admin", "username": "admin", "status": "active"}
        return None

    root = find_parser_root()
    if root != BUNDLED_PARSER:
        errors.append("find_parser_root 不是本仓库 parser/")
    else:
        _print("parser bundled ok")
    synced = data_root()
    if synced is None or not any(Path(synced).iterdir()):
        errors.append("会话同步目录为空")
    else:
        _print("sessions root " + str(synced))
    if not LOCAL_ATTACH_ROOT.is_dir() or not any(LOCAL_ATTACH_ROOT.iterdir()):
        errors.append("附件/ 为空")
    else:
        _print("local attach ok")
    sqlite = ROOT / "data" / "agent_review.sqlite"
    if not sqlite.is_file():
        errors.append("缺少 data/agent_review.sqlite")

    with patch.object(auth, "user_by_token", side_effect=fake):
        c = TestClient(app, raise_server_exceptions=False)
        h = {"Authorization": "Bearer t-admin"}
        r = c.get("/login.html")
        if r.status_code != 200:
            errors.append("login.html " + str(r.status_code))
        else:
            _print("login page ok")
        r = c.get("/api/tasks", headers=h)
        if r.status_code != 200:
            errors.append("/api/tasks " + str(r.status_code))
        else:
            tasks = (r.json() or {}).get("tasks") or []
            _print("tasks " + str(len(tasks)))
            if not tasks:
                errors.append("历史任务列表为空")
            else:
                tid = str(tasks[0].get("id") or "")
                one = c.get("/api/tasks/" + tid, headers=h)
                if one.status_code != 200:
                    errors.append("打开历史任务失败 " + str(one.status_code))
                else:
                    _print("history task " + tid)
        r = c.get("/api/admin/talent-ingest", headers=h)
        if r.status_code != 200:
            errors.append("入库看板 " + str(r.status_code) + " " + (r.text or "")[:120])
        else:
            body = r.json() or {}
            groups = body.get("groups") or body.get("items") or []
            _print("ingest groups " + str(len(groups)))
            if not groups:
                errors.append("入库看板没有记录")
        r = c.get("/api/agent-review/sessions", headers=h)
        if r.status_code != 200:
            errors.append("会话记录 " + str(r.status_code) + " " + (r.text or "")[:120])
        else:
            body = r.json() or {}
            items = body.get("data") or body.get("items") or body.get("sessions") or []
            _print("review sessions " + str(len(items)))
            if not items:
                errors.append("会话记录没有关注群")
        r = c.get("/api/wecom/groups?kind=group&limit=20", headers=h)
        if r.status_code != 200:
            errors.append("会话群列表 " + str(r.status_code))
        else:
            _print("wecom groups ok")
        r = c.get("/admin/chatinsight", headers=h)
        if r.status_code not in (200, 303):
            errors.append("解析控制台 " + str(r.status_code))
        else:
            _print("chatinsight route " + str(r.status_code))
        r = c.post("/api/tasks", json={}, headers=h)
        if r.status_code != 400:
            errors.append("新建任务入口异常 " + str(r.status_code))
        else:
            _print("create-task gate ok (empty body -> 400)")

    base = str(args.base or "http://127.0.0.1:3777").rstrip("/")
    st, _ = _http_ok(base + "/login.html")
    if st != 200:
        errors.append("现场 3777 未响应 login.html status=" + str(st))
    else:
        _print("live " + base + " ok")
    st, body = _http_ok("http://127.0.0.1:8767/api/sources")
    if st != 200:
        errors.append("现场 8767 未响应 sources status=" + str(st))
    else:
        _print("live parser 8767 ok")
        try:
            js = json.loads(body) if body.startswith("{") or body.startswith("[") else None
            if js is not None:
                _print("parser sources reachable")
        except Exception:
            pass

    if errors:
        _print("SMOKE FAIL")
        for e in errors:
            _print("  - " + e)
        return 1
    _print("SMOKE OK")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="申报书系统整机迁移")
    sub = p.add_subparsers(dest="cmd", required=True)
    pack = sub.add_parser("pack", help="旧机停服务并打包")
    pack.add_argument("--out", default="", help="输出目录，默认项目上一级 shenbaoshu-migrate-时间戳")
    pack.add_argument("--force", action="store_true")
    pack.add_argument("--no-restart", action="store_true", help="打包后不拉起本机服务")
    rest = sub.add_parser("restore", help="新机还原")
    rest.add_argument("--from", dest="src", required=True)
    rest.add_argument("--dest", default="")
    rest.add_argument("--check", action="store_true", help="只检查迁移包和本机工具，不拷贝")
    rest.add_argument("--skip-mysql", action="store_true")
    rest.add_argument("--skip-venv", action="store_true")
    rest.add_argument("--mysql-password", default=None)
    rest.add_argument("--open-firewall", action="store_true")
    rest.add_argument("--start", action="store_true")
    sub.add_parser("firewall", help="放行 TCP 8767 并打印局域网 IP")
    smoke = sub.add_parser("smoke", help="登录/任务/入库/会话/解析控制台自检")
    smoke.add_argument("--base", default="http://127.0.0.1:3777")
    smoke.add_argument("--pack", default="")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.cmd == "pack":
        return cmd_pack(args)
    if args.cmd == "restore":
        return cmd_restore(args)
    if args.cmd == "firewall":
        return cmd_firewall(args)
    if args.cmd == "smoke":
        return cmd_smoke(args)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
