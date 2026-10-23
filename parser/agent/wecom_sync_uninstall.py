"""卸载企业微信同步助手：关掉进程、取消自启、删除本机数据。"""
import os
import shutil
import subprocess
import sys
import time
import tkinter as tk
from tkinter import messagebox, ttk


AGENT_PROCESS = "WeComSyncAgent.exe"
UNINSTALLER_PROCESS = "WeComSyncUninstall.exe"
AUTOSTART_NAME = "WeComSyncAgent"
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
WORK_DIR_NAME = "WeComSyncAgent"


def work_dir(environ=None) -> str:
    env = environ if environ is not None else os.environ
    root = env.get("APPDATA") or os.path.expanduser("~")
    return os.path.join(root, WORK_DIR_NAME)


def parse_quoted_exe(command: str) -> str:
    text = (command or "").strip()
    if not text:
        return ""
    if text.startswith('"'):
        end = text.find('"', 1)
        if end > 1:
            return text[1:end]
    return text.split()[0]


def read_autostart_command() -> str:
    if os.name != "nt":
        return ""
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_READ) as key:
            value, _ = winreg.QueryValueEx(key, AUTOSTART_NAME)
        return str(value or "")
    except FileNotFoundError:
        return ""
    except OSError as exc:
        if getattr(exc, "winerror", None) == 2:
            return ""
        raise


def disable_autostart() -> bool:
    if os.name != "nt":
        return False
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
            winreg.DeleteValue(key, AUTOSTART_NAME)
        return True
    except FileNotFoundError:
        return False
    except OSError as exc:
        if getattr(exc, "winerror", None) == 2:
            return False
        raise


def read_lock_pid(root: str) -> int:
    path = os.path.join(root, "agent.lock")
    if not os.path.isfile(path):
        return 0
    try:
        with open(path, encoding="utf-8") as handle:
            return int((handle.read() or "").strip())
    except (OSError, ValueError):
        return 0


def _no_window_flags() -> int:
    if os.name != "nt":
        return 0
    return int(getattr(subprocess, "CREATE_NO_WINDOW", 0))


def _run_hidden(args):
    return subprocess.run(
        args,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        creationflags=_no_window_flags(),
    )


def self_paths() -> set:
    out = set()
    for raw in (sys.executable, os.path.abspath(__file__)):
        if raw:
            out.add(os.path.normcase(os.path.abspath(raw)))
    return out


def stop_agent_processes(root: str, skip_pids=None) -> list:
    logs = []
    skip = {int(p) for p in (skip_pids or []) if int(p) > 0}
    skip.add(os.getpid())
    if os.name != "nt":
        return logs
    pid = read_lock_pid(root)
    if pid > 0 and pid not in skip:
        out = _run_hidden(["taskkill", "/F", "/PID", str(pid), "/T"])
        if out.returncode == 0:
            logs.append("已结束助手进程 PID " + str(pid))
        skip.add(pid)
    out = _run_hidden(["taskkill", "/F", "/IM", AGENT_PROCESS, "/T"])
    text = (out.stdout or "") + (out.stderr or "")
    if out.returncode == 0:
        logs.append("已结束 " + AGENT_PROCESS)
    elif "not found" in text.lower() or "没有运行" in text or "not running" in text.lower():
        logs.append("助手当前没有在运行")
    return logs


def remove_tree(path: str, retries: int = 8) -> None:
    last = None
    for _ in range(max(1, retries)):
        try:
            if os.path.isdir(path):
                shutil.rmtree(path)
            return
        except OSError as exc:
            last = exc
            time.sleep(0.4)
    if last:
        raise last


def remove_file(path: str, skip: set) -> str:
    if not path:
        return ""
    abs_path = os.path.normcase(os.path.abspath(path))
    if abs_path in skip:
        return ""
    if not os.path.isfile(path):
        return ""
    last = None
    for _ in range(6):
        try:
            os.remove(path)
            return path
        except OSError as exc:
            last = exc
            time.sleep(0.4)
    raise last or OSError("无法删除 " + path)


