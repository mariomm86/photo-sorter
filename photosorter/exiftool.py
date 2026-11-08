"""Thin wrapper around a long-running ExifTool process (-stay_open mode).

One process is reused for many commands, which avoids starting Perl for every
photo (about 0.2 s each on an older Mac).
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import threading
from pathlib import Path

_CANDIDATES = [
    "/usr/local/bin/exiftool",      # macOS .pkg installer
    "/opt/homebrew/bin/exiftool",
    "/usr/bin/exiftool",
]


def find_exiftool() -> str | None:
    env = os.environ.get("PHOTOSORTER_EXIFTOOL")
    if env and Path(env).exists():
        return env
    found = shutil.which("exiftool")
    if found:
        return found
    for c in _CANDIDATES:
        if Path(c).exists():
            return c
    return None


class ExifToolError(RuntimeError):
    pass


class ExifTool:
    def __init__(self, path: str | None = None) -> None:
        self.path = path or find_exiftool()
        if not self.path:
            raise ExifToolError(
                "ExifTool was not found. Install it from https://exiftool.org (macOS package)."
            )
        self._lock = threading.Lock()
        self._proc: subprocess.Popen | None = None
        self._stderr: list[str] = []

    def _start(self) -> None:
        self._proc = subprocess.Popen(
            [self.path, "-stay_open", "True", "-@", "-", "-common_args", "-charset", "filename=utf8"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        t = threading.Thread(target=self._drain_stderr, args=(self._proc,), daemon=True)
        t.start()

    def _drain_stderr(self, proc: subprocess.Popen) -> None:
        for line in iter(proc.stderr.readline, b""):
            self._stderr.append(line.decode("utf-8", "replace").rstrip())
            if len(self._stderr) > 500:
                del self._stderr[:250]

    def version(self) -> str:
        out = subprocess.run([self.path, "-ver"], capture_output=True, text=True, timeout=30)
        return out.stdout.strip()

    def execute(self, *args: str) -> bytes:
        """Run one ExifTool command and return its raw stdout."""
        with self._lock:
            if self._proc is None or self._proc.poll() is not None:
                self._start()
            assert self._proc and self._proc.stdin and self._proc.stdout
            payload = "\n".join(str(a) for a in args) + "\n-execute\n"
            self._proc.stdin.write(payload.encode("utf-8"))
            self._proc.stdin.flush()
            fd = self._proc.stdout.fileno()
            buf = bytearray()
            marker = b"{ready}\n"
            while True:
                chunk = os.read(fd, 65536)
                if not chunk:
                    raise ExifToolError("ExifTool stopped unexpectedly")
                buf += chunk
                if buf.endswith(marker) or buf.endswith(b"{ready}\r\n"):
                    break
            end = buf.rfind(b"{ready}")
            return bytes(buf[:end])

    def read_json(self, files: list[str], tags: list[str], extra: list[str] | None = None) -> list[dict]:
        if not files:
            return []
        args = ["-json", "-n", *(extra or []), *[f"-{t}" for t in tags], *files]
        out = self.execute(*args)
        text = out.decode("utf-8", "replace").strip()
        if not text:
            return []
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            # A warning line can precede the JSON; cut to the first '['.
            i = text.find("[")
            return json.loads(text[i:]) if i >= 0 else []

    def recent_errors(self) -> list[str]:
        return list(self._stderr[-20:])

    def close(self) -> None:
        with self._lock:
            if self._proc and self._proc.poll() is None:
                try:
                    self._proc.stdin.write(b"-stay_open\nFalse\n")
                    self._proc.stdin.flush()
                    self._proc.wait(timeout=5)
                except Exception:
                    self._proc.kill()
            self._proc = None
