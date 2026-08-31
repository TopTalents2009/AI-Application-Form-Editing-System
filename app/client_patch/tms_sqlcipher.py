# -*- coding: utf-8 -*-
"""只读打开 RCZX 客户端 SQLCipher 库（密码固定为 QObject）。"""
from __future__ import annotations

import ctypes
from pathlib import Path
from typing import Any, Optional

DEFAULT_PASSWORD = b"QObject"
SQLITE_OK = 0
SQLITE_ROW = 100
SQLITE_DONE = 101
SQLITE_OPEN_READONLY = 0x00000001
SQLITE_OPEN_READWRITE = 0x00000002
SQLITE_TRANSIENT = ctypes.c_void_p(-1)
SQLITE_NULL = 5
SQLITE_TEXT = 3
SQLITE_INTEGER = 1
SQLITE_FLOAT = 2
SQLITE_BLOB = 4


def package_paths(pkg: Path) -> dict[str, Path]:
    app = pkg / "App"
    return {
        "pkg": pkg,
        "app": app,
        "db": app / "Database" / "db",
        "dll": app / "qsqlcipher.dll",
        "dll_driver": app / "sqldrivers" / "qsqlcipher.dll",
    }


def resolve_dll(pkg: Path) -> Path:
    paths = package_paths(pkg)
    if paths["dll"].is_file():
        return paths["dll"]
    if paths["dll_driver"].is_file():
        return paths["dll_driver"]
    raise FileNotFoundError(f"qsqlcipher.dll not found under {pkg}")


