"""Pixel-driven candidate orchestration with CPU and real-engine fallbacks.

No asset presets: upstream repeat-band and outline analysis supplies borders;
GPU sampling decides which generated PNG is safe enough to publish.
"""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import shutil

import numpy as np
from PIL import Image

from ..candidate import Candidate
from ..scan import repeated_bands
from ..visual import VisualCandidate, choose_visual_candidate, quality_limits
from ..reconstruct import reconstruct_9slice
from ..crop import png_bytes

ALGORITHM='auto-grid-v3'
GPU_LIMITS=dict(maxMeanError=.02,maxBadPixelRatio=0,pixelErrorThreshold=1,edgeTolerancePixels=0)
CENTERS=(8,16,32,64)
BOUNDARY_INSETS=(0,1,2,4,8)
MAX_COMPRESSED_CANDIDATES=8


def image_traits(rgba):
    visible=rgba[...,3]>8
    values=rgba.astype(np.float32)
    values[...,:3]*=values[...,3:4]/255
    dx=np.max(np.abs(np.diff(values[...,:3],axis=1)),axis=2)
    dy=np.max(np.abs(np.diff(values[...,:3],axis=0)),axis=2)
    edge=float((np.mean(dx>16)+np.mean(dy>16))/2) if dx.size and dy.size else 0
    colors=len(np.unique((rgba[visible,:3]//16),axis=0)) if visible.any() else 0
    h,w=rgba.shape[:2]
    return dict(visible=bool(visible.any()),highFrequency=edge,quantizedColors=colors,
                detailedSquare=bool(.75<=w/h<=1.33 and colors>128 and edge>.12))


def useful_bands(bands,size,visible,axis):
    result=[]
    span=visible.any(axis=0 if axis==1 else 1)
    occupied=np.flatnonzero(span)
    for a,b in sorted(bands,key=lambda band:(-(band[1]-band[0]),band[0])):
        if b-a<4 or not occupied.size or a<occupied[0] or b>occupied[-1]+1:continue
        # A tiny repeated outside border is a fixed cap, not a safe stretch core.
        if (a==0 or b==size) and b-a<size*.15 and (a,b)!=(0,size):continue
        result.append((a,b))
    return result[:2]


def pixel_candidates(rgba,policy='visual'):
    """Return compressed and original-size options derived only from pixels."""
    h,w=rgba.shape[:2];traits=image_traits(rgba)
    if np.all(rgba==rgba[0,0]):
        if w==1 and h==1:return [],dict(traits,reason='minimal_constant_texture_no_grid_needed')
        candidate=Candidate((w,h),(0,w) if w>1 else None,(0,h) if h>1 else None)
        fields=candidate.fields()
        return [dict(fields,candidateCoding='strict',mode=policy,geometryOnly=False,
                     decision='constant_pixels',cpuExact=True)],traits
    if traits['detailedSquare']:return [],dict(traits,reason='detailed_square_image_no_stretch')
    columns,rows=repeated_bands(rgba);visible=rgba[...,3]>8
    columns=useful_bands(columns,w,visible,1);rows=useful_bands(rows,h,visible,0)
    pairs=[]
    for column,row in [(columns[0] if columns else None,rows[0] if rows else None),
                       (columns[0] if columns else None,None),(None,rows[0] if rows else None)]:
        if (column or row) and (column,row) not in pairs:pairs.append((column,row))
    candidates=[]
    # Moving the stretch boundary further inside an exact repeat band keeps
    # identical fixed pixels around a hard alpha/color transition. Longer center
    # patches alone cannot protect minification at that boundary.
    def boundary_jump(band,axis):
        if not band:return 0
        a,b=band;values=np.moveaxis(rgba.astype(np.int16),axis,0)
        return max((int(np.abs(values[index]-values[neighbor]).max())
                    for index,neighbor in ((a,a-1),(b-1,b)) if 0<=neighbor<len(values)),default=0)
    for center in CENTERS:
        for inset in BOUNDARY_INSETS:
            for column,row in pairs:
                jump=max(boundary_jump(column,1),boundary_jump(row,0))
                if inset and jump<=1:continue
                inner=[(band[0]+inset,band[1]-inset) if band else None for band in (column,row)]
                if any(band and band[1]-band[0]<4 for band in inner):continue
                bands=[(*band,(band[0]+band[1])//2) if band else None for band in inner]
                candidate=VisualCandidate((w,h),*bands,center_size=center)
                fields=candidate.fields();cropped=candidate.crop(rgba)
                if fields['outputSize']==[w,h]:continue
                if not np.array_equal(reconstruct_9slice(cropped,candidate.margins,(w,h)),rgba):continue
                record=dict(fields,candidateCoding='guarded',mode=policy,geometryOnly=False,
                            decision='exact_band_guarded_center',cpuExact=True,boundaryInset=inset,boundaryChannelJump=jump)
                candidates.append(record)
                # A transparent repeated band has no visible pattern to stretch.
                # Native tiled grids avoid extreme UV minification at hard cuts.
                # This proves only the identified axis; the orthogonal extent is
                # retained, and real height/pose checks still run independently.
                if column or row:
                    band=column or row
                    strip=rgba[:,band[0]:band[1]] if column else rgba[band[0]:band[1]]
                    if (column and row) or not strip[...,3].any():
                        tiled=deepcopy(record);tiled['sizeGrid']['repeat']=1
                        tiled['layaSizeGrid']=','.join(str(tiled['sizeGrid'][k]) for k in ('top','right','bottom','left','repeat'))
                        tiled.update(tileEquivalentAxis='both' if column and row else ('horizontal' if column else 'vertical'),
                                     decision='exact_repeat_bands_native_tile' if column and row else 'exact_transparent_band_native_tile')
                        from .codecs import reconstruct_candidate
                        if np.array_equal(reconstruct_candidate(cropped,tiled['sizeGrid'],(w,h)),rgba):candidates.append(tiled)
    geometry=[]
    for column,row in pairs:
        fields=Candidate((w,h),column,row).fields()
        if any(fields['sizeGrid'][key] for key in ('top','right','bottom','left')):
            geometry.append(dict(fields,outputSize=[w,h],pixelAreaSavedRatio=0,geometryOnly=True,
                                 mode=policy,decision='exact_band_original_png',cpuExact=True))
    # For non-uniform gradients/rounded panels, reuse upstream outline analysis
    # only to propose borders. Its approximate crop is NEVER applied here.
    for axis in ('horizontal','vertical','auto'):
        inferred,analysis=choose_visual_candidate(rgba,min_saving=0,limits=quality_limits(),center_size=8,axis=axis)
        if inferred:
            fields=inferred.fields()
            if any(fields['sizeGrid'][key] for key in ('top','right','bottom','left')):
                geometry.append(dict(fields,outputSize=[w,h],pixelAreaSavedRatio=0,geometryOnly=True,
                    mode=policy,decision='outline_inferred_original_png',cpuExact=True,analysisOnlyApproximateCrop=analysis))
    if policy=='visual':
        from .linear import linear_candidates
        candidates.extend(linear_candidates(rgba))
    candidates.extend(geometry)
    unique=[];seen=set()
    for fields in candidates:
        key=(fields['geometryOnly'],tuple(fields['outputSize']),fields['layaSizeGrid'])
        if key not in seen:seen.add(key);unique.append(fields)
    compressed=[c for c in unique if not c['geometryOnly']]
    fallback=[c for c in unique if c['geometryOnly']]
    traits.update(compressedCandidatesGenerated=len(compressed),compressedCandidatesRetained=min(len(compressed),MAX_COMPRESSED_CANDIDATES))
    return compressed[:MAX_COMPRESSED_CANDIDATES]+fallback,traits
