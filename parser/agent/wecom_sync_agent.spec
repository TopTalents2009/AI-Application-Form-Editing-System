# -*- mode: python ; coding: utf-8 -*-
import os

spec_dir = os.path.dirname(os.path.abspath(SPEC))
project = os.path.dirname(spec_dir)
decrypt = os.path.join(project, "tools", "wechat-decrypt")

decrypt_scripts = [
    "find_wxwork_keys.py",
    "decrypt_wxwork_db.py",
    "wxwork_crypto.py",
    "key_utils.py",
    "key_scan_common.py",
    "config.py",
]

datas = [(os.path.join(decrypt, name), "tools/wechat-decrypt") for name in decrypt_scripts]
datas.append((os.path.join(project, "core-wecom", "message_decode.py"), "core-wecom"))
datas.append((os.path.join(project, "core-wecom", "attachments.py"), "core-wecom"))
datas.append((os.path.join(project, "agent", "VERSION"), "agent"))
datas.append((os.path.join(project, "agent", "CHANGELOG.md"), "agent"))

a = Analysis(
    [os.path.join(spec_dir, "wecom_sync_agent.py")],
    pathex=[decrypt, project],
    binaries=[],
    datas=datas,
    hiddenimports=[
        "Crypto",
        "Crypto.Cipher",
        "Crypto.Cipher.AES",
        "find_wxwork_keys",
        "decrypt_wxwork_db",
        "wxwork_crypto",
        "key_utils",
        "key_scan_common",
        "config",
        "shared",
        "shared.wxwork_paths",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="WeComSyncAgent",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
