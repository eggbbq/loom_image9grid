"""Add proven stretch borders while preserving the complete source PNG bytes."""
from dataclasses import dataclass


@dataclass(frozen=True)
class GeometryOnlyGrid:
    size: tuple
    margins: dict

    def fields(self):
        return dict(sourceSize=list(self.size),outputSize=list(self.size),sizeGrid=self.margins,
                    layaSizeGrid=','.join(str(self.margins[k]) for k in ('top','right','bottom','left','repeat')),geometryOnly=True)

    def crop(self, rgba):
        if tuple(rgba.shape[1::-1]) != self.size:
            raise ValueError('原图尺寸与仅设置边界的证明不一致')
        return rgba


@dataclass(frozen=True)
class TiledGridCandidate:
    base: object

    @property
    def margins(self):return dict(self.base.margins,repeat=1)

    def crop(self,rgba):return self.base.crop(rgba)

    def fields(self):
        fields=self.base.fields();fields['sizeGrid']=self.margins
        fields['layaSizeGrid']=','.join(str(self.margins[k]) for k in ('top','right','bottom','left','repeat'))
        return fields


def reconstruct_candidate(rgba,margins,size):
    """CPU decode for the native stretch and tile grid modes at source size."""
    if margins.get('repeat',0)==0:
        from ..reconstruct import reconstruct_9slice
        return reconstruct_9slice(rgba,margins,size)
    import numpy as np
    def indices(source,target,before,after):
        count=source-before-after;middle=target-before-after
        if count<1 or middle<1:raise ValueError('Tile CPU proof requires a positive center')
        return np.concatenate((np.arange(before),before+np.arange(middle)%count,np.arange(source-after,source)))
    xs=indices(rgba.shape[1],size[0],margins['left'],margins['right'])
    ys=indices(rgba.shape[0],size[1],margins['top'],margins['bottom'])
    return rgba[ys[:,None],xs[None,:]]


def candidate_for_record(item):
    if item.get('candidateCoding')=='linear':
        from .linear import LinearCenterCandidate
        return LinearCenterCandidate(tuple(item['sourceSize']),
            tuple(item['columnBand']) if item.get('columnBand') else None,
            tuple(item['rowBand']) if item.get('rowBand') else None,tuple(item['linearCenterSize']))
    if not item.get('geometryOnly'):
        from ..jobs import _legacy_candidate_from_fields as candidate_from_fields
        if item.get('candidateCoding') in {'strict','guarded'}:
            candidate=candidate_from_fields(dict(item,mode='visual' if item['candidateCoding']=='guarded' else 'strict'))
            return TiledGridCandidate(candidate) if item['sizeGrid']['repeat']==1 else candidate
        return candidate_from_fields(item)
    size=item['sourceSize'];grid=item['sizeGrid']
    values=[grid[k] for k in ('top','right','bottom','left','repeat')]
    if (item['outputSize'] != size or len(size)!=2 or any(type(v) is not int or v<0 for v in values)
            or values[4]!=0 or not any(values[:4]) or values[1]+values[3]>=size[0]
            or values[0]+values[2]>=size[1]):
        raise ValueError('无效的保留原图九宫格边界')
    return GeometryOnlyGrid(tuple(size),dict(grid))



decode_candidate = candidate_for_record
