import copy
import uuid

from ..paths import inside, load_json
from .project import LayaProject
from .references import references, unsafe_reason


TEXTURE_DEFAULTS = {"textureType": 2, "sRGB": True, "generateMipmap": False, "npot": 0,
                    "maxSize": 0, "usePng8": False, "filterMode": 1, "wrapMode": 0, "readWrite": False}


def source_importer_reason(meta):
    settings = meta.get("importer", {})
    known = set(TEXTURE_DEFAULTS) | {"sizeGrid"}
    if set(settings) - known:
        return "unverified_texture_importer_settings"
    if settings.get("sizeGrid"):
        return "existing_texture_size_grid"
    if settings.get("textureType", 2) != 2:
        return "not_sprite_texture"
    if (settings.get("generateMipmap") or settings.get("readWrite") or settings.get("wrapMode", 0) != 0
            or type(settings.get("filterMode", 1)) is not int
            or settings.get("filterMode", 1) not in (0, 1) or settings.get("usePng8")
            or settings.get("maxSize", 0) or settings.get("npot", 0)):
        return "special_texture_sampling"
    if any(settings.get(key) for key in ("rotated", "frames", "animation", "atlas", "textureSheet")):
        return "atlas_or_animation_texture"
    return None


class LayaProjectAdapter:
    def __init__(self, root, scene_roots=None, *, reference_policy=None, allowed_component_types=None):
        self.project = LayaProject(root, scene_roots)
        from .policy import current_reference_policy
        policy,components=current_reference_policy()
        policy=reference_policy or policy
        components=tuple(allowed_component_types) if allowed_component_types is not None else components
        if policy=='native':
            from .native import references as native_references
            self.references=native_references(self.project,components)
        elif policy=='legacy':self.references=references(self.project)
        else:raise ValueError(f'Unknown reference policy: {policy}')

    def for_source(self, source):
        return [ref for ref in self.references if ref.source == source]

    def create_meta(self, item):
        root = self.project.root
        original = inside(root, item["source"] + ".meta")
        meta = copy.deepcopy(load_json(original)) if original.exists() else {"importer": {"textureType": 2}}
        meta["uuid"] = str(uuid.uuid5(uuid.NAMESPACE_URL, "ninegrid:3.4.1:" + item["source"]))
        # Proven by the installed IDE's native UI PNG metadata and runtime probe.
        meta.setdefault("importer", {})["sizeGrid"] = [item["sizeGrid"][key] for key in
                                                        ("top", "right", "bottom", "left", "repeat")]
        meta["importer"]["generateMipmap"] = False
        meta["importer"]["filterMode"] = meta["importer"].get("filterMode", 1)
        return meta

    def patch(self, item):
        meta = load_json(inside(self.project.root, item["output"] + ".meta"))
        changes = []
        for ref in self.for_source(inside(self.project.root, item["source"])):
            reason = unsafe_reason(ref, item["sourceSize"])
            record = ref.report(reason)
            if reason is None:
                old = ref.node[ref.field]
                value = "res://" + meta["uuid"] if old.startswith("res://") else (
                    item["output"] if old.startswith("assets/") else item["output"][len("assets/"):])
                ref.node[ref.field] = value
                if ref.field == "skin":
                    ref.node["sizeGrid"] = item["layaSizeGrid"]
                record.update(newSkin=value, newSizeGrid=item["layaSizeGrid"],
                              gridLocation="node.sizeGrid" if ref.field == "skin" else "texture.importer.sizeGrid")
            changes.append(record)
        return changes
