"""
utils/photos.py — Safe kid-photo handling.

Security: the uploaded bytes are never stored or served as-is. Pillow decodes
the image, we fix its rotation, crop it square, resize it and RE-ENCODE it as a
fresh JPEG with a random name. That strips EXIF/GPS data and any hidden payload
(polyglot files, SVG scripts, etc.). Only JPEG / PNG / WEBP are accepted.
Files live in instance/photos (NOT in /static) and are served only by an
authenticated route that checks the viewer may see that kid.
"""
import io
import os
import re
import secrets
from flask import current_app
from PIL import Image, ImageOps, UnidentifiedImageError

Image.MAX_IMAGE_PIXELS = 40_000_000          # blocks decompression bombs
ALLOWED_FORMATS = {"JPEG", "PNG", "WEBP"}
FILENAME_RE = re.compile(r"^[0-9a-f]{32}\.jpg$")


def photos_dir() -> str:
    path = os.path.join(current_app.instance_path, "photos")
    os.makedirs(path, exist_ok=True)
    return path


def is_valid_filename(name: str | None) -> bool:
    return bool(name and FILENAME_RE.match(name))


def save_photo(file_storage) -> str:
    """Validate + re-encode an uploaded image. Returns the stored filename."""
    size = current_app.config.get("PHOTO_SIZE_PX", 320)
    limit = current_app.config["MAX_CONTENT_LENGTH"]
    data = file_storage.read(limit + 1)
    if not data:
        raise ValueError("الملف فارغ")
    if len(data) > limit:
        raise ValueError("الصورة كبيرة جداً")
    try:
        img = Image.open(io.BytesIO(data))
        if img.format not in ALLOWED_FORMATS:
            raise ValueError("نوع الصورة غير مدعوم. استخدم JPG أو PNG أو WEBP.")
        img = ImageOps.exif_transpose(img)           # phone photos: fix rotation
        img = img.convert("RGBA")
        flat = Image.new("RGB", img.size, "white")   # transparent PNG -> white
        flat.paste(img, mask=img.split()[3])
        # square crop, biased slightly upward so faces are kept
        flat = ImageOps.fit(flat, (size, size), Image.LANCZOS, centering=(0.5, 0.35))
    except ValueError:
        raise
    except (UnidentifiedImageError, Image.DecompressionBombError, OSError, SyntaxError):
        raise ValueError("تعذّر قراءة الصورة. تأكد أنها ملف صورة سليم.")

    name = secrets.token_hex(16) + ".jpg"
    flat.save(os.path.join(photos_dir(), name), "JPEG", quality=85, optimize=True)
    return name


def delete_photo(filename: str | None) -> None:
    if not is_valid_filename(filename):
        return
    try:
        os.remove(os.path.join(photos_dir(), filename))
    except OSError:
        pass
