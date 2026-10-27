"""Pixel-derived smooth-center candidates for bounded visual mode only."""
from dataclasses import dataclass
import numpy as np
from PIL import Image

@dataclass(frozen=True)
class LinearCenterCandidate:
    size: tuple
    column_band: tuple | None
    row_band: tuple | None
    center_size: tuple

    @property
    def margins(self):
        w,h=self.size;c,r=self.column_band,self.row_band
        return dict(top=r[0] if r else 0,right=w-c[1] if c else 0,
                    bottom=h-r[1] if r else 0,left=c[0] if c else 0,repeat=0)

    def crop(self,rgba):
        result=rgba
        if self.column_band:
            a,b=self.column_band
            patch=np.asarray(Image.fromarray(result[:,a:b]).resize((self.center_size[0],result.shape[0]),Image.Resampling.BILINEAR))
            result=np.concatenate((result[:,:a],patch,result[:,b:]),axis=1)
        if self.row_band:
            a,b=self.row_band
            patch=np.asarray(Image.fromarray(result[a:b]).resize((result.shape[1],self.center_size[1]),Image.Resampling.BILINEAR))
            result=np.concatenate((result[:a],patch,result[b:]),axis=0)
        return result

    def restore(self,rgba):
        result=rgba;grid=self.margins
        if self.column_band:
            a,b=self.column_band;end=result.shape[1]-grid['right']
            patch=np.asarray(Image.fromarray(result[:,a:end]).resize((b-a,result.shape[0]),Image.Resampling.BILINEAR))
            result=np.concatenate((result[:,:a],patch,result[:,end:]),axis=1)
        if self.row_band:
            a,b=self.row_band;end=result.shape[0]-grid['bottom']
            patch=np.asarray(Image.fromarray(result[a:end]).resize((result.shape[1],b-a),Image.Resampling.BILINEAR))
            result=np.concatenate((result[:a],patch,result[end:]),axis=0)
        return result

    def fields(self):
        w,h=self.size;grid=self.margins
        out=[w-(self.column_band[1]-self.column_band[0])+self.center_size[0] if self.column_band else w,
             h-(self.row_band[1]-self.row_band[0])+self.center_size[1] if self.row_band else h]
        return dict(sourceSize=[w,h],outputSize=out,sizeGrid=grid,
                    layaSizeGrid=','.join(str(grid[k]) for k in ('top','right','bottom','left','repeat')),
                    columnBand=list(self.column_band) if self.column_band else None,
                    rowBand=list(self.row_band) if self.row_band else None,linearCenterSize=list(self.center_size),
                    pixelAreaSavedRatio=1-out[0]*out[1]/(w*h),mode='visual',candidateCoding='linear',geometryOnly=False,
                    decision='bounded_linear_center_resample',cpuProofModel='Pillow-RGBA-bilinear-roundtrip')


def smooth_bands(rgba,axis):
    values=rgba.astype(np.float32);values[...,:3]*=values[...,3:4]/255
    moved=np.moveaxis(values,axis,0)
    good=np.max(np.abs(np.diff(moved,axis=0)),axis=(1,2))<=1
    transitions=np.flatnonzero(np.diff(np.r_[False,good,False]))
    candidates=[]
    for a,b in transitions.reshape(-1,2):
        b+=1
        if b-a<16:continue
        strip=moved[a:b]
        if not strip[...,3].any() or np.all(strip==strip[:1]):continue
        candidates.append((int(a),int(b)))
    return sorted(candidates,key=lambda pair:(-(pair[1]-pair[0]),pair[0]))[:2]


def linear_candidates(rgba):
    h,w=rgba.shape[:2];columns=smooth_bands(rgba,1);rows=smooth_bands(rgba,0)
    pairs=[]
    for c,r in [(columns[0] if columns else None,rows[0] if rows else None),
                *[(c,None) for c in columns],*[(None,r) for r in rows]]:
        if (c or r) and (c,r) not in pairs:pairs.append((c,r))
    result=[];choices=[]
    for c,r in pairs:
        for center in (8,16,32,64):
            if (c and c[1]-c[0]<=center) or (r and r[1]-r[0]<=center):continue
            choices.append((c,r,(center if c else w,center if r else h),'retained_center'))
        # Derive a compact texture with exactly representable power-of-two UVs;
        # this is only a candidate, never an exemption from error validation.
        target_w=1<<((w-1).bit_length()-1) if w>1 else 1
        target_h=1<<((h-1).bit_length()-1) if h>1 else 1
        cw=target_w-(w-(c[1]-c[0])) if c else w
        ch=target_h-(h-(r[1]-r[0])) if r else h
        if (not c or 0<cw<c[1]-c[0]) and (not r or 0<ch<r[1]-r[0]):
            choices.append((c,r,(cw,ch),'source_size_power_of_two'))
    seen=set()
    for c,r,center,reason in choices:
        key=(c,r,center)
        if key in seen:continue
        seen.add(key)
        candidate=LinearCenterCandidate((w,h),c,r,center)
        cropped=candidate.crop(rgba);restored=candidate.restore(cropped)
        difference=np.abs(restored.astype(np.int16)-rgba.astype(np.int16))
        maximum=int(difference.max());mean=float(difference.mean())
        if maximum>1 or mean>.02:continue
        result.append(dict(candidate.fields(),cpuExact=maximum==0,cpuMaxRawChannelError=maximum,
                           cpuMeanError=mean,boundaryChannelJump=1,centerSelection=reason))
    return sorted(result,key=lambda c:(-c['pixelAreaSavedRatio'],c['cpuMeanError']))
