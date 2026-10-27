"""Effective native LayaAir Sprite2D importer sampling, used by real probes."""

def effective_sampling(metadata):
    source=metadata.get('importer',{})
    if source.get('textureType')==2:
        # The IDE's texture importer normalizes Sprite2D to these values.
        # Loading a raw PNG directly otherwise leaves WebGL's anisotropy at 4.
        return dict(anisoLevel=1,premultiplyAlpha=True,mipmap=False,sRGB=True,
                    rule='LayaAir-3.4.2-Sprite2D-importer')
    return dict(anisoLevel=source.get('anisoLevel',4),
                premultiplyAlpha=source.get('premultiplyAlpha',True),
                mipmap=source.get('generateMipmap',False),sRGB=source.get('sRGB',True),
                rule='source-importer')
