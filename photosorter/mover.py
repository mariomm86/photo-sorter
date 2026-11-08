"""Plan and execute moves, write ratings/keywords, keep a journal for undo.

Safety rules:
* never overwrite: a name clash gets a suffix (_2, _3 ...) on the whole set
  (RAW, JPEG and sidecar keep matching names);
* a file identical to one already at the destination is skipped and left in
  the source folder;
* photos marked for deletion are moved to <destination>/_to_delete, never erased;
* every step is written to a journal before the next one starts, so a run can
  be undone even after an error.
"""

from __future__ import annotations

import errno
import hashlib
import json
import os
import shutil
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable

from .exiftool import ExifTool
from .scan import Photo, kind_of

DELETE_FOLDER = "_to_delete"


@dataclass
class PlanItem:
    photo: Photo
    folder: str                         # folder name inside destination
    moves: list[tuple[str, str]] = field(default_factory=list)
    sidecar: str | None = None          # sidecar path to create or update (RAW/HEIC)
    embed: list[str] = field(default_factory=list)   # destination files to embed XMP into
    rating: int | None = None           # None = leave as is
    add_kw: list[str] = field(default_factory=list)
    remove_kw: list[str] = field(default_factory=list)
    final_kw: list[str] = field(default_factory=list)
    skip: str | None = None             # reason the photo is not moved
    renamed: bool = False

    @property
    def writes_metadata(self) -> bool:
        return bool(self.rating is not None or self.add_kw or self.remove_kw)


@dataclass
class Plan:
    dest: str
    items: list[PlanItem]
    folders_new: list[str]
    folders_existing: list[str]

    def summary(self) -> dict:
        moving = [i for i in self.items if not i.skip]
        return {
            "photos": len(moving),
            "files": sum(len(i.moves) for i in moving),
            "to_delete": sum(1 for i in moving if i.folder == DELETE_FOLDER),
            "skipped": [{"name": Path(i.photo.primary).name, "reason": i.skip} for i in self.items if i.skip],
            "renamed": [Path(i.moves[0][1]).name for i in moving if i.renamed],
            "folders_new": self.folders_new,
            "folders_existing": self.folders_existing,
            "sidecars": sum(1 for i in moving if i.sidecar and i.writes_metadata),
            "embedded": sum(len(i.embed) for i in moving if i.writes_metadata),
        }


def _sha1(path: str) -> str:
    h = hashlib.sha1()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def identical(a: str, b: str) -> bool:
    try:
        if os.path.getsize(a) != os.path.getsize(b):
            return False
    except OSError:
        return False
    return _sha1(a) == _sha1(b)


def _sidecar_name(src_sidecar: str, old_stem: str, new_stem: str) -> str:
    name = os.path.basename(src_sidecar)
    return new_stem + name[len(old_stem):] if name.lower().startswith(old_stem.lower()) else name


