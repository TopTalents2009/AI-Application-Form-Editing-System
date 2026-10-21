"""Locate WeCom (WXWork) data roots on Windows.

The chat store is not always ``%USERPROFILE%\\Documents\\WXWork``. Company
images redirect the Documents folder (Explorer User Shell Folders\\Personal,
for example ``D:\\Users\\Documents``) while leaving
``HKCU\\Software\\Tencent\\WXWork\\DataLocationPath`` empty. Newer installs
with ``DataLocation=1`` and no custom path keep the store under
``%APPDATA%\\Tencent\\WXWork``.
"""
import os

_WXWORK_KEY = r"Software\Tencent\WXWork"
_SHELL_KEYS = (
    r"Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders",
    r"Software\Microsoft\Windows\CurrentVersion\Explorer\Shell Folders",
)


def _reg_value(subkey: str, name: str) -> str:
    """Read an HKCU value. Expand ``%VAR%``. Missing values are empty."""
    try:
        import winreg
    except ImportError:
        return ""
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, subkey) as key:
            value, _ = winreg.QueryValueEx(key, name)
    except OSError:
        return ""
    if isinstance(value, (int, float)):
        return str(int(value))
    if not isinstance(value, str):
        return ""
    return os.path.expandvars(value).strip().strip('"')


def append_wxwork_base(path: str, bases: list) -> None:
    """Add a directory and, when present, a nested ``WXWork`` subdirectory."""
    if not path:
        return
    path = os.path.abspath(os.path.expandvars(str(path)))
    seen = {os.path.normcase(item) for item in bases}

    def add(candidate: str) -> None:
        if not os.path.isdir(candidate):
            return
        key = os.path.normcase(candidate)
        if key in seen:
            return
        seen.add(key)
        bases.append(candidate)

    add(path)
    add(os.path.join(path, "WXWork"))


def collect_wxwork_base_dirs() -> list:
    """WXWork roots that may contain ``<account_id>\\Data\\message.db``."""
    bases = []
    custom = _reg_value(_WXWORK_KEY, "DataLocationPath")
    append_wxwork_base(custom, bases)

    for subkey in _SHELL_KEYS:
        personal = _reg_value(subkey, "Personal")
        if personal:
            append_wxwork_base(os.path.join(personal, "WXWork"), bases)

    userprofile = os.environ.get("USERPROFILE", "")
    if userprofile:
        append_wxwork_base(
            os.path.join(userprofile, "Documents", "WXWork"),
            bases,
        )

    appdata = os.environ.get("APPDATA", "")
    if appdata:
        app_root = os.path.join(appdata, "Tencent", "WXWork")
        append_wxwork_base(app_root, bases)
        # DataLocation=1 stores the tree under ...\\WXWork\\Data.
        append_wxwork_base(os.path.join(app_root, "Data"), bases)
    return bases
