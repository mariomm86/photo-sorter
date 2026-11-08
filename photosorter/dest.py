"""Read the folders that already exist in the destination."""

from __future__ import annotations

import json
import os
from collections import Counter
from datetime import datetime
from pathlib import Path

from .exiftool import ExifTool
from .grouping import FOLDER_RE, ExistingFolder
from .scan import PHOTO_EXTS, _as_list, _parse_dt


def _cache_path(app_dir: Path) -> Path:
    return app_dir / "destination-cache.json"


def scan_destination(et: ExifTool, dest: str, app_dir: Path) -> list[ExistingFolder]:
    if not dest or not os.path.isdir(dest):
        return []
    try:
        cache = json.loads(_cache_path(app_dir).read_text())
    except (OSError, ValueError):
        cache = {}
    out: list[ExistingFolder] = []
    changed = False
    for entry in sorted(os.scandir(dest), key=lambda e: e.name):
        if not entry.is_dir() or not FOLDER_RE.match(entry.name):
            continue
        st = entry.stat()
        key = entry.path
        c = cache.get(key)
        if not c or c.get("mtime") != st.st_mtime_ns or "items" not in c:
            c = _read_folder(et, entry.path)
            c["mtime"] = st.st_mtime_ns
            cache[key] = c
            changed = True
        start = datetime.fromisoformat(c["start"]) if c.get("start") else None
        end = datetime.fromisoformat(c["end"]) if c.get("end") else None
        out.append(ExistingFolder(name=entry.name, path=entry.path, start=start, end=end,
                                  count=c.get("count", 0), keywords=c.get("keywords", []),
                                  items=c.get("items", [])))
    if changed:
        try:
            app_dir.mkdir(parents=True, exist_ok=True)
            _cache_path(app_dir).write_text(json.dumps(cache))
        except OSError:
            pass
    return out


def _read_folder(et: ExifTool, path: str) -> dict:
    photos, xmps = [], []
    for e in os.scandir(path):
        if not e.is_file() or e.name.startswith("."):
            continue
        ext = os.path.splitext(e.name)[1].lower()
        if ext in PHOTO_EXTS:
            photos.append(e.path)
        elif ext == ".xmp":
            xmps.append(e.path)
    dates: list[datetime] = []
    items: list[dict] = []
    kw = Counter()
    files = photos + xmps
    for i in range(0, len(files), 200):
        tags = ["DateTimeOriginal", "CreateDate", "XMP-dc:Subject", "Make", "Model",
                "SerialNumber", "InternalSerialNumber", "Orientation"]
        for rec in et.read_json(files[i:i + 200], tags, ["-fast"]):
            src = rec.get("SourceFile", "")
            if not src.lower().endswith(".xmp"):
                d = _parse_dt(rec.get("DateTimeOriginal")) or _parse_dt(rec.get("CreateDate"))
                if d:
                    dates.append(d)
                    make, model = (rec.get("Make") or "").strip(), (rec.get("Model") or "").strip()
                    serial = str(rec.get("SerialNumber") or rec.get("InternalSerialNumber") or "").strip()
                    items.append({"p": src, "t": d.isoformat(),
                                  "key": "|".join([make, model, serial]) if (make or model) else "unknown",
                                  "cam": model if model.lower().startswith(make.split()[0].lower() if make else "~") else f"{make} {model}".strip(),
                                  "o": rec.get("Orientation") or 1})
            for k in set(_as_list(rec.get("Subject"))):
                kw[k] += 1
    if not dates:
        # No readable dates: the folder can still be chosen by name, but is not
        # used for matching by date (a whole month would catch too much).
        return {"start": None, "end": None, "count": len(photos),
                "keywords": [k for k, _ in kw.most_common(8)], "items": []}
    common = [k for k, n in kw.most_common(8) if n >= max(1, len(photos) // 4)]
    # A RAW+JPEG pair counts once in the line-up.
    seen, unique = set(), []
    for it in sorted(items, key=lambda x: x["p"]):
        stem = os.path.splitext(it["p"])[0].lower()
        if stem not in seen:
            seen.add(stem)
            unique.append(it)
    return {"start": min(dates).isoformat(), "end": max(dates).isoformat(), "count": len(photos),
            "keywords": common, "items": unique}