def build_plan(
    dest: str,
    assignments: list[tuple[Photo, str, list[str]]],   # (photo, folder name, extra keywords)
    heic_mode: str = "both",
) -> Plan:
    items: list[PlanItem] = []
    taken: set[str] = set()
    folders_new: set[str] = set()
    folders_existing: set[str] = set()

    for ph, folder, extra_kw in assignments:
        if ph.delete:
            folder = DELETE_FOLDER
        tdir = os.path.join(dest, folder)
        if folder != DELETE_FOLDER:
            (folders_existing if os.path.isdir(tdir) else folders_new).add(folder)
        item = PlanItem(photo=ph, folder=folder)
        old_stem = os.path.splitext(os.path.basename(ph.primary))[0]
        kinds = [kind_of(os.path.splitext(f)[1]) for f in [ph.primary, *ph.companions]]
        has_raw = any(k == "raw" for k in kinds)
        wants_sidecar = folder != DELETE_FOLDER and (has_raw or ("heic" in kinds and heic_mode in ("both", "sidecar") and not has_raw))

        n = 1
        while True:
            stem = old_stem if n == 1 else f"{old_stem}_{n}"
            moves = []
            for f in [ph.primary, *ph.companions]:
                moves.append((f, os.path.join(tdir, stem + os.path.splitext(f)[1])))
            for s in ph.sidecars:
                moves.append((s, os.path.join(tdir, _sidecar_name(s, old_stem, stem))))
            sidecar_target = os.path.join(tdir, stem + ".xmp") if wants_sidecar else None
            must_be_free = [d for _, d in moves]
            if sidecar_target and sidecar_target not in must_be_free:
                must_be_free.append(sidecar_target)
            clash = [d for d in must_be_free if os.path.exists(d) or d.lower() in taken]
            if not clash:
                break
            if n == 1 and os.path.exists(moves[0][1]) and identical(moves[0][0], moves[0][1]):
                item.skip = "already there (identical file)"
                break
            n += 1
        if item.skip:
            items.append(item)
            continue
        item.moves = moves
        item.renamed = n > 1
        for _, d in moves:
            taken.add(d.lower())
        if sidecar_target:
            taken.add(sidecar_target.lower())

        if folder != DELETE_FOLDER:
            final_kw = list(dict.fromkeys([*ph.keywords, *extra_kw]))
            item.final_kw = final_kw
            item.add_kw = [k for k in final_kw if k not in ph.file_keywords]
            item.remove_kw = [k for k in ph.file_keywords if k not in final_kw]
            if ph.rating != ph.file_rating:
                item.rating = ph.rating
            item.sidecar = sidecar_target
            for (src, dst), k in zip(moves, kinds):
                if k in ("jpeg", "dng", "other") or (k == "heic" and heic_mode in ("both", "embedded")):
                    item.embed.append(dst)
                elif k == "heic" and has_raw:
                    item.embed.append(dst)
        items.append(item)
    return Plan(dest=dest, items=items, folders_new=sorted(folders_new),
                folders_existing=sorted(folders_existing))


class Journal:
    def __init__(self, app_dir: Path) -> None:
        self.dir = app_dir / "journals"
        self.dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y-%m-%d-%H%M%S")
        self.path = self.dir / f"move-{stamp}.jsonl"
        self.backups = self.dir / f"move-{stamp}-backups"
        self._f = open(self.path, "a", encoding="utf-8")

    def write(self, **entry) -> None:
        entry["at"] = time.time()
        self._f.write(json.dumps(entry) + "\n")
        self._f.flush()
        os.fsync(self._f.fileno())

    def close(self) -> None:
        self._f.close()


def _move_file(src: str, dst: str) -> None:
    if os.path.exists(dst):
        raise FileExistsError(dst)
    try:
        os.rename(src, dst)
    except OSError as e:
        if e.errno != errno.EXDEV:
            raise
        # Different disk (SD card, external drive, NAS): copy, check, then remove.
        shutil.copy2(src, dst)
        if os.path.getsize(src) != os.path.getsize(dst):
            os.remove(dst)
            raise OSError(f"copy check failed for {src}")
        os.remove(src)


def _xmp_args(item: PlanItem) -> list[str]:
    args: list[str] = []
    if item.rating is not None:
        args.append(f"-XMP-xmp:Rating={item.rating}")
    for k in item.remove_kw:
        args += [f"-XMP-dc:Subject-={k}", f"-XMP-lr:HierarchicalSubject-={k}"]
    for k in item.add_kw:
        args += [f"-XMP-dc:Subject-={k}", f"-XMP-dc:Subject+={k}",
                 f"-XMP-lr:HierarchicalSubject-={k}", f"-XMP-lr:HierarchicalSubject+={k}"]
    return args


def _ok(out: bytes) -> bool:
    t = out.decode("utf-8", "replace")
    return ("1 image files updated" in t or "1 image files created" in t
            or "1 image files unchanged" in t)


