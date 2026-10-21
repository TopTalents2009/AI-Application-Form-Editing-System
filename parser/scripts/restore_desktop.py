"""Restore desktop shortcuts from Start Menu and installed programs."""
import os
import shutil
import sys
import winreg

DESKTOP = os.path.join(os.environ["USERPROFILE"], "Desktop")
START_MENUS = [
    os.path.join(os.environ["APPDATA"], r"Microsoft\Windows\Start Menu\Programs"),
    os.path.join(os.environ.get("ProgramData", r"C:\ProgramData"), r"Microsoft\Windows\Start Menu\Programs"),
    r"C:\Users\Public\Desktop",
]
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RECOVER_DIR = os.path.join(DESKTOP, "_recovered_shortcuts")


def log(msg: str):
    print(msg, flush=True)


def ensure_dir(path: str):
    os.makedirs(path, exist_ok=True)


def unique_name(dest_dir: str, name: str) -> str:
    base, ext = os.path.splitext(name)
    candidate = os.path.join(dest_dir, name)
    n = 1
    while os.path.exists(candidate):
        candidate = os.path.join(dest_dir, f"{base} ({n}){ext}")
        n += 1
    return candidate


def copy_start_menu_shortcuts():
    copied = 0
    seen = set()
    for root_dir in START_MENUS:
        if not os.path.isdir(root_dir):
            continue
        for dirpath, _, files in os.walk(root_dir):
            for name in files:
                if not name.lower().endswith(".lnk"):
                    continue
                src = os.path.join(dirpath, name)
                key = name.lower()
                if key in seen:
                    continue
                seen.add(key)
                dst = unique_name(DESKTOP, name)
                try:
                    shutil.copy2(src, dst)
                    copied += 1
                    log(f"  + {name}")
                except OSError as exc:
                    log(f"  ! skip {name}: {exc}")
    return copied


def create_shortcut(name: str, target: str, workdir: str = "", icon: str = ""):
    try:
        import win32com.client
    except ImportError:
        return False

    path = unique_name(DESKTOP, f"{name}.lnk")
    shell = win32com.client.Dispatch("WScript.Shell")
    shortcut = shell.CreateShortcut(path)
    shortcut.TargetPath = target
    if workdir:
        shortcut.WorkingDirectory = workdir
    if icon:
        shortcut.IconLocation = icon
    shortcut.Save()
    log(f"  + {os.path.basename(path)} -> {target}")
    return True


def create_project_shortcuts():
    items = [
        ("企业微信聊天记录解析", os.path.join(PROJECT_ROOT, "start.bat"), PROJECT_ROOT),
        ("拉取远端企业微信", os.path.join(PROJECT_ROOT, "pull_remote.bat"), PROJECT_ROOT),
    ]
    for name, target, workdir in items:
        if os.path.exists(target):
            if not create_shortcut(name, target, workdir):
                bat = unique_name(DESKTOP, f"{name}.bat")
                shutil.copy2(target, bat)
                log(f"  + {os.path.basename(bat)} (copied)")


def create_common_app_shortcuts():
    common = [
        ("Chrome", r"C:\Program Files\Google\Chrome\Application\chrome.exe"),
        ("Chrome", r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"),
        ("Edge", r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"),
        ("微信", os.path.join(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"), r"Tencent\WeChat\WeChat.exe")),
        ("企业微信", os.path.join(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"), r"WXWork\WXWork.exe")),
        ("Cursor", os.path.join(os.environ.get("LOCALAPPDATA", ""), r"Programs\cursor\Cursor.exe")),
        ("VS Code", os.path.join(os.environ.get("LOCALAPPDATA", ""), r"Programs\Microsoft VS Code\Code.exe")),
        ("记事本", r"C:\Windows\System32\notepad.exe"),
        ("PowerShell", r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe"),
        ("此电脑", "::{20D04FE0-3AEA-1069-A2D8-08002B30309D}"),
    ]
    created = set()
    for name, target in common:
        if name in created or not os.path.exists(target):
            continue
        if create_shortcut(name, target):
            created.add(name)


def scan_installed_programs():
    roots = [
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall"),
        (winreg.HKEY_CURRENT_USER, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
    ]
    found = []
    for hive, subkey in roots:
        try:
            key = winreg.OpenKey(hive, subkey)
        except OSError:
            continue
        i = 0
        while True:
            try:
                name = winreg.EnumKey(key, i)
                i += 1
            except OSError:
                break
            try:
                with winreg.OpenKey(key, name) as appkey:
                    display, _ = winreg.QueryValueEx(appkey, "DisplayName")
                    try:
                        icon, _ = winreg.QueryValueEx(appkey, "DisplayIcon")
                    except OSError:
                        icon = ""
                    try:
                        location, _ = winreg.QueryValueEx(appkey, "InstallLocation")
                    except OSError:
                        location = ""
                exe = icon.split(",")[0].strip('"') if icon else ""
                if exe and os.path.exists(exe) and display:
                    found.append((display, exe, location))
            except OSError:
                continue
    return found


def main():
    log("=== Restore Desktop Shortcuts ===")
    log(f"Desktop: {DESKTOP}")
    ensure_dir(RECOVER_DIR)

    log("\n[1] Copy shortcuts from Start Menu...")
    copied = copy_start_menu_shortcuts()
    log(f"Copied {copied} shortcuts from Start Menu")

    log("\n[2] Create common app shortcuts...")
    create_common_app_shortcuts()

    log("\n[3] Create project shortcuts...")
    create_project_shortcuts()

    log("\n[4] Scan installed programs...")
    programs = scan_installed_programs()
    created = 0
    for display, exe, _ in programs[:40]:
        safe = "".join(ch if ch not in r'\/:*?"<>|' else "_" for ch in display)[:60]
        target_name = safe.strip() or "app"
        dst = os.path.join(DESKTOP, f"{target_name}.lnk")
        if os.path.exists(dst):
            continue
        if create_shortcut(target_name, exe):
            created += 1
    log(f"Created {created} shortcuts from installed programs")

    desktop_items = [f for f in os.listdir(DESKTOP) if f != "_recovered_shortcuts"]
    log(f"\nDone. Desktop now has {len(desktop_items)} items.")
    log("For deeper recovery of deleted personal files, run Recuva as Administrator:")
    log(f"  {os.path.join(os.environ.get('TEMP', ''), 'recuva_setup.exe')}")


if __name__ == "__main__":
    main()
