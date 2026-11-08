"""Find photos in a folder, pair RAW+JPEG files and read their metadata."""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable

from .exiftool import ExifTool

RAW_EXTS = {
    ".arw", ".srf", ".sr2", ".cr2", ".cr3", ".crw", ".nef", ".nrw", ".raf", ".rw2",
    ".orf", ".pef", ".rwl", ".3fr", ".iiq", ".srw", ".x3f", ".erf", ".mrw", ".kdc",
    ".dcr", ".mos", ".mef",
}
DNG_EXTS = {".dng"}
JPEG_EXTS = {".jpg", ".jpeg", ".jpe"}
HEIC_EXTS = {".heic", ".heif", ".hif"}
OTHER_IMG_EXTS = {".png", ".tif", ".tiff"}
PHOTO_EXTS = RAW_EXTS | DNG_EXTS | JPEG_EXTS | HEIC_EXTS | OTHER_IMG_EXTS
SKIP_DIRS = {"_to_delete", ".photosorter", "@eaDir", "#recycle", ".Trashes", ".Spotlight-V100", ".fseventsd"}

READ_TAGS = [
    "FileSize", "FileModifyDate", "Make", "Model", "SerialNumber", "InternalSerialNumber",
    "DateTimeOriginal", "SubSecTimeOriginal", "CreateDate", "OffsetTimeOriginal",
    "Composite:GPSLatitude", "Composite:GPSLongitude", "Orientation",
    "ImageWidth", "ImageHeight", "XMP-xmp:Rating", "XMP-dc:Subject",
]


def kind_of(ext: str) -> str:
    ext = ext.lower()
    if ext in RAW_EXTS:
        return "raw"
    if ext in DNG_EXTS:
        return "dng"
    if ext in JPEG_EXTS:
        return "jpeg"
    if ext in HEIC_EXTS:
        return "heic"
    if ext in OTHER_IMG_EXTS:
        return "other"
    return "unknown"


@dataclass
class Photo:
    id: str
    primary: str                      # absolute path of the main file (RAW if a pair)
    companions: list[str] = field(default_factory=list)   # paired JPEG/HEIC
    sidecars: list[str] = field(default_factory=list)     # existing .xmp next to it
    kind: str = "jpeg"
    fmt: str = "JPEG"
    camera: str = "Unknown camera"
    camera_key: str = "unknown"
    raw_time: str | None = None       # ISO, as recorded in the file (local time)
    time_source: str = "exif"         # exif | file
    tz_in_file: bool = False
    lat: float | None = None
    lon: float | None = None
    orientation: int = 1
    size: int = 0
    rating: int = 0
    keywords: list[str] = field(default_factory=list)
    file_rating: int = 0
    file_keywords: list[str] = field(default_factory=list)
    delete: bool = False

    def files(self) -> list[str]:
        return [self.primary, *self.companions, *self.sidecars]

    def to_json(self) -> dict:
        return {
            "id": self.id, "name": Path(self.primary).name, "fmt": self.fmt, "kind": self.kind,
            "camera": self.camera, "camera_key": self.camera_key, "lat": self.lat, "lon": self.lon,
            "rating": self.rating, "keywords": self.keywords, "delete": self.delete,
            "files": [Path(f).name for f in self.files()], "time_source": self.time_source,
            "orientation": self.orientation,
        }


def photo_id(path: str) -> str:
    return hashlib.sha1(path.encode("utf-8")).hexdigest()[:16]


def _parse_dt(value) -> datetime | None:
    if not value or not isinstance(value, str):
        return None
    s = value.strip()
    if s.startswith("0000"):
        return None
    s = s[:19]
    for fmt in ("%Y:%m:%d %H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y:%m:%d %H:%M", "%Y:%m:%d"):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    return None


def _as_list(v) -> list[str]:
    if v is None:
        return []
    if isinstance(v, list):
        return [str(x) for x in v if str(x).strip()]
    return [str(v)] if str(v).strip() else []


def walk(source: str, recursive: bool = True) -> tuple[list[str], list[str], list[str]]:
    """Return (photo files, xmp sidecars, other files)."""
    photos, xmps, others = [], [], []
    for root, dirs, files in os.walk(source):
        dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS and not d.startswith("."))
        if not recursive:
            dirs[:] = []
        for name in sorted(files):
            if name.startswith("._") or name.startswith("."):
                continue
            p = os.path.join(root, name)
            ext = os.path.splitext(name)[1].lower()
            if ext in PHOTO_EXTS:
                photos.append(p)
            elif ext == ".xmp":
                xmps.append(p)
            else:
                others.append(p)
    return photos, xmps, others


