"""Local web server. Listens on 127.0.0.1 only and requires a per-start token."""

from __future__ import annotations

import json
import mimetypes
import os
import platform
import secrets
import shutil
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from .exiftool import ExifTool
from .grouping import Settings
from .session import Session
from .thumbs import Thumbnailer

WEB = Path(__file__).parent / "web"


def pick_folder(prompt: str) -> str | None:
    """Open the operating system's folder dialog on this computer."""
    if sys.platform == "darwin":
        script = (f'set f to choose folder with prompt "{prompt.replace(chr(34), "")}"\n'
                  'POSIX path of f')
        r = subprocess.run(["osascript", "-e", script], capture_output=True, text=True)
        if r.returncode != 0:
            return None
        return r.stdout.strip().rstrip("/") or "/"
    try:
        import tkinter
        from tkinter import filedialog
        root = tkinter.Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        path = filedialog.askdirectory(title=prompt)
        root.destroy()
        return path or None
    except Exception:
        return None


def open_in_file_browser(path: str) -> None:
    if sys.platform == "darwin":
        subprocess.Popen(["open", path])
    elif os.name == "nt":
        os.startfile(path)  # type: ignore[attr-defined]
    elif shutil.which("xdg-open"):
        subprocess.Popen(["xdg-open", path])


def make_server(port: int = 8765, host: str = "127.0.0.1"):
    et = ExifTool()
    et_thumbs = ExifTool(et.path)
    session = Session(et)
    thumbs = Thumbnailer(et_thumbs, session.dir / "cache" / "thumbs")
    token = secrets.token_urlsafe(16)
    try:
        et_version = et.version()
    except Exception:
        et_version = "?"

    class Handler(BaseHTTPRequestHandler):
        server_version = "PhotoSorter"

        def log_message(self, fmt, *args):  # keep the Terminal quiet
            pass

        # ----- helpers -----
        def _host_ok(self) -> bool:
            host_hdr = (self.headers.get("Host") or "").split(":")[0]
            return host_hdr in ("127.0.0.1", "localhost")

        def _token_ok(self, qs) -> bool:
            t = self.headers.get("X-Token") or (qs.get("t") or [""])[0]
            return secrets.compare_digest(t, token)

        def _send(self, code: int, body: bytes, ctype: str, cache: bool = False) -> None:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "private, max-age=86400" if cache else "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.end_headers()
            self.wfile.write(body)

        def _json(self, obj, code: int = 200) -> None:
            self._send(code, json.dumps(obj).encode("utf-8"), "application/json; charset=utf-8")

        def _body(self) -> dict:
            n = int(self.headers.get("Content-Length") or 0)
            if not n:
                return {}
            try:
                return json.loads(self.rfile.read(n).decode("utf-8"))
            except ValueError:
                return {}

        # ----- GET -----
        def do_GET(self):
            if not self._host_ok():
                return self._send(403, b"Forbidden", "text/plain")
            u = urlparse(self.path)
            qs = parse_qs(u.query)
            path = u.path
            if path in ("/", "/index.html"):
                return self._static("index.html")
            if path.startswith("/static/"):
                return self._static(path[len("/static/"):])
            if not self._token_ok(qs):
                return self._json({"error": "Open the link shown in the Terminal window."}, 401)
            if path == "/api/state":
                st = session.state_json()
                st["exiftool"] = et_version
                st["platform"] = platform.system()
                return self._json(st)
            if path.startswith("/api/collection/"):
                c = session.collection(path.rsplit("/", 1)[1])
                return self._json(session.detail_json(c)) if c else self._json({"error": "not found"}, 404)
            if path == "/api/lineup":
                return self._json(session.lineup_json((qs.get("date") or [None])[0]))
            if path == "/api/plan":
                return self._json(session.plan_json())
            if path == "/api/apply":
                return self._json(session.apply_state)
            if path.startswith("/thumb/") or path.startswith("/preview/"):
                pid = path.rsplit("/", 1)[1]
                p = session.photos.get(pid)
                if not p and pid in session.dest_photos:
                    p = session.dest_photos[pid][0]
                if not p:
                    return self._send(404, b"", "text/plain")
                size = 480 if path.startswith("/thumb/") else 1600
                data, ctype = thumbs.get(p, size)
                return self._send(200, data, ctype, cache=True)
            return self._send(404, b"Not found", "text/plain")

        def _static(self, name: str):
            f = (WEB / name).resolve()
            if WEB.resolve() not in f.parents or not f.is_file():
                return self._send(404, b"Not found", "text/plain")
            ctype = mimetypes.guess_type(str(f))[0] or "application/octet-stream"
            if ctype.startswith("text/") or ctype.endswith("javascript"):
                ctype += "; charset=utf-8"
            return self._send(200, f.read_bytes(), ctype)

        # ----- POST -----
        def do_POST(self):
            if not self._host_ok():
                return self._send(403, b"Forbidden", "text/plain")
            origin = self.headers.get("Origin")
            if origin and urlparse(origin).hostname not in ("127.0.0.1", "localhost"):
                return self._send(403, b"Forbidden", "text/plain")
            u = urlparse(self.path)
            if not self._token_ok(parse_qs(u.query)):
                return self._json({"error": "unauthorized"}, 401)
            b = self._body()
            path = u.path
            try:
                return self._route_post(path, b)
            except Exception as e:  # report to the browser instead of crashing
                return self._json({"error": str(e)}, 500)

        def _route_post(self, path: str, b: dict):
            s = session
            if path == "/api/pick-folder":
                return self._json({"path": pick_folder(b.get("prompt") or "Choose a folder")})
            if path == "/api/scan":
                src = (b.get("source") or "").strip()
                if not src or not os.path.isdir(src):
                    return self._json({"error": "That source folder does not exist."}, 400)
                dst = (b.get("dest") or "").strip()
                if dst and not os.path.isdir(dst):
                    return self._json({"error": "That destination folder does not exist."}, 400)
                if src != s.source:
                    s.forced_starts.clear()
                    s.removed_starts.clear()
                    s.col_meta.clear()
                    s.kind_overrides.clear()
                s.source, s.dest = src, dst
                s.recursive = bool(b.get("recursive", True))
                _apply_settings(s, b)
                s.save()
                s.start_scan()
                return self._json({"ok": True})
            if path == "/api/settings":
                _apply_settings(s, b)
                s.regroup()
                s.save()
                return self._json({"ok": True})
            if path == "/api/destination":
                dst = (b.get("dest") or "").strip()
                if dst and not os.path.isdir(dst):
                    return self._json({"error": "That destination folder does not exist."}, 400)
                s.dest = dst
                s.refresh_destination()
                s.save()
                return self._json({"ok": True})
            if path == "/api/camera-offset":
                s.offsets[b["key"]] = int(b.get("seconds") or 0)
                s.regroup()
                s.save()
                return self._json({"ok": True})
            if path == "/api/collection":
                key = b["key"]
                m = s.col_meta.setdefault(key, {})
                for k in ("name", "use_existing", "copy_keywords"):
                    if k in b:
                        m[k] = b[k]
                if "kind" in b:
                    s.kind_overrides[key] = b["kind"]
                    s.regroup()
                s.save()
                c = s.collection(key)
                return self._json(s.collection_json(c) if c else {})
            if path == "/api/merge":
                key = b["key"]
                s.forced_starts.discard(key)
                s.removed_starts.add(key)
                s.regroup()
                s.save()
                return self._json({"ok": True})
            if path == "/api/split":
                pid = b["photo_id"]
                s.removed_starts.discard(pid)
                s.forced_starts.add(pid)
                s.regroup()
                s.save()
                return self._json({"ok": True, "key": pid})
            if path == "/api/photos":
                s.update_photos(b.get("ids", []), rating=b.get("rating"), add=b.get("add"),
                                remove=b.get("remove"), delete=b.get("delete"))
                return self._json({"ok": True})
            if path == "/api/apply":
                plan = s.plan_json()
                if not plan["dest_ok"]:
                    return self._json({"error": "Choose an existing destination folder first."}, 400)
                if plan["same_as_source"]:
                    return self._json({"error": "The destination must be different from the source folder."}, 400)
                s.start_apply()
                return self._json({"ok": True})
            if path == "/api/undo":
                return self._json(s.undo_last())
            if path == "/api/open":
                target = b.get("path") or s.dest
                if target and os.path.isdir(target):
                    open_in_file_browser(target)
                return self._json({"ok": True})
            if path == "/api/quit":
                self._json({"ok": True})
                threading.Thread(target=httpd.shutdown, daemon=True).start()
                return None
            return self._json({"error": "unknown"}, 404)

    def _apply_settings(s: Session, b: dict) -> None:
        st = s.settings
        if "gap_days" in b:
            st.gap_days = max(0.01, float(b["gap_days"]))
        if "km" in b:
            st.km = max(0.0, float(b["km"]))
        if "keep_trips_together" in b:
            st.keep_trips_together = bool(b["keep_trips_together"])
        if "home" in b:
            st.home = _parse_home(b["home"])
        if "heic_mode" in b and b["heic_mode"] in ("both", "sidecar", "embedded"):
            s.heic_mode = b["heic_mode"]

    httpd = None
    for p in range(port, port + 20):
        try:
            httpd = ThreadingHTTPServer((host, p), Handler)
            break
        except OSError:
            continue
    if httpd is None:
        raise RuntimeError("No free port found")
    httpd.daemon_threads = True
    url = f"http://127.0.0.1:{httpd.server_address[1]}/?t={token}"
    return httpd, url, (et, et_thumbs)


def _parse_home(v) -> tuple[float, float] | None:
    if not v:
        return None
    if isinstance(v, (list, tuple)) and len(v) == 2:
        return float(v[0]), float(v[1])
    parts = [x for x in str(v).replace(";", ",").split(",") if x.strip()]
    if len(parts) != 2:
        raise ValueError("Home location: type latitude, longitude (for example 48.14, 11.58).")
    lat, lon = float(parts[0]), float(parts[1])
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        raise ValueError("Home location is out of range.")
    return lat, lon
