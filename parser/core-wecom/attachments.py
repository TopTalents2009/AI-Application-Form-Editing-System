"""Resolve WeCom message attachments to local cache files."""
import os
import sqlite3
from dataclasses import dataclass
from typing import Dict, Optional

IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"}
IMAGE_CONTENT_TYPES = {4, 15}
FILE_CONTENT_TYPES = {14, 16, 20, 7}


@dataclass
class AttachmentInfo:
    message_id: int
    name: str = ""
    size: int = 0
    md5: str = ""
    local_path: str = ""
    kind: str = ""  # image | file | voice
    available: bool = False


class WeComAttachmentResolver:
    def __init__(self, file_db_path: str, account_roots, finder=None):
        self.account_roots = list(account_roots or [])
        self._index: Dict[int, dict] = {}
        self._path_cache: Dict[int, str] = {}
        self._finder = finder
        self._load_index(file_db_path)

    @property
    def cache_roots(self):
        roots = []
        for account_root in self.account_roots:
            roots.extend([
                os.path.join(account_root, "Cache", "Image"),
                os.path.join(account_root, "Cache", "File"),
                os.path.join(account_root, "Cache", "Voice"),
            ])
        return roots

    def _load_index(self, file_db_path: str):
        if not file_db_path or not os.path.exists(file_db_path):
            return
        conn = sqlite3.connect(file_db_path)
        try:
            row = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='file_table4'"
            ).fetchone()
            if not row:
                return
            cols = [d[0] for d in conn.execute("SELECT * FROM file_table4 LIMIT 1").description]
            for row in conn.execute("SELECT * FROM file_table4").fetchall():
                item = dict(zip(cols, row))
                message_id = int(item.get("message_id") or 0)
                if message_id:
                    self._index[message_id] = item
        finally:
            conn.close()

    def _kind_for_name(self, name: str, content_type: Optional[int] = None) -> str:
        ext = os.path.splitext(name)[1].lower()
        if content_type in IMAGE_CONTENT_TYPES or ext in IMAGE_EXTENSIONS:
            return "image"
        if content_type == 7:
            return "voice"
        return "file"

    def _find_in_cache(self, name: str, md5: str = "") -> str:
        if not name:
            return ""
        name = os.path.basename(name.replace("\\", "/"))
        if self._finder:
            return self._finder(name, md5) or ""
        for root in self.cache_roots:
            if not os.path.isdir(root):
                continue
            for dirpath, _, files in os.walk(root):
                for filename in files:
                    if filename == name:
                        return os.path.join(dirpath, filename)
                    if name in filename:
                        return os.path.join(dirpath, filename)
                    if md5 and md5.lower() in filename.lower():
                        return os.path.join(dirpath, filename)
        return ""

    def resolve(
        self,
        message_id: int,
        content_type: Optional[int] = None,
        filename_hint: str = "",
    ) -> AttachmentInfo:
        if message_id in self._path_cache:
            path = self._path_cache[message_id]
            meta = self._index.get(message_id, {})
            name = meta.get("name") or filename_hint or os.path.basename(path)
            kind = self._kind_for_name(name, content_type)
            return AttachmentInfo(
                message_id=message_id,
                name=name,
                size=int(meta.get("size") or 0),
                md5=str(meta.get("md5") or ""),
                local_path=path,
                kind=kind,
                available=True,
            )

        meta = self._index.get(int(message_id or 0), {})
        name = str(meta.get("name") or filename_hint or "")
        md5 = str(meta.get("md5") or "")
        size = int(meta.get("size") or 0)
        kind = self._kind_for_name(name or filename_hint, content_type)

        path = self._find_in_cache(name, md5)
        if not path and filename_hint:
            path = self._find_in_cache(filename_hint, md5)

        if path:
            self._path_cache[message_id] = path
        return AttachmentInfo(
            message_id=message_id,
            name=name or filename_hint or os.path.basename(path),
            size=size,
            md5=md5,
            local_path=path,
            kind=kind,
            available=bool(path and (self._finder or os.path.isfile(path))),
        )

    def get_allowed_path(self, message_id: int) -> str:
        return self.resolve_file(message_id)

    def resolve_file(self, message_id: int = 0, filename_hint: str = "") -> str:
        info = self.resolve(int(message_id or 0), filename_hint=filename_hint)
        if not info.available or not info.local_path:
            return ""
        if self._finder:
            return info.local_path
        real_path = os.path.realpath(info.local_path)
        for root in self.cache_roots:
            root_real = os.path.realpath(root)
            if real_path.startswith(root_real + os.sep) or real_path == root_real:
                return real_path
        return ""
