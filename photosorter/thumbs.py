"""Preview images for the browser, cached on disk.

Order of attempts:
1. a JPEG/HEIC/PNG/TIFF (or the JPEG of a RAW+JPEG pair) is resized with
   macOS `sips` (built in), or Pillow if installed;
2. a RAW file: the largest preview embedded in it is extracted with ExifTool,
   given the RAW's orientation, then resized;
3. otherwise a JPEG original is sent as is, or a grey placeholder.
Original files are only read, never changed.
"""

from __future__ import annotations

import base64
import hashlib
import os
import shutil
import subprocess
import tempfile
import threading
from pathlib import Path

from .exiftool import ExifTool
from .scan import Photo, kind_of

SIPS = shutil.which("sips")
try:  # optional, used only when sips is missing (Windows, Linux, NAS)
    from PIL import Image, ImageOps  # type: ignore
    try:
        import pillow_heif  # type: ignore
        pillow_heif.register_heif_opener()
    except Exception:
        pass
except Exception:  # pragma: no cover
    Image = None

PLACEHOLDER = (
    b'<svg xmlns="http://www.w3.org/2000/svg" width="300" height="200" viewBox="0 0 300 200">'
    b'<rect width="300" height="200" fill="#2b3036"/>'
    b'<text x="150" y="108" fill="#8b929c" font-family="sans-serif" font-size="16" text-anchor="middle">'
    b'No preview</text></svg>'
)

_sem = threading.Semaphore(3)


class Thumbnailer:
    def __init__(self, et: ExifTool, cache_dir: Path) -> None:
        self.et = et
        self.cache = cache_dir
        self.cache.mkdir(parents=True, exist_ok=True)

    def _key(self, ph: Photo, size: int) -> Path:
        try:
            st = os.stat(ph.primary)
            sig = f"{ph.primary}|{st.st_size}|{st.st_mtime_ns}|{size}"
        except OSError:
            sig = f"{ph.primary}|{size}"
        return self.cache / (hashlib.sha1(sig.encode()).hexdigest() + ".jpg")

    def get(self, ph: Photo, size: int = 480) -> tuple[bytes, str]:
        out = self._key(ph, size)
        if out.exists() and out.stat().st_size > 0:
            return out.read_bytes(), "image/jpeg"
        with _sem:
            if not out.exists():
                try:
                    self._make(ph, size, out)
                except Exception:
                    pass
        if out.exists() and out.stat().st_size > 0:
            return out.read_bytes(), "image/jpeg"
        # Last resort: a plain JPEG can be shown by the browser directly.
        for f in [ph.primary, *ph.companions]:
            if kind_of(os.path.splitext(f)[1]) == "jpeg":
                with open(f, "rb") as fh:
                    return fh.read(), "image/jpeg"
        return PLACEHOLDER, "image/svg+xml"

    def _make(self, ph: Photo, size: int, out: Path) -> None:
        files = [ph.primary, *ph.companions]
        viewable = [f for f in files if kind_of(os.path.splitext(f)[1]) in ("jpeg", "heic", "other")]
        tmp_dir = Path(tempfile.mkdtemp(prefix="ps-thumb-"))
        try:
            if viewable:
                src = viewable[0]
                if self._resize(src, out, size):
                    return
            src = ph.primary
            extracted = tmp_dir / "preview.jpg"
            if self._extract_preview(src, extracted):
                if ph.orientation not in (0, 1):
                    self.et.execute("-overwrite_original", f"-Orientation#={ph.orientation}", str(extracted))
                if not self._resize(str(extracted), out, size):
                    shutil.copyfile(extracted, out)
                return
            # macOS can render many RAW formats directly.
            self._resize(src, out, size)
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)

    def _resize(self, src: str, out: Path, size: int) -> bool:
        tmp = out.with_suffix(".part.jpg")
        if SIPS:
            r = subprocess.run(
                [SIPS, "-s", "format", "jpeg", "-s", "formatOptions", "75", "-Z", str(size), src, "--out", str(tmp)],
                capture_output=True, timeout=60,
            )
            if r.returncode == 0 and tmp.exists() and tmp.stat().st_size > 0:
                os.replace(tmp, out)
                return True
        if Image is not None:
            try:
                with Image.open(src) as im:
                    im = ImageOps.exif_transpose(im)
                    im.thumbnail((size, size))
                    im.convert("RGB").save(tmp, "JPEG", quality=75)
                os.replace(tmp, out)
                return True
            except Exception:
                pass
        if tmp.exists():
            tmp.unlink()
        return False

    def _extract_preview(self, src: str, dest: Path) -> bool:
        out = self.et.execute("-json", "-b", "-PreviewImage", "-JpgFromRaw", "-OtherImage", "-ThumbnailImage", src)
        import json
        try:
            rec = json.loads(out.decode("utf-8", "replace") or "[{}]")[0]
        except (ValueError, IndexError):
            return False
        best = b""
        for tag in ("JpgFromRaw", "PreviewImage", "OtherImage", "ThumbnailImage"):
            v = rec.get(tag)
            if isinstance(v, str) and v.startswith("base64:"):
                data = base64.b64decode(v[7:])
                if data[:2] == b"\xff\xd8" and len(data) > len(best):
                    best = data
        if not best:
            return False
        dest.write_bytes(best)
        return True
