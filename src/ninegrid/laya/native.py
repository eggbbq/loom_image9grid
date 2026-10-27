"""Scoped safety policy for the native UI components emitted by this exporter."""
import math



def geometry_relations(relations):
    """Native size/position relations do not change a texture's content."""
    return isinstance(relations, list) and all(
        isinstance(relation, dict) and relation.get('_$type') == 'Relation'
        and isinstance(relation.get('target'), dict) and isinstance(relation['target'].get('_$ref'), str)
        and isinstance(relation.get('data'), list) and len(relation['data']) % 2 == 0
        and all(code in {1, 2, 3, 6, 9, 10, 13, 16} and weight == 0
                for code, weight in zip(relation['data'][::2], relation['data'][1::2]))
        for relation in relations)


def references(project,allowed_component_types=()):
    from ninegrid.laya.references import Reference
    result = []
    supported = {'Scene', 'Sprite', 'Box', 'Panel', 'Image', 'GWidget', 'GImage',
                 'GBox', 'GPanel', 'GList', 'GButton', 'GLabel', 'GProgressBar',
                 'GSlider', 'GTextField', 'GTextInput', 'GLoader'}
    for file, data in sorted(project.scenes.items()):
        dynamic_parts = set()
        def collect(node):
            if node.get('_$type') in ('GProgressBar', 'GSlider'):
                for key in ('hBar', 'vBar', '_hBar', '_vBar'):
                    value = node.get(key)
                    if isinstance(value, dict) and value.get('_$ref'):
                        dynamic_parts.add(value['_$ref'])
            for child in node.get('_$child', []):
                collect(child)
        collect(data)
        ids = set()
        def visit(node, pointer=(), inherited=False):
            uid = node.get('_$id')
            if not isinstance(uid, str) or uid in ids:
                raise ValueError(f'Missing/duplicate stable node ID: {file} {pointer}')
            ids.add(uid)
            kind = node.get('_$type')
            # Native progress/slider bars resize a plain GImage's geometry;
            # its fixed texture is precisely what sizeGrid is intended for.
            # Keep custom meshes and non-image fill implementations guarded.
            unsafe_fill = uid in dynamic_parts and (kind != 'GImage' or bool(node.get('mesh')))
            conflict = inherited or kind not in supported or unsafe_fill
            conflict |= any(node.get(k) for k in ('_$prefab', 'gears', 'controllers', 'animations', 'states'))
            conflict |= bool(node.get('relations')) and not geometry_relations(node['relations'])
            conflict |= any(comp.get('_$type') not in allowed_component_types for comp in node.get('_$comp', []))
            layout = node.get('layout')
            if layout:
                conflict |= (kind not in ('GBox', 'GPanel', 'GList') or
                             layout.get('stretchX', 0) != 0 or layout.get('stretchY', 0) != 0)
            for field in ('skin', 'src'):
                if field in node:
                    source = project.resolve_reference(node[field])
                    if source is not None and source.suffix.lower() == '.png':
                        result.append(Reference(file, pointer, node, source, field, bool(conflict)))
            transformed = any(node.get(k, 1) != 1 for k in ('scaleX', 'scaleY')) or any(
                node.get(k, 0) != 0 for k in ('rotation', 'skewX', 'skewY'))
            # A half-pixel anchor coordinate can still have integer visual
            # edges (odd-sized buttons with a centered scaling pivot).
            for axis, dimension in (('X', 'width'), ('Y', 'height')):
                pivot = node.get('anchor'+axis, 0) * node.get(dimension, 0) if 'anchor'+axis in node else node.get('pivot'+axis, 0)
                translation = node.get(axis.lower(), 0) - pivot
                transformed |= not math.isclose(translation, round(translation), abs_tol=1e-6)
            transformed |= any(node.get(k) for k in ('filters', 'mask', 'material', 'dataSource'))
            transformed |= node.get('cacheAs') not in (None, 'none')
            children = node.get('_$child', [])
            if not isinstance(children, list):
                raise ValueError(f'Invalid child array: {file}')
            for i, child in enumerate(children):
                visit(child, (*pointer, '_$child', i), bool(conflict or transformed))
        visit(data)
    return result
