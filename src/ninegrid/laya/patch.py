"""Journaled, optimistic multi-file transactions and conflict-safe rollback."""
import hashlib
import uuid
from datetime import datetime, timezone

from ..paths import inside, relative, sha256, json_bytes, atomic_write, load_json


def commit(root, changes, kind, expected=None, details=None):
    changes = {relative(root, p): data for p, data in changes.items()}
    for name, digest in (expected or {}).items():
        path = inside(root, name)
        actual = sha256(path) if path.exists() else None
        if actual != digest:
            raise ValueError(f"Concurrent modification: {name}")
    changes = {p: data for p, data in changes.items()
               if not inside(root, p).exists() or inside(root, p).read_bytes() != data}
    if not changes:
        return None
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S") + "-" + uuid.uuid4().hex[:12]
    directory = inside(root, ".ninegrid/backups/" + run_id)
    journal = {"schemaVersion": 1, "runId": run_id, "kind": kind, "status": "pending",
               "files": [], "details": details or {}}
    for name, content in sorted(changes.items()):
        path = inside(root, name)
        exists = path.exists()
        record = {"path": name, "existed": exists, "beforeSha256": sha256(path) if exists else None,
                  "afterSha256": hashlib.sha256(content).hexdigest() if content is not None else None}
        if exists:
            atomic_write(inside(root, directory / "files" / name), path.read_bytes())
        journal["files"].append(record)
    journal_path = directory / "transaction.json"
    atomic_write(journal_path, json_bytes(journal))
    written = []
    try:
        for record in journal["files"]:
            path = inside(root, record["path"])
            actual = sha256(path) if path.exists() else None
            if actual != record["beforeSha256"]:
                raise ValueError(f"Concurrent modification: {record['path']}")
            content = changes[record["path"]]
            if content is None:
                path.unlink(missing_ok=True)
            else:
                atomic_write(path, content)
            written.append(record)
        journal["status"] = "committed"
        atomic_write(journal_path, json_bytes(journal))
    except BaseException:
        for record in reversed(written):
            path = inside(root, record["path"])
            if record["existed"]:
                atomic_write(path, (directory / "files" / record["path"]).read_bytes())
            else:
                path.unlink(missing_ok=True)
        journal["status"] = "failed_rolled_back"
        atomic_write(journal_path, json_bytes(journal))
        raise
    return run_id


def rollback(root, run_id, dry_run=False):
    if not run_id or any(c not in "0123456789Tabcdef-" for c in run_id):
        raise ValueError("Invalid run ID")
    directory = inside(root, ".ninegrid/backups/" + run_id)
    journal_path = directory / "transaction.json"
    journal = load_json(journal_path)
    if journal.get("status") == "rolled_back":
        return {"runId": run_id, "status": "already_rolled_back"}
    if journal.get("status") != "committed":
        raise ValueError("Only committed transactions can be rolled back automatically")
    changes, expected = {}, {}
    for record in journal["files"]:
        path = inside(root, record["path"])
        expected[record["path"]] = record["afterSha256"]
        actual = sha256(path) if path.exists() else None
        if actual != record["afterSha256"]:
            raise ValueError(f"Rollback conflict; file edited after this run: {record['path']}")
        data = (directory / "files" / record["path"]).read_bytes() if record["existed"] else None
        if data is not None and hashlib.sha256(data).hexdigest() != record["beforeSha256"]:
            raise ValueError("Backup hash mismatch")
        changes[record["path"]] = data
    if dry_run:
        return {"runId": run_id, "status": "dry_run", "files": list(changes)}
    journal["status"] = "rolled_back"
    changes[relative(root, journal_path)] = json_bytes(journal)
    undo = commit(root, changes, "rollback", expected, {"reverts": run_id})
    return {"runId": run_id, "status": "rolled_back", "rollbackRunId": undo}