def run_uninstall(
    *,
    environ=None,
    autostart_cmd=None,
    disable_autostart_fn=None,
    stop_fn=None,
    remove_exe=True,
    skip_pids=None,
) -> dict:
    logs = []
    errors = []
    root = work_dir(environ)
    command = read_autostart_command() if autostart_cmd is None else autostart_cmd
    agent_exe = parse_quoted_exe(command)

    stopper = stop_fn or stop_agent_processes
    try:
        for line in stopper(root, skip_pids) or []:
            if line:
                logs.append(line)
    except Exception as exc:
        errors.append("结束助手进程失败：" + str(exc))

    disabler = disable_autostart_fn or disable_autostart
    try:
        if disabler():
            logs.append("已关闭开机自启")
        else:
            logs.append("没有开机自启项")
    except Exception as exc:
        errors.append("关闭开机自启失败：" + str(exc))

    if os.path.isdir(root):
        try:
            remove_tree(root)
            logs.append("已删除数据目录 " + root)
        except OSError as exc:
            errors.append("未能完全删除数据目录：" + str(exc))
    else:
        logs.append("没有数据目录 " + root)

    if remove_exe and agent_exe:
        try:
            deleted = remove_file(agent_exe, self_paths())
            if deleted:
                logs.append("已删除助手程序 " + deleted)
        except OSError:
            errors.append("未能删除助手程序，请手动删除：" + agent_exe)

    return {
        "ok": not errors,
        "logs": logs,
        "errors": errors,
        "work_dir": root,
        "agent_exe": agent_exe,
    }


def format_result(result: dict) -> str:
    lines = list(result.get("logs") or [])
    for item in result.get("errors") or []:
        lines.append("失败：" + item)
    if result.get("ok"):
        lines.append("卸载完成。")
    else:
        lines.append("卸载未完全成功，请按上面提示处理。")
    lines.append("若桌面或下载文件夹还留着 WeComSyncAgent.exe，可手动删掉。")
    return "\n".join(lines)


def _show_gui() -> int:
    root = tk.Tk()
    root.title("卸载企业微信同步助手")
    root.geometry("520x360")
    root.resizable(False, False)
    try:
        root.attributes("-topmost", True)
        root.after(400, lambda: root.attributes("-topmost", False))
    except tk.TclError:
        pass

    frame = ttk.Frame(root, padding=16)
    frame.pack(fill="both", expand=True)

    ttk.Label(frame, text="卸载企业微信同步助手", font=("Microsoft YaHei UI", 14, "bold")).pack(anchor="w")
    ttk.Label(
        frame,
        text="运行后会结束 WeComSyncAgent、取消开机自启，并删除本机数据目录：\n"
        + work_dir(),
        wraplength=470,
        justify="left",
    ).pack(anchor="w", pady=(8, 12))

    log = tk.Text(frame, height=10, wrap="word", font=("Microsoft YaHei UI", 9))
    log.pack(fill="both", expand=True)
    log.insert("end", "点「立即卸载」开始。不会卸载企业微信本身。\n")
    log.configure(state="disabled")

    def append(text: str) -> None:
        log.configure(state="normal")
        log.insert("end", text.rstrip() + "\n")
        log.see("end")
        log.configure(state="disabled")
        root.update_idletasks()

    def do_uninstall() -> None:
        if not messagebox.askyesno("确认卸载", "确定要从这台电脑卸掉企业微信同步助手吗？"):
            return
        btn.configure(state="disabled")
        append("正在卸载…")
        result = run_uninstall()
        append(format_result(result))
        if result.get("ok"):
            messagebox.showinfo("卸载完成", "企业微信同步助手已从本机卸掉。")
        else:
            messagebox.showwarning("未完全卸掉", "有步骤失败，请查看窗口里的说明。")
        btn.configure(state="normal")

    btn = ttk.Button(frame, text="立即卸载", command=do_uninstall)
    btn.pack(anchor="e", pady=(12, 0))
    root.mainloop()
    return 0


def main(argv=None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if "--quiet" in args or "-q" in args:
        result = run_uninstall()
        sys.stdout.write(format_result(result) + "\n")
        return 0 if result.get("ok") else 1
    return _show_gui()


if __name__ == "__main__":
    sys.exit(main())
