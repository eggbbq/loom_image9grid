from dataclasses import dataclass


@dataclass
class Reference:
    file: str
    pointer: tuple
    node: dict
    source: object
    field: str
    inherited_conflict: bool

    @property
    def id(self):
        return self.node["_$id"]

    def report(self, reason=None):
        return {"file": self.file, "nodeId": self.id, "pointer": list(self.pointer),
                "type": self.node.get("_$type"), "property": self.field,
                "oldSkin": self.node[self.field], "oldSizeGrid": self.node.get("sizeGrid"),
                "eligible": reason is None, "reason": reason}


def non_pixel_transform(node):
    if any(node.get(k, 1) != 1 for k in ("scaleX", "scaleY")) or any(
            node.get(k, 0) != 0 for k in ("rotation", "skewX", "skewY")):
        return True
    for key in ("x", "y", "pivotX", "pivotY"):
        value = node.get(key, 0)
        if not isinstance(value, (int, float)) or isinstance(value, bool) or value % 1:
            return True
    for key, size in (("anchorX", "width"), ("anchorY", "height")):
        if node.get(key):
            value, dimension = node[key], node.get(size)
            if not isinstance(value, (int, float)) or not isinstance(dimension, (int, float)) or (value*dimension) % 1:
                return True
    return False


def references(project):
    result = []
    conflicts = ("_$comp", "_$prefab", "gears", "controllers", "relations", "layout", "animations", "states")
    static_types = {"Scene", "GWidget", "Sprite", "Box", "GBox", "GPanel", "Panel", "Image", "GImage"}
    for file, data in sorted(project.scenes.items()):
        ids = set()

        def visit(node, pointer=(), inherited=False):
            if not isinstance(node, dict) or not isinstance(node.get("_$id"), str) or node["_$id"] in ids:
                raise ValueError(f"Missing/duplicate stable node ID: {file} {pointer}")
            ids.add(node["_$id"])
            conflict = inherited or node.get("_$type") not in static_types or any(node.get(key) for key in conflicts)
            for field in ("skin", "src"):
                if field in node:
                    source = project.resolve_reference(node[field])
                    if source is not None and source.suffix.lower() == ".png":
                        result.append(Reference(file, pointer, node, source, field, conflict))
            children = node.get("_$child", [])
            if not isinstance(children, list):
                raise ValueError(f"Invalid child array: {file}")
            inherited_rendering = non_pixel_transform(node) or any(node.get(k) for k in
                ("filters", "mask", "material", "cacheAs", "dataSource"))
            for i, child in enumerate(children):
                visit(child, (*pointer, "_$child", i), conflict or inherited_rendering)

        visit(data)
    return result


def unsafe_reason(ref, source_size):
    node = ref.node
    kind = node.get("_$type")
    if (kind, ref.field) not in (("Image", "skin"), ("GImage", "src")):
        return "unsupported_node_type"
    if ref.inherited_conflict:
        return "dynamic_or_inherited_configuration"
    if node.get("sizeGrid"):
        return "existing_size_grid"
    if node.get("useSourceSize") or kind == "GImage" and node.get("autoSize", True):
        return "automatic_source_size"
    if (any(isinstance(node.get(key), bool) for key in ("width", "height")) or
            [node.get("width"), node.get("height")] != source_size):
        return "display_size_differs_from_png"
    if any(node.get(key) not in (None, 0) for key in ("rotation", "skewX", "skewY")) or any(
            node.get(key, 1) != 1 for key in ("scaleX", "scaleY")):
        return "transformed_reference"
    if any(node.get(key) is not None for key in ("left", "right", "top", "bottom", "centerX", "centerY")):
        return "layout_controlled_size"
    if any(node.get(key) for key in ("dataSource", "source", "texture", "mesh", "filters", "material",
                                    "mask", "graphics", "cacheAs", "stateNum", "clipX", "clipY", "skinBaseUrl",
                                    "dynamicSkin", "skinBinding", "skinExpression")):
        return "special_rendering_or_dynamic_skin"
    if any(not isinstance(node.get(key, 0), (int, float)) or node.get(key, 0) % 1 for key in ("x", "y")):
        return "fractional_position"
    if non_pixel_transform(node):
        return "non_pixel_aligned_transform"
    if node.get("blendMode") not in (None, "normal", "source-over"):
        return "unverified_blend_mode"
    return None
