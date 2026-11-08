"""Start Photo Sorter:  python3 -m photosorter"""

from __future__ import annotations

import argparse
import sys
import webbrowser

from . import __version__


def main() -> int:
    ap = argparse.ArgumentParser(prog="photosorter")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--no-browser", action="store_true", help="do not open the browser")
    args = ap.parse_args()

    if sys.version_info < (3, 9):
        print("Photo Sorter needs Python 3.9 or newer. Install Python from python.org.")
        return 1
    from .exiftool import ExifToolError
    from .server import make_server
    try:
        httpd, url, procs = make_server(args.port)
    except ExifToolError as e:
        print(f"\n  {e}\n")
        return 1

    print(f"\n  Photo Sorter {__version__} is running on this computer only.")
    print(f"  If the browser does not open, copy this link into it:\n\n    {url}\n")
    print("  To stop: press Quit in the browser, or Ctrl+C here.\n")
    if not args.no_browser:
        webbrowser.open(url)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
        for p in procs:
            p.close()
    print("  Photo Sorter stopped.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
