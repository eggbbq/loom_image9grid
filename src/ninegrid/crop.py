from io import BytesIO
from PIL import Image


def png_bytes(rgba):
    stream = BytesIO()
    Image.fromarray(rgba).save(stream, format="PNG")
    return stream.getvalue()
