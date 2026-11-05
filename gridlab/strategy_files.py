"""Safe, local-only storage for user-authored strategy source files.

Files are parsed as Python syntax, never imported or executed by the listing or
save API. Strategy execution is delegated to the isolated engine runtime.
"""
from __future__ import annotations

import ast
import hashlib
import json
import os
import re
import secrets
import stat
import threading
from pathlib import Path


FILENAME_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,79}\.py\Z")
MAX_SOURCE_BYTES = 256 * 1024


class StrategyFileError(ValueError):
    """Invalid strategy filename, source, or optimistic-lock token."""


class StrategyFileConflict(StrategyFileError):
    """An optimistic update used a stale hash."""


class StrategyFileNotFound(LookupError):
    """Requested strategy filename is not present in the local directory."""


class StrategyFiles:
    def __init__(self, directory: str | Path):
        self.directory = Path(directory).absolute()
        self.directory.mkdir(parents=True, exist_ok=True)
        if self.directory.is_symlink():
            raise StrategyFileError("Strategy directory cannot be a symbolic link.")
        self._lock = threading.Lock()

    @staticmethod
    def _hash(source: str) -> str:
        return hashlib.sha256(source.encode("utf-8")).hexdigest()

    def _path(self, filename: str) -> Path:
        if not isinstance(filename, str) or not FILENAME_RE.fullmatch(filename) or filename in {".", ".."}:
            raise StrategyFileError("Filename must be a simple .py basename.")
        path = self.directory / filename
        if path.parent != self.directory or path.is_symlink():
            raise StrategyFileError("Strategy path must be a regular file inside the strategy directory.")
        return path

    @staticmethod
    def _metadata(filename: str, source: str) -> dict:
        try:
            tree = ast.parse(source, filename=filename)
            description = ast.get_docstring(tree) or ""
            name, settings, warmup = None, {}, 0
            for node in tree.body:
                if isinstance(node, (ast.Assign, ast.AnnAssign)):
                    targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                    value = node.value
                    for target in targets:
                        if isinstance(target, ast.Name) and target.id == "NAME":
                            candidate = ast.literal_eval(value)
                            if not isinstance(candidate, str):
                                raise ValueError("NAME must be a string literal.")
                            name = candidate
                        elif isinstance(target, ast.Name) and target.id == "SETTINGS":
                            candidate = ast.literal_eval(value)
                            if not isinstance(candidate, dict):
                                raise ValueError("SETTINGS must be a dictionary literal.")
                            settings = candidate
                        elif isinstance(target, ast.Name) and target.id == "WARMUP":
                            candidate = ast.literal_eval(value)
                            if isinstance(candidate, bool) or not isinstance(candidate, int) or candidate < 0:
                                raise ValueError("WARMUP must be a nonnegative integer literal.")
                            warmup = candidate
            functions = [n for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
                         and n.name == "on_bar"]
            if not functions or isinstance(functions[-1], ast.AsyncFunctionDef):
                raise ValueError("Strategy must define a synchronous on_bar(ctx).")
            if len(functions[-1].args.args) != 1:
                raise ValueError("on_bar must accept exactly one context argument.")
            def check_json(value):
                if value is None or isinstance(value, (str, bool, int)):
                    return
                if isinstance(value, float):
                    json.dumps(value, allow_nan=False)
                    return
                if isinstance(value, list):
                    for item in value:
                        check_json(item)
                    return
                if isinstance(value, dict) and all(isinstance(key, str) for key in value):
                    for item in value.values():
                        check_json(item)
                    return
                raise ValueError("SETTINGS must contain only JSON values with string keys.")
            check_json(settings)
            return {"name": name or Path(filename).stem, "description": description,
                    "settings": settings, "warmup": warmup, "error": ""}
        except (SyntaxError, ValueError, TypeError) as exc:
            return {"name": Path(filename).stem, "description": "", "settings": {},
                    "warmup": 0, "error": str(exc)}

    def list(self) -> list[dict]:
        result = []
        for path in sorted(self.directory.glob("*.py"), key=lambda p: p.name.casefold()):
            if path.is_symlink():
                continue
            try:
                result.append(self.get(path.name))
            except (OSError, UnicodeError, StrategyFileError, StrategyFileNotFound) as exc:
                result.append({"filename": path.name, "source": "", "name": path.stem,
                               "description": "", "settings": {}, "warmup": 0,
                               "error": str(exc), "hash": ""})
        return result

    def get(self, filename: str) -> dict:
        self._path(filename)
        dir_fd = os.open(self.directory, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) |
                         getattr(os, "O_NOFOLLOW", 0))
        try:
            fd = os.open(filename, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0), dir_fd=dir_fd)
            try:
                if not stat.S_ISREG(os.fstat(fd).st_mode):
                    raise StrategyFileError("Strategy target must be a regular file.")
                with os.fdopen(fd, "r", encoding="utf-8") as stream:
                    fd = -1
                    source = stream.read()
            except FileNotFoundError:
                raise StrategyFileNotFound(f"unknown strategy file {filename}") from None
            finally:
                if fd >= 0:
                    os.close(fd)
        finally:
            os.close(dir_fd)
        return {"filename": filename, "source": source,
                **self._metadata(filename, source), "hash": self._hash(source)}

    def save(self, filename: str, source: str, previous_hash: str | None = None) -> dict:
        if not isinstance(source, str) or not source.strip():
            raise StrategyFileError("Strategy source must not be empty.")
        if len(source.encode("utf-8")) > MAX_SOURCE_BYTES:
            raise StrategyFileError("Strategy source must be at most 256 KB.")
        metadata = self._metadata(filename, source)
        if metadata["error"]:
            raise StrategyFileError(f"Invalid strategy source: {metadata['error']}")
        path = self._path(filename)
        with self._lock:
            dir_fd = os.open(self.directory, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) |
                             getattr(os, "O_NOFOLLOW", 0))
            temporary = f".{filename}.{secrets.token_hex(8)}.tmp"
            try:
                try:
                    target = os.stat(filename, dir_fd=dir_fd, follow_symlinks=False)
                    exists = True
                except FileNotFoundError:
                    target, exists = None, False
                if exists:
                    if not stat.S_ISREG(target.st_mode):
                        raise StrategyFileError("Strategy target must be a regular file.")
                    fd = os.open(filename, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0), dir_fd=dir_fd)
                    try:
                        with os.fdopen(fd, "r", encoding="utf-8") as stream:
                            fd = -1
                            current_hash = self._hash(stream.read())
                    finally:
                        if fd >= 0:
                            os.close(fd)
                    if not isinstance(previous_hash, str) or previous_hash != current_hash:
                        raise StrategyFileConflict("Strategy file changed since it was loaded. Refresh before saving.")
                elif previous_hash is not None:
                    raise StrategyFileConflict("Strategy file no longer exists. Refresh before saving.")
                fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL |
                             getattr(os, "O_NOFOLLOW", 0), 0o600, dir_fd=dir_fd)
                with os.fdopen(fd, "w", encoding="utf-8", newline="") as stream:
                    stream.write(source)
                    stream.flush()
                    os.fsync(stream.fileno())
                try:
                    target = os.stat(filename, dir_fd=dir_fd, follow_symlinks=False)
                    if not stat.S_ISREG(target.st_mode):
                        raise StrategyFileError("Strategy target must be a regular file.")
                except FileNotFoundError:
                    if previous_hash is not None:
                        raise StrategyFileConflict("Strategy file no longer exists. Refresh before saving.") from None
                os.replace(temporary, filename, src_dir_fd=dir_fd, dst_dir_fd=dir_fd)
                os.fsync(dir_fd)
            finally:
                try:
                    os.unlink(temporary, dir_fd=dir_fd)
                except FileNotFoundError:
                    pass
                os.close(dir_fd)
        return self.get(filename)
