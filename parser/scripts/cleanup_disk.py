"""Free disk space by removing large optional project data."""
import os
import shutil
import sys

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EXPORT = os.path.join(PROJECT_ROOT, "export")

REMOVE_DIRS = [
    os.path.join(EXPORT, "wxwork_remote"),
    os.path.join(EXPORT, "wxwork_decrypted.bak_20260911_144305"),
    os.path.join(EXPORT, "wxwork_decrypted.bak_20260911_144348"),
    os.path.join(EXPORT, "wxwork_export"),
]

KEEP_DIRS = [
    os.path.join(EXPORT, "wxwork_decrypted"),
]


def dir_size(path: str) -> int:
    total = 0
    for root, _, files in os.walk(path):
        for name in files:
            try:
                total += os.path.getsize(os.path.join(root, name))
            except OSError:
                pass
    return total


def main():
    freed = 0
    for path in REMOVE_DIRS:
        if not os.path.exists(path):
            print(f"skip (not found): {path}")
            continue
        size = dir_size(path)
        print(f"removing {path} ({size / (1024**3):.2f} GB)...")
        shutil.rmtree(path, ignore_errors=True)
        if not os.path.exists(path):
            freed += size
            print("  done")
        else:
            print("  partial/failed")

    print(f"\nFreed about {freed / (1024**3):.2f} GB")
    print("Kept:", KEEP_DIRS[0])


if __name__ == "__main__":
    main()
