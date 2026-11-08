"""Split photos into collections (large events) with day sub-blocks.

Rules agreed in the spec:
* a gap of `gap_days` between consecutive photos starts a new collection;
* a GPS jump of more than `km` starts a new collection. With "keep trips
  together" on (default) a jump only splits when it also crosses the edge of
  the home area (leaving home or coming back), so day trips during a holiday
  stay in one collection;
* photos falling into the date range of an existing destination folder are
  kept apart from photos that do not, so they can be added to that folder.
"""

from __future__ import annotations

import math
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from .scan import Photo

FOLDER_RE = re.compile(r"^(\d{6})-(.+)$")
_BAD_CHARS = re.compile(r'[/\\:*?"<>|\x00-\x1f]')


@dataclass
class Settings:
    gap_days: float = 7.0
    km: float = 20.0
    home: tuple[float, float] | None = None       # set by the user
    keep_trips_together: bool = True
    day_blocks: bool = True


@dataclass
class ExistingFolder:
    name: str
    path: str
    start: datetime | None
    end: datetime | None
    count: int = 0
    keywords: list[str] = field(default_factory=list)   # most common first
    items: list[dict] = field(default_factory=list)      # photos inside: path, time, camera


@dataclass
class Collection:
    key: str
    photos: list[Photo]
    times: list[datetime]
    kind: str
    match: ExistingFolder | None
    prefix: str

    @property
    def start(self) -> datetime:
        return self.times[0]

    @property
    def end(self) -> datetime:
        return self.times[-1]


def haversine_km(a: tuple[float, float], b: tuple[float, float]) -> float:
    lat1, lon1, lat2, lon2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    h = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    return 2 * 6371.0 * math.asin(min(1.0, math.sqrt(h)))


def corrected_time(ph: Photo, offsets: dict[str, int]) -> datetime:
    base = datetime.fromisoformat(ph.raw_time) if ph.raw_time else datetime(1970, 1, 1)
    return base + timedelta(seconds=offsets.get(ph.camera_key, 0))


def detect_home(photos: list[Photo], times: dict[str, datetime]) -> tuple[tuple[float, float], int] | None:
    """Densest ~5 km GPS cell, counted in distinct days so one big trip can't win.

    Returns (centre, number of distinct days seen there)."""
    days: dict[tuple[int, int], set] = defaultdict(set)
    pts: dict[tuple[int, int], list[tuple[float, float]]] = defaultdict(list)
    for ph in photos:
        if ph.lat is None:
            continue
        cell = (round(ph.lat / 0.05), round(ph.lon / 0.05))
        days[cell].add(times[ph.id].date())
        pts[cell].append((ph.lat, ph.lon))
    if not days:
        return None
    best = max(days, key=lambda c: (len(days[c]), len(pts[c])))
    p = pts[best]
    return (sum(x for x, _ in p) / len(p), sum(y for _, y in p) / len(p)), len(days[best])


def folder_for(t: datetime, folders: list[ExistingFolder]) -> ExistingFolder | None:
    tol = timedelta(hours=12)
    for f in folders:
        if f.start and f.end and f.start - tol <= t <= f.end + tol:
            return f
    return None


def clean_name(name: str) -> str:
    name = _BAD_CHARS.sub("-", name or "").strip().strip(".").strip()
    return re.sub(r"\s+", " ", name)


def group(
    photos: list[Photo],
    settings: Settings,
    offsets: dict[str, int],
    folders: list[ExistingFolder],
    forced_starts: set[str],
    removed_starts: set[str],
    kind_overrides: dict[str, str],
    learned_home: tuple[tuple[float, float], int] | None = None,
) -> tuple[list[Collection], tuple[float, float] | None, tuple[tuple[float, float], int] | None]:
    if not photos:
        return [], settings.home or (learned_home[0] if learned_home else None), learned_home
    times = {ph.id: corrected_time(ph, offsets) for ph in photos}
    order = sorted(photos, key=lambda p: (times[p.id], p.primary))
    # A home learned from an earlier, larger scan wins over a guess from a few
    # days of photos (for example a card holding only a holiday).
    found = detect_home(order, times)
    if found and (learned_home is None or found[1] >= learned_home[1]):
        learned_home = found
    home = settings.home or (learned_home[0] if learned_home else None)
    gap = timedelta(days=settings.gap_days)

    def away(ph: Photo) -> bool | None:
        if ph.lat is None or home is None:
            return None
        return haversine_km((ph.lat, ph.lon), home) > settings.km

    auto_starts: set[str] = {order[0].id}
    last_gps: Photo | None = None
    last_folder = folder_for(times[order[0].id], folders)
    for i in range(1, len(order)):
        prev, cur = order[i - 1], order[i]
        split = times[cur.id] - times[prev.id] > gap
        if split:
            last_gps = None   # a new collection starts; don't compare places across it
        if cur.lat is not None and settings.km > 0:
            if last_gps is not None:
                jump = haversine_km((last_gps.lat, last_gps.lon), (cur.lat, cur.lon)) > settings.km
                if jump:
                    if settings.keep_trips_together and home is not None:
                        split = split or (away(last_gps) != away(cur))
                    else:
                        split = True
            last_gps = cur
        f = folder_for(times[cur.id], folders)
        if f is not last_folder:
            split = True
        last_folder = f
        if split:
            auto_starts.add(cur.id)

    starts = (auto_starts | forced_starts) - removed_starts
    starts.add(order[0].id)

    runs: list[list[Photo]] = []
    for ph in order:
        if ph.id in starts or not runs:
            runs.append([ph])
        else:
            runs[-1].append(ph)

    cols: list[Collection] = []
    for run in runs:
        ts = [times[p.id] for p in run]
        aways = [away(p) for p in run if away(p) is not None]
        if not aways:
            kind = "No GPS"
        else:
            kind = "Holiday" if sum(aways) > len(aways) / 2 else "Daily life"
        key = run[0].id
        kind = kind_overrides.get(key, kind)
        votes = Counter(id(folder_for(t, folders)) for t in ts)
        match = None
        for f in folders:
            if votes.get(id(f), 0) > len(run) / 2:
                match = f
        cols.append(Collection(key=key, photos=run, times=ts, kind=kind, match=match,
                               prefix=ts[0].strftime("%Y%m")))
    return cols, home, learned_home


def day_blocks(col: Collection) -> list[dict]:
    blocks: list[dict] = []
    for ph, t in zip(col.photos, col.times):
        d = t.date()
        if not blocks or blocks[-1]["date"] != d:
            blocks.append({"date": d, "photos": []})
        blocks[-1]["photos"].append(ph)
    return blocks
