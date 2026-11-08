"""Working state of one sorting session, saved so edits survive a restart."""

from __future__ import annotations

import json
import os
import threading
import traceback
from collections import Counter
from datetime import datetime
from pathlib import Path

from . import dest as dest_mod
from . import grouping, mover, scan
from .exiftool import ExifTool
from .grouping import Collection, ExistingFolder, Settings, clean_name

DAY_FMT = "%a %-d %b" if os.name != "nt" else "%a %d %b"


def app_dir() -> Path:
    d = Path(os.environ.get("PHOTOSORTER_HOME", Path.home() / ".photosorter"))
    d.mkdir(parents=True, exist_ok=True)
    return d


class Session:
    def __init__(self, et: ExifTool) -> None:
        self.et = et
        self.dir = app_dir()
        self.lock = threading.RLock()
        self.source = ""
        self.dest = ""
        self.recursive = True
        self.settings = Settings()
        self.heic_mode = "both"
        self.photos: dict[str, scan.Photo] = {}
        self.other_files = 0
        self.annotations: dict[str, dict] = {}     # by file path
        self.col_meta: dict[str, dict] = {}        # by collection key (first photo id)
        self.forced_starts: set[str] = set()
        self.removed_starts: set[str] = set()
        self.kind_overrides: dict[str, str] = {}
        self.offsets: dict[str, int] = {}
        self.recent_keywords: list[str] = []
        self.folders: list[ExistingFolder] = []
        self.dest_photos: dict[str, tuple[scan.Photo, str, str]] = {}   # id -> (photo, time, folder)
        self.collections: list[Collection] = []
        self.home: tuple[float, float] | None = None
        self.learned_home: tuple[tuple[float, float], int] | None = None
        self.scan_state = {"running": False, "phase": "", "done": 0, "total": 0, "error": None}
        self.apply_state = {"running": False, "done": 0, "total": 0, "result": None, "error": None}
        self._load()

    # ---------- persistence ----------
    def _load(self) -> None:
        try:
            data = json.loads((self.dir / "session.json").read_text())
        except (OSError, ValueError):
            data = {}
        self.source = data.get("source", "")
        self.dest = data.get("dest", "")
        self.recursive = data.get("recursive", True)
        s = data.get("settings", {})
        self.settings = Settings(
            gap_days=s.get("gap_days", 7.0), km=s.get("km", 20.0),
            home=tuple(s["home"]) if s.get("home") else None,
            keep_trips_together=s.get("keep_trips_together", True),
        )
        self.heic_mode = data.get("heic_mode", "both")
        self.annotations = data.get("annotations", {})
        self.col_meta = data.get("col_meta", {})
        self.forced_starts = set(data.get("forced_starts", []))
        self.removed_starts = set(data.get("removed_starts", []))
        self.kind_overrides = data.get("kind_overrides", {})
        self.recent_keywords = data.get("recent_keywords", [])
        lh = data.get("learned_home")
        self.learned_home = ((lh[0], lh[1]), lh[2]) if lh else None
        try:
            self.offsets = json.loads((self.dir / "cameras.json").read_text())
        except (OSError, ValueError):
            self.offsets = {}

    def save(self) -> None:
        data = {
            "source": self.source, "dest": self.dest, "recursive": self.recursive,
            "settings": {"gap_days": self.settings.gap_days, "km": self.settings.km,
                         "home": list(self.settings.home) if self.settings.home else None,
                         "keep_trips_together": self.settings.keep_trips_together},
            "heic_mode": self.heic_mode, "annotations": self.annotations, "col_meta": self.col_meta,
            "forced_starts": sorted(self.forced_starts), "removed_starts": sorted(self.removed_starts),
            "kind_overrides": self.kind_overrides, "recent_keywords": self.recent_keywords[:15],
            "learned_home": [*self.learned_home[0], self.learned_home[1]] if self.learned_home else None,
        }
        tmp = self.dir / "session.json.tmp"
        tmp.write_text(json.dumps(data))
        os.replace(tmp, self.dir / "session.json")
        (self.dir / "cameras.json").write_text(json.dumps(self.offsets))

    # ---------- scanning ----------
    def start_scan(self) -> None:
        with self.lock:
            if self.scan_state["running"]:
                return
            self.scan_state = {"running": True, "phase": "Looking for photos", "done": 0, "total": 0, "error": None}
        threading.Thread(target=self._scan, daemon=True).start()

    def _scan(self) -> None:
        try:
            paths, xmps, others = scan.walk(self.source, self.recursive)
            photos = scan.build_photos(paths, xmps)
            self.scan_state.update(phase="Reading dates, cameras and GPS", total=len(photos))

            def prog(done: int, total: int) -> None:
                self.scan_state.update(done=done, total=total)

            scan.read_metadata(self.et, photos, prog)
            self.scan_state.update(phase="Checking the destination folder")
            folders = dest_mod.scan_destination(self.et, self.dest, self.dir) if self.dest else []
            with self.lock:
                self.photos = {p.id: p for p in photos}
                self.other_files = len(others)
                for p in photos:
                    a = self.annotations.get(p.primary)
                    if a:
                        p.rating = a.get("rating", p.rating)
                        p.keywords = a.get("keywords", p.keywords)
                        p.delete = a.get("delete", False)
                self.folders = folders
                self._index_dest()
                self.regroup()
            self.save()
            self.scan_state.update(running=False, phase="done")
        except Exception as e:
            traceback.print_exc()
            self.scan_state.update(running=False, phase="error", error=str(e))

    def refresh_destination(self) -> None:
        self.folders = dest_mod.scan_destination(self.et, self.dest, self.dir) if self.dest else []
        self._index_dest()
        self.regroup()

    def _index_dest(self) -> None:
        """Photos already in the destination, for thumbnails in the camera line-up."""
        out = {}
        for f in self.folders:
            for it in f.items:
                ph = scan.Photo(id=scan.photo_id(it["p"]), primary=it["p"],
                                kind=scan.kind_of(os.path.splitext(it["p"])[1]),
                                camera=it.get("cam") or "Unknown camera", camera_key=it.get("key", "unknown"),
                                raw_time=it["t"])
                try:
                    ph.orientation = int(it.get("o") or 1)
                except (TypeError, ValueError):
                    pass
                out[ph.id] = (ph, it["t"], f.name)
        self.dest_photos = out

    def regroup(self) -> None:
        with self.lock:
            self.collections, self.home, self.learned_home = grouping.group(
                list(self.photos.values()), self.settings, self.offsets, self.folders,
                self.forced_starts, self.removed_starts, self.kind_overrides, self.learned_home,
            )

    # ---------- annotations ----------
    def _remember(self, p: scan.Photo) -> None:
        self.annotations[p.primary] = {"rating": p.rating, "keywords": p.keywords, "delete": p.delete}

    def update_photos(self, ids: list[str], rating=None, add=None, remove=None, delete=None) -> None:
        with self.lock:
            for i in ids:
                p = self.photos.get(i)
                if not p:
                    continue
                if rating is not None:
                    p.rating = max(0, min(5, int(rating)))
                for k in add or []:
                    k = k.strip()
                    if k and k not in p.keywords:
                        p.keywords.append(k)
                if remove:
                    p.keywords = [k for k in p.keywords if k not in remove]
                if delete is not None:
                    p.delete = bool(delete)
                self._remember(p)
            for k in add or []:
                k = k.strip()
                if k:
                    self.recent_keywords = [k] + [x for x in self.recent_keywords if x != k]
            self.save()

    def collection(self, key: str) -> Collection | None:
        for c in self.collections:
            if c.key == key:
                return c
        return None

    def meta(self, c: Collection) -> dict:
        m = self.col_meta.get(c.key, {})
        return {"name": m.get("name", ""), "use_existing": m.get("use_existing", True),
                "copy_keywords": m.get("copy_keywords", True)}

    def target(self, c: Collection) -> str:
        m = self.meta(c)
        if c.match and m["use_existing"]:
            return c.match.name
        return f"{c.prefix}-{clean_name(m['name']) or 'unnamed'}"

    def extra_keywords(self, c: Collection) -> list[str]:
        m = self.meta(c)
        if c.match and m["use_existing"] and m["copy_keywords"]:
            return list(c.match.keywords)
        return []

    # ---------- output for the browser ----------
    def collection_json(self, c: Collection) -> dict:
        m = self.meta(c)
        target = self.target(c)
        exists = bool(self.dest) and os.path.isdir(os.path.join(self.dest, target))
        return {
            "key": c.key, "prefix": c.prefix, "name": m["name"], "target": target,
            "unnamed": not (c.match and m["use_existing"]) and not clean_name(m["name"]),
            "kind": c.kind, "count": len(c.photos),
            "start": c.start.isoformat(), "end": c.end.isoformat(),
            "dates": _date_range(c.start, c.end),
            "cameras": sorted({p.camera for p in c.photos}),
            "to_delete": sum(1 for p in c.photos if p.delete),
            "existing_target": exists,
            "match": None if not c.match else {
                "name": c.match.name, "count": c.match.count, "keywords": c.match.keywords,
                "dates": _date_range(c.match.start, c.match.end) if c.match.start else "",
            },
            "use_existing": m["use_existing"], "copy_keywords": m["copy_keywords"],
        }

    def detail_json(self, c: Collection) -> dict:
        days = []
        for b in grouping.day_blocks(c):
            photos = []
            for p in b["photos"]:
                j = p.to_json()
                i = c.photos.index(p)
                j["time"] = c.times[i].strftime("%H:%M")
                photos.append(j)
            days.append({"date": b["date"].isoformat(), "label": b["date"].strftime(DAY_FMT), "photos": photos})
        out = self.collection_json(c)
        out["days"] = days
        return out

    def cameras_json(self) -> list[dict]:
        groups: dict[str, list[scan.Photo]] = {}
        for p in self.photos.values():
            groups.setdefault(p.camera_key, []).append(p)
        out = []
        for key, ps in sorted(groups.items(), key=lambda kv: -len(kv[1])):
            first = min(grouping.corrected_time(p, self.offsets) for p in ps)
            out.append({"key": key, "name": ps[0].camera, "count": len(ps),
                        "offset": self.offsets.get(key, 0), "tz": any(p.tz_in_file for p in ps),
                        "first": first.strftime("%-d %b %Y, %H:%M") if os.name != "nt" else first.isoformat()})
        return out

    def lineup_json(self, date: str | None) -> dict:
        """Photos of one day from all cameras, in corrected time order."""
        with self.lock:
            by_day: dict[str, list] = {}
            for p in self.photos.values():
                t = grouping.corrected_time(p, self.offsets)
                by_day.setdefault(t.date().isoformat(), []).append((t, p, None))
            src_days = set(by_day)
            for ph, _, folder in self.dest_photos.values():
                t = grouping.corrected_time(ph, self.offsets)
                d = t.date().isoformat()
                if d in src_days:
                    by_day[d].append((t, ph, folder))
            days = []
            for d, items in sorted(by_day.items()):
                cams = {p.camera_key for _, p, _ in items}
                if len(cams) > 1:
                    days.append({"date": d, "label": datetime.fromisoformat(d).strftime(DAY_FMT + " %Y"),
                                 "cameras": len(cams), "count": len(items)})
            if not date and days:
                date = days[0]["date"]
            photos = []
            for t, p, folder in sorted(by_day.get(date or "", []), key=lambda x: x[0]):
                photos.append({"id": p.id, "camera": p.camera, "camera_key": p.camera_key,
                               "time": t.strftime("%H:%M:%S"), "name": os.path.basename(p.primary),
                               "sorted": folder is not None, "folder": folder})
            return {"date": date, "days": days, "photos": photos}

    def state_json(self) -> dict:
        with self.lock:
            fmts = Counter()
            for p in self.photos.values():
                for f in [p.primary, *p.companions]:
                    fmts[scan.kind_of(os.path.splitext(f)[1])] += 1
            return {
                "source": self.source, "dest": self.dest, "recursive": self.recursive,
                "settings": {"gap_days": self.settings.gap_days, "km": self.settings.km,
                             "home": self.settings.home, "keep_trips_together": self.settings.keep_trips_together},
                "heic_mode": self.heic_mode,
                "detected_home": self.home,
                "scan": self.scan_state,
                "summary": {
                    "photos": len(self.photos), "collections": len(self.collections),
                    "cameras": len({p.camera_key for p in self.photos.values()}),
                    "gps": sum(1 for p in self.photos.values() if p.lat is not None),
                    "formats": dict(fmts), "pairs": sum(1 for p in self.photos.values() if p.companions),
                    "no_date": sum(1 for p in self.photos.values() if p.time_source == "file"),
                    "other_files": self.other_files,
                    "existing_folders": len(self.folders),
                },
                "cameras": self.cameras_json(),
                "collections": [self.collection_json(c) for c in self.collections],
                "recent_keywords": self.recent_keywords[:8],
            }

    # ---------- planning and moving ----------
    def plan(self) -> mover.Plan:
        assignments = []
        for c in self.collections:
            folder, extra = self.target(c), self.extra_keywords(c)
            for p in c.photos:
                assignments.append((p, folder, extra))
        return mover.build_plan(self.dest, assignments, self.heic_mode)

    def plan_json(self) -> dict:
        with self.lock:
            plan = self.plan()
            summary = plan.summary()
            folders: dict[str, dict] = {}
            for it in plan.items:
                f = folders.setdefault(it.folder, {"name": it.folder, "adding": 0, "samples": [],
                                                   "existing": it.folder in plan.folders_existing,
                                                   "before": 0, "skipped": 0})
                if it.skip:
                    f["skipped"] += 1
                    if len(f["samples"]) < 6:
                        f["samples"].append({"name": os.path.basename(it.photo.primary), "note": "skipped, already there"})
                    continue
                f["adding"] += 1
                if len(f["samples"]) < 6 and (it.renamed or len(f["samples"]) < 3):
                    note = "renamed, name taken" if it.renamed else ""
                    f["samples"].append({"name": os.path.basename(it.moves[0][1]), "note": note})
            for f in folders.values():
                if f["existing"]:
                    try:
                        f["before"] = sum(1 for e in os.scandir(os.path.join(self.dest, f["name"]))
                                          if e.is_file() and not e.name.startswith(".")
                                          and os.path.splitext(e.name)[1].lower() in scan.PHOTO_EXTS)
                    except OSError:
                        pass
            per_target = Counter(self.target(c) for c in self.collections)
            shared = [t for t, n in per_target.items() if n > 1]
            unnamed = [self.target(c) for c in self.collections if self.collection_json(c)["unnamed"]]
            order = sorted(folders.values(), key=lambda f: (f["name"] == mover.DELETE_FOLDER, f["name"]))
            return {"dest": self.dest, "summary": summary, "folders": order,
                    "unnamed": sorted(set(unnamed)), "shared": shared,
                    "dest_ok": bool(self.dest) and os.path.isdir(self.dest),
                    "same_as_source": bool(self.dest) and os.path.realpath(self.dest) == os.path.realpath(self.source)}

    def start_apply(self) -> None:
        with self.lock:
            if self.apply_state["running"]:
                return
            self.apply_state = {"running": True, "done": 0, "total": 0, "result": None, "error": None}
        threading.Thread(target=self._apply, daemon=True).start()

    def _apply(self) -> None:
        started = datetime.now()
        try:
            with self.lock:
                plan = self.plan()
            summary = plan.summary()
            self.apply_state["total"] = summary["photos"]

            def prog(done: int, total: int) -> None:
                self.apply_state.update(done=done, total=total)

            res = mover.execute(plan, self.et, self.dir, prog)
            summary.update(res)
            summary["seconds"] = int((datetime.now() - started).total_seconds())
            with self.lock:
                moved_ids = {i.photo.id for i in plan.items if not i.skip}
                for pid in moved_ids:
                    p = self.photos.pop(pid, None)
                    if p:
                        self.annotations.pop(p.primary, None)
                self.save()
                self.folders = dest_mod.scan_destination(self.et, self.dest, self.dir)
                self._index_dest()
                self.regroup()
            self.apply_state.update(running=False, result=summary)
        except Exception as e:
            traceback.print_exc()
            self.apply_state.update(running=False, error=str(e))

    def undo_last(self) -> dict:
        j = mover.latest_journal(self.dir)
        if not j:
            return {"error": "Nothing to undo."}
        res = mover.undo(j)
        self.start_scan()
        return res


def _date_range(a: datetime | None, b: datetime | None) -> str:
    if not a or not b:
        return ""
    if a.date() == b.date():
        return a.strftime("%-d %b %Y") if os.name != "nt" else a.strftime("%d %b %Y")
    if a.year == b.year and a.month == b.month:
        return f"{a.day}–{b.day} {b.strftime('%b %Y')}"
    if a.year == b.year:
        return f"{a.day} {a.strftime('%b')} – {b.day} {b.strftime('%b %Y')}"
    return f"{a.strftime('%b %Y')} – {b.strftime('%b %Y')}"