def build_photos(paths: list[str], xmps: list[str]) -> list[Photo]:
    """Group files into Photo items: RAW + JPEG/HEIC with the same name become one."""
    by_stem: dict[tuple[str, str], list[str]] = {}
    for p in paths:
        d, n = os.path.split(p)
        by_stem.setdefault((d, os.path.splitext(n)[0].lower()), []).append(p)
    xmp_by_stem: dict[tuple[str, str], list[str]] = {}
    for x in xmps:
        d, n = os.path.split(x)
        stem = os.path.splitext(n)[0]
        # Both "IMG_1.xmp" (Lightroom) and "IMG_1.CR3.xmp" (darktable) belong to IMG_1.
        base = os.path.splitext(stem)[0] if os.path.splitext(stem)[1].lower() in PHOTO_EXTS else stem
        xmp_by_stem.setdefault((d, base.lower()), []).append(x)

    out: list[Photo] = []
    for key, files in by_stem.items():
        raws = [f for f in files if kind_of(os.path.splitext(f)[1]) in ("raw", "dng")]
        rest = [f for f in files if f not in raws]
        groups: list[list[str]] = []
        if len(raws) == 1:
            groups.append([raws[0], *rest])
        else:
            groups.extend([[f] for f in files])
        for g in groups:
            primary = g[0]
            kinds = [kind_of(os.path.splitext(f)[1]) for f in g]
            label = {"raw": "RAW", "dng": "DNG", "jpeg": "JPEG", "heic": "HEIC", "other": "IMG"}
            fmt = "+".join(label[k] for k in kinds)
            fmt = fmt.replace("JPEG", "JPG") if "+" in fmt else fmt
            ph = Photo(id=photo_id(primary), primary=primary, companions=g[1:], kind=kinds[0], fmt=fmt)
            if len(g) == len(files):
                ph.sidecars = xmp_by_stem.get(key, [])
            out.append(ph)
    return out


def read_metadata(et: ExifTool, photos: list[Photo], progress: Callable[[int, int], None] | None = None) -> None:
    files = []
    owner: dict[str, Photo] = {}
    for ph in photos:
        files.append(ph.primary)
        owner[ph.primary] = ph
        for s in ph.sidecars:          # keywords/ratings of RAW files live in sidecars
            files.append(s)
            owner[s] = ph
    total = len(files)
    chunk = 150
    for i in range(0, total, chunk):
        part = files[i:i + chunk]
        for rec in et.read_json(part, READ_TAGS, extra=["-fast"]):
            src = rec.get("SourceFile")
            ph = owner.get(src) or owner.get(os.path.normpath(src or ""))
            if not ph:
                continue
            if src.lower().endswith(".xmp"):
                if rec.get("Rating") is not None:
                    ph.file_rating = int(float(rec["Rating"]))
                ph.file_keywords = sorted(set(ph.file_keywords) | set(_as_list(rec.get("Subject"))))
                continue
            _apply_record(ph, rec)
        if progress:
            progress(min(i + chunk, total), total)
    for ph in photos:
        ph.rating = ph.file_rating
        ph.keywords = list(ph.file_keywords)


def _apply_record(ph: Photo, rec: dict) -> None:
    make = (rec.get("Make") or "").strip()
    model = (rec.get("Model") or "").strip()
    if model and make and model.lower().startswith(make.split()[0].lower()):
        name = model
    else:
        name = f"{make} {model}".strip()
    ph.camera = name or "Unknown camera"
    serial = str(rec.get("SerialNumber") or rec.get("InternalSerialNumber") or "").strip()
    ph.camera_key = "|".join([make, model, serial]) if (make or model) else "unknown"
    dt = _parse_dt(rec.get("DateTimeOriginal")) or _parse_dt(rec.get("CreateDate"))
    if dt is None:
        dt = _parse_dt(rec.get("FileModifyDate"))
        ph.time_source = "file"
    ph.raw_time = dt.isoformat() if dt else None
    ph.tz_in_file = bool(rec.get("OffsetTimeOriginal"))
    lat, lon = rec.get("GPSLatitude"), rec.get("GPSLongitude")
    try:
        if lat is not None and lon is not None and not (float(lat) == 0 and float(lon) == 0):
            ph.lat, ph.lon = float(lat), float(lon)
    except (TypeError, ValueError):
        pass
    try:
        ph.orientation = int(rec.get("Orientation") or 1)
    except (TypeError, ValueError):
        ph.orientation = 1
    try:
        ph.size = int(rec.get("FileSize") or 0)
    except (TypeError, ValueError):
        ph.size = 0
    if rec.get("Rating") is not None and not ph.sidecars:
        try:
            ph.file_rating = int(float(rec["Rating"]))
        except (TypeError, ValueError):
            pass
    if not ph.sidecars:
        ph.file_keywords = sorted(set(_as_list(rec.get("Subject"))))