class SqlCipherDB:
    def __init__(
        self,
        db_path: Path,
        dll_path: Path,
        password: bytes = DEFAULT_PASSWORD,
        readonly: bool = True,
    ):
        self.db_path = Path(db_path)
        self.dll_path = Path(dll_path)
        self.password = password
        self.readonly = readonly
        self.lib = ctypes.CDLL(str(self.dll_path))
        self._setup_api()
        self.h = ctypes.c_void_p()
        flags = SQLITE_OPEN_READONLY if readonly else SQLITE_OPEN_READWRITE
        rc = self.lib.sqlite3_open_v2(
            str(self.db_path).encode("utf-8"),
            ctypes.byref(self.h),
            flags,
            None,
        )
        if rc != SQLITE_OK:
            raise RuntimeError(f"sqlite3_open_v2 failed rc={rc} path={self.db_path}")
        rc = self.lib.sqlite3_key(self.h, self.password, len(self.password))
        if rc != SQLITE_OK:
            self.close()
            raise RuntimeError(f"sqlite3_key failed rc={rc}")
        self.query("SELECT count(*) FROM sqlite_master;")

    def _setup_api(self) -> None:
        L = self.lib
        L.sqlite3_open_v2.argtypes = [
            ctypes.c_char_p,
            ctypes.POINTER(ctypes.c_void_p),
            ctypes.c_int,
            ctypes.c_char_p,
        ]
        L.sqlite3_open_v2.restype = ctypes.c_int
        L.sqlite3_close.argtypes = [ctypes.c_void_p]
        L.sqlite3_close.restype = ctypes.c_int
        L.sqlite3_key.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_int]
        L.sqlite3_key.restype = ctypes.c_int
        L.sqlite3_prepare_v2.argtypes = [
            ctypes.c_void_p,
            ctypes.c_char_p,
            ctypes.c_int,
            ctypes.POINTER(ctypes.c_void_p),
            ctypes.POINTER(ctypes.c_char_p),
        ]
        L.sqlite3_prepare_v2.restype = ctypes.c_int
        L.sqlite3_step.argtypes = [ctypes.c_void_p]
        L.sqlite3_step.restype = ctypes.c_int
        L.sqlite3_finalize.argtypes = [ctypes.c_void_p]
        L.sqlite3_finalize.restype = ctypes.c_int
        L.sqlite3_errmsg.argtypes = [ctypes.c_void_p]
        L.sqlite3_errmsg.restype = ctypes.c_char_p
        L.sqlite3_column_count.argtypes = [ctypes.c_void_p]
        L.sqlite3_column_count.restype = ctypes.c_int
        L.sqlite3_column_name.argtypes = [ctypes.c_void_p, ctypes.c_int]
        L.sqlite3_column_name.restype = ctypes.c_char_p
        L.sqlite3_column_type.argtypes = [ctypes.c_void_p, ctypes.c_int]
        L.sqlite3_column_type.restype = ctypes.c_int
        L.sqlite3_column_text.argtypes = [ctypes.c_void_p, ctypes.c_int]
        L.sqlite3_column_text.restype = ctypes.c_char_p
        L.sqlite3_column_int64.argtypes = [ctypes.c_void_p, ctypes.c_int]
        L.sqlite3_column_int64.restype = ctypes.c_int64
        L.sqlite3_column_double.argtypes = [ctypes.c_void_p, ctypes.c_int]
        L.sqlite3_column_double.restype = ctypes.c_double
        L.sqlite3_bind_text.argtypes = [
            ctypes.c_void_p,
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_int,
            ctypes.c_void_p,
        ]
        L.sqlite3_bind_text.restype = ctypes.c_int
        L.sqlite3_bind_int64.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int64]
        L.sqlite3_bind_int64.restype = ctypes.c_int
        L.sqlite3_bind_null.argtypes = [ctypes.c_void_p, ctypes.c_int]
        L.sqlite3_bind_null.restype = ctypes.c_int
        L.sqlite3_changes.argtypes = [ctypes.c_void_p]
        L.sqlite3_changes.restype = ctypes.c_int

    def _err(self) -> str:
        e = self.lib.sqlite3_errmsg(self.h)
        return e.decode("utf-8", "replace") if e else ""

    def close(self) -> None:
        if self.h:
            self.lib.sqlite3_close(self.h)
            self.h = ctypes.c_void_p()

    def query(self, sql: str) -> list[dict[str, Any]]:
        stmt = ctypes.c_void_p()
        tail = ctypes.c_char_p()
        rc = self.lib.sqlite3_prepare_v2(
            self.h, sql.encode("utf-8"), -1, ctypes.byref(stmt), ctypes.byref(tail)
        )
        if rc != SQLITE_OK:
            raise RuntimeError(f"prepare rc={rc}: {self._err()} sql={sql[:160]}")
        try:
            cols = self.lib.sqlite3_column_count(stmt)
            names = [
                self.lib.sqlite3_column_name(stmt, i).decode("utf-8", "replace")
                for i in range(cols)
            ]
            rows: list[dict[str, Any]] = []
            while True:
                rc = self.lib.sqlite3_step(stmt)
                if rc == SQLITE_ROW:
                    row: dict[str, Any] = {}
                    for i, name in enumerate(names):
                        t = self.lib.sqlite3_column_type(stmt, i)
                        if t == SQLITE_NULL:
                            row[name] = None
                        elif t == SQLITE_INTEGER:
                            row[name] = int(self.lib.sqlite3_column_int64(stmt, i))
                        elif t == SQLITE_FLOAT:
                            row[name] = float(self.lib.sqlite3_column_double(stmt, i))
                        elif t == SQLITE_BLOB:
                            row[name] = None
                        else:
                            txt = self.lib.sqlite3_column_text(stmt, i)
                            row[name] = txt.decode("utf-8", "replace") if txt else ""
                    rows.append(row)
                elif rc == SQLITE_DONE:
                    break
                else:
                    raise RuntimeError(f"step rc={rc}: {self._err()}")
            return rows
        finally:
            self.lib.sqlite3_finalize(stmt)

    def execute(self, sql: str, params: Optional[list[Any]] = None) -> int:
        if self.readonly:
            raise RuntimeError("DB opened readonly; cannot execute: " + sql[:80])
        stmt = ctypes.c_void_p()
        tail = ctypes.c_char_p()
        rc = self.lib.sqlite3_prepare_v2(
            self.h, sql.encode("utf-8"), -1, ctypes.byref(stmt), ctypes.byref(tail)
        )
        if rc != SQLITE_OK:
            raise RuntimeError(f"prepare rc={rc}: {self._err()} sql={sql[:160]}")
        keep: list[Any] = []
        try:
            for i, val in enumerate(params or [], start=1):
                if val is None:
                    self.lib.sqlite3_bind_null(stmt, i)
                elif isinstance(val, int) and not isinstance(val, bool):
                    self.lib.sqlite3_bind_int64(stmt, i, val)
                else:
                    raw = str(val).encode("utf-8")
                    buf = ctypes.create_string_buffer(raw)
                    keep.append(buf)
                    self.lib.sqlite3_bind_text(stmt, i, buf, len(raw), SQLITE_TRANSIENT)
            rc = self.lib.sqlite3_step(stmt)
            if rc not in (SQLITE_DONE, SQLITE_ROW):
                raise RuntimeError(f"step rc={rc}: {self._err()} sql={sql[:120]}")
            while rc == SQLITE_ROW:
                rc = self.lib.sqlite3_step(stmt)
            return int(self.lib.sqlite3_changes(self.h))
        finally:
            self.lib.sqlite3_finalize(stmt)

    def tables(self) -> list[str]:
        rows = self.query(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name NOT LIKE 'sqlite_%' ORDER BY name"
        )
        return [r["name"] for r in rows]


def open_package_db(
    package: Path,
    password: bytes = DEFAULT_PASSWORD,
    readonly: bool = True,
) -> tuple[SqlCipherDB, dict[str, Path]]:
    pkg = Path(package)
    paths = package_paths(pkg)
    dll = resolve_dll(pkg)
    if not paths["db"].is_file() or paths["db"].stat().st_size == 0:
        raise FileNotFoundError(f"db not found: {paths['db']}")
    db = SqlCipherDB(paths["db"], dll, password=password, readonly=readonly)
    return db, paths
