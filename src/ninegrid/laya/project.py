from pathlib import Path
import hashlib
import json
from ..paths import inside, load_json, relative


class LayaProject:
    SUPPORTED = {"3.4.1", "3.4.2"}

    def __init__(self, root, scene_roots=None):
        self.root = Path(root).resolve()
        if not self.root.is_dir():
            raise ValueError(f"Missing project: {root}")
        configs = sorted(self.root.glob("*.laya"))
        if not configs:
            raise ValueError("No .laya project file")
        versions = {load_json(inside(self.root, p)).get("version") for p in configs}
        if len(versions) != 1 or not versions.issubset(self.SUPPORTED):
            raise ValueError(f"Unsupported LayaAir version: {versions}; tested: 3.4.1 / 3.4.2")
        self.version = next(iter(versions))
        self.assets = inside(self.root, "assets")
        if not self.assets.is_dir():
            raise ValueError("No project assets directory")
        roots = [inside(self.root, p) for p in (scene_roots or ["assets"])]
        self.scenes = {}
        self.scene_hashes = {}
        for directory in roots:
            files = [directory] if directory.is_file() else sorted(directory.rglob("*"))
            for path in files:
                if path.suffix.lower() not in (".ls", ".lh"):
                    continue
                path = inside(self.root, path)
                content = path.read_bytes()
                data = json.loads(content)
                if not isinstance(data, dict) or data.get("_$ver") != 1 or not isinstance(data.get("_$type"), str):
                    raise ValueError(f"Unsupported scene serialization: {relative(self.root, path)}")
                self.scenes[relative(self.root, path)] = data
                self.scene_hashes[relative(self.root, path)] = hashlib.sha256(content).hexdigest()
        if not self.scenes:
            raise ValueError("No parseable .ls/.lh scenes; project integration unavailable")
        self.uuids = {}
        for meta in sorted(self.assets.rglob("*.png.meta")):
            meta = inside(self.root, meta)
            image = meta.with_suffix("")
            if not image.is_file():
                continue
            value = load_json(meta).get("uuid")
            if not isinstance(value, str):
                raise ValueError(f"Invalid texture UUID: {meta}")
            if value in self.uuids and self.uuids[value] != image:
                raise ValueError(f"Duplicate texture UUID: {value}")
            self.uuids[value] = image

    def resolve_reference(self, value):
        if not isinstance(value, str):
            return None
        if value.startswith("res://"):
            return self.uuids.get(value[6:])
        if ":" in value or "?" in value or "#" in value:
            return None
        path = inside(self.root, value if value.startswith("assets/") else "assets/" + value)
        return path if path.is_file() else None
