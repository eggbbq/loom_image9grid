import hashlib
import json
import os
from pathlib import Path
import tempfile


def inside(root, value):
    root = Path(root).resolve()
    path = Path(value)
    if ".." in path.parts or "\\" in str(value):
        raise ValueError(f"Unsafe project path: {value}")
    path = path if path.is_absolute() else root / path
    resolved = path.resolve()
    if not resolved.is_relative_to(root):
        raise ValueError(f"Path escapes project: {value}")
    # Do not write through even an in-project symlink: aliases break transactions.
    current = root
    for part in path.relative_to(root).parts:
        current /= part
        if current.is_symlink():
            raise ValueError(f"Symbolic links are unsupported: {value}")
    return resolved


def relative(root, path):
    return inside(root, path).relative_to(Path(root).resolve()).as_posix()


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def json_bytes(data):
    return (json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode("utf-8")


def atomic_write(path, content):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=".ninegrid-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        Path(name).unlink(missing_ok=True)


def load_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))