def execute(
    plan: Plan,
    et: ExifTool,
    app_dir: Path,
    progress: Callable[[int, int], None] | None = None,
) -> dict:
    journal = Journal(app_dir)
    journal.write(op="start", dest=plan.dest)
    errors: list[str] = []
    made_dirs: set[str] = set()
    todo = [i for i in plan.items if not i.skip]
    for n, item in enumerate(todo, 1):
        try:
            tdir = os.path.dirname(item.moves[0][1])
            if tdir not in made_dirs and not os.path.isdir(tdir):
                os.makedirs(tdir)
                journal.write(op="mkdir", path=tdir)
            made_dirs.add(tdir)
            for src, dst in item.moves:
                _move_file(src, dst)
                journal.write(op="move", src=src, dst=dst)
            if item.writes_metadata:
                _write_metadata(item, et, journal, errors)
        except Exception as e:  # keep going; the journal records what happened
            errors.append(f"{os.path.basename(item.photo.primary)}: {e}")
            journal.write(op="error", photo=item.photo.primary, error=str(e))
        if progress:
            progress(n, len(todo))
    journal.write(op="end", errors=len(errors))
    journal.close()
    return {"journal": str(journal.path), "errors": errors}


def _write_metadata(item: PlanItem, et: ExifTool, journal: Journal, errors: list[str]) -> None:
    args = _xmp_args(item)
    if item.sidecar:
        if os.path.exists(item.sidecar):
            journal.backups.mkdir(parents=True, exist_ok=True)
            backup = journal.backups / f"{hashlib.sha1(item.sidecar.encode()).hexdigest()[:12]}.xmp"
            shutil.copy2(item.sidecar, backup)
            journal.write(op="edit_sidecar", path=item.sidecar, backup=str(backup))
            out = et.execute("-overwrite_original", "-P", *args, item.sidecar)
        else:
            new_args = []
            if item.photo.rating:
                new_args.append(f"-XMP-xmp:Rating={item.photo.rating}")
            for k in item.final_kw:
                new_args += [f"-XMP-dc:Subject+={k}", f"-XMP-lr:HierarchicalSubject+={k}"]
            if not new_args:
                new_args = args
            journal.write(op="create", path=item.sidecar)
            out = et.execute("-o", item.sidecar, *new_args)
        if not _ok(out):
            errors.append(f"{os.path.basename(item.sidecar)}: could not write sidecar ({_last_error(et, out)})")
    for f in item.embed:
        journal.write(op="embed", path=f)
        out = et.execute("-overwrite_original", "-P", *args, f)
        if not _ok(out):
            errors.append(f"{os.path.basename(f)}: could not write keywords ({_last_error(et, out)})")


def _last_error(et: ExifTool, out: bytes) -> str:
    errs = et.recent_errors()
    return errs[-1] if errs else out.decode("utf-8", "replace").strip()[:200]


def latest_journal(app_dir: Path) -> Path | None:
    d = app_dir / "journals"
    if not d.is_dir():
        return None
    runs = sorted(p for p in d.glob("move-*.jsonl") if not p.with_suffix(".undone").exists())
    return runs[-1] if runs else None


def undo(journal_path: Path) -> dict:
    entries = [json.loads(line) for line in journal_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    undone = skipped = 0
    problems: list[str] = []
    for e in reversed(entries):
        op = e.get("op")
        try:
            if op == "create":
                if os.path.exists(e["path"]):
                    os.remove(e["path"])
            elif op == "edit_sidecar":
                if os.path.exists(e["backup"]):
                    shutil.copy2(e["backup"], e["path"])
            elif op == "move":
                src, dst = e["src"], e["dst"]
                if os.path.exists(dst) and not os.path.exists(src):
                    os.makedirs(os.path.dirname(src), exist_ok=True)
                    _move_file(dst, src)
                    undone += 1
                else:
                    skipped += 1
                    problems.append(f"not moved back: {os.path.basename(dst)}")
            elif op == "mkdir":
                try:
                    os.rmdir(e["path"])
                except OSError:
                    pass
        except Exception as ex:
            skipped += 1
            problems.append(f"{op}: {ex}")
    journal_path.with_suffix(".undone").write_text(datetime.now().isoformat())
    return {"files_moved_back": undone, "skipped": skipped, "problems": problems[:50]}
