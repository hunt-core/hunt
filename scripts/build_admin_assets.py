#!/usr/bin/env python3
"""
Download, verify and patch the vendor JS/CSS assets bundled with the Hunt admin panel.

Run via: make assets   (or directly: python scripts/build_admin_assets.py)

Every upstream file is pinned twice:

* ``SOURCES`` maps each download URL to the SHA-256 of the exact bytes
  upstream publishes for that version. A download that does not match aborts
  the build before anything is written.
* ``OUTPUTS`` maps each file under ``src/hunt/admin/static/`` to the SHA-256
  of what is committed (the upstream bytes after our patches).

Together they are the lock file for the vendored assets. ``--verify`` checks
the committed files against ``OUTPUTS`` without touching the network (the
test-suite runs this, so CI fails when a vendored file drifts), and
``--check`` re-downloads everything and confirms the build is reproducible
without writing.

To bump a dependency: change its version in ``VERSIONS``, download the new
files, review them, update the hashes in ``SOURCES`` and ``OUTPUTS``, then run
this script and ``--verify``.
"""

from __future__ import annotations

import argparse
import hashlib
import re
import sys
import urllib.request
from pathlib import Path

STATIC = Path(__file__).resolve().parent.parent / "src" / "hunt" / "admin" / "static"

VERSIONS = {
    "trix": "2.1.19",
    "font-awesome": "4.7.0",
    "easymde": "2.18.0",
}

_CDN = "https://unpkg.com"
_TRIX = f"{_CDN}/trix@{VERSIONS['trix']}/dist"
_FA = f"{_CDN}/font-awesome@{VERSIONS['font-awesome']}"
_EASYMDE = f"{_CDN}/easymde@{VERSIONS['easymde']}/dist"

# URL -> SHA-256 of the upstream bytes (before any patch).
SOURCES: dict[str, str] = {
    f"{_TRIX}/trix.css": "7a35d25d0204a594f9f89a6fef18f74321ee2dae3970da3ac11e22c4305536ac",
    f"{_TRIX}/trix.umd.min.js": "5dcaa7c434ae9f3cf1a53651ea74bfad10cd6423c72cb86a8694969aaff67e51",
    f"{_FA}/css/font-awesome.min.css": "799aeb25cc0373fdee0e1b1db7ad6c2f6a0e058dfadaa3379689f583213190bd",
    f"{_FA}/fonts/fontawesome-webfont.eot": "7bfcab6db99d5cfbf1705ca0536ddc78585432cc5fa41bbd7ad0f009033b2979",
    f"{_FA}/fonts/fontawesome-webfont.woff2": "2adefcbc041e7d18fcf2d417879dc5a09997aa64d675b7a3c4b6ce33da13f3fe",
    f"{_FA}/fonts/fontawesome-webfont.woff": "ba0c59deb5450f5cb41b3f93609ee2d0d995415877ddfa223e8a8a7533474f07",
    f"{_FA}/fonts/fontawesome-webfont.ttf": "aa58f33f239a0fb02f5c7a6c45c043d7a9ac9a093335806694ecd6d4edc0d6a8",
    f"{_FA}/fonts/fontawesome-webfont.svg": "ad6157926c1622ba4e1d03d478f1541368524bfc46f51e42fe0d945f7ef323e4",
    f"{_EASYMDE}/easymde.min.js": "42c578c29ae613807f43c292e23365f2f676071450a8f09314668a27720ccee3",
    f"{_EASYMDE}/easymde.min.css": "8a148c947f7e63250d8fb8d97e030b6fef6e02480ea08c0acfacb11618ac11f6",
}

# Path under STATIC -> SHA-256 of the committed file (after patching).
OUTPUTS: dict[str, str] = {
    "trix.css": "7a35d25d0204a594f9f89a6fef18f74321ee2dae3970da3ac11e22c4305536ac",
    "trix.umd.min.js": "5dcaa7c434ae9f3cf1a53651ea74bfad10cd6423c72cb86a8694969aaff67e51",
    "font-awesome.min.css": "5cfcdebf8f38725bebe6f58ff39a6d044806bca0220a1e0b7e3257f9ad7392d1",
    "fonts/fontawesome-webfont.eot": "7bfcab6db99d5cfbf1705ca0536ddc78585432cc5fa41bbd7ad0f009033b2979",
    "fonts/fontawesome-webfont.woff2": "2adefcbc041e7d18fcf2d417879dc5a09997aa64d675b7a3c4b6ce33da13f3fe",
    "fonts/fontawesome-webfont.woff": "ba0c59deb5450f5cb41b3f93609ee2d0d995415877ddfa223e8a8a7533474f07",
    "fonts/fontawesome-webfont.ttf": "aa58f33f239a0fb02f5c7a6c45c043d7a9ac9a093335806694ecd6d4edc0d6a8",
    "fonts/fontawesome-webfont.svg": "ad6157926c1622ba4e1d03d478f1541368524bfc46f51e42fe0d945f7ef323e4",
    "easymde.min.js": "3e94b3ea4d92532523e9e99d510f0d8207d3ac5bf784d91fc276ef3f6dc3fa06",
    "easymde.min.css": "8a148c947f7e63250d8fb8d97e030b6fef6e02480ea08c0acfacb11618ac11f6",
}

# Files under STATIC that are written by hand in this repository (or built by
# `make css`), not downloaded. They are not hash-pinned; --verify only checks
# that nothing else has appeared next to the vendored files.
FIRST_PARTY: frozenset[str] = frozenset({"admin.css", "admin.js", "admin-media.js"})

_FONT_FILES = (
    "fontawesome-webfont.eot",
    "fontawesome-webfont.woff2",
    "fontawesome-webfont.woff",
    "fontawesome-webfont.ttf",
    "fontawesome-webfont.svg",
)

# EasyMDE decides whether Font Awesome is already loaded by looking for the
# maxcdn CDN href. We serve FA locally (different href), so the check fails
# and EasyMDE injects the CDN stylesheet, violating strict CSPs. Widen the
# check to also accept any href containing "font-awesome".
_EASYMDE_FA_CHECK = 'n[i].href.indexOf("//maxcdn.bootstrapcdn.com/font-awesome/")>-1'
_EASYMDE_FA_CHECK_PATCHED = (
    '(n[i].href.indexOf("//maxcdn.bootstrapcdn.com/font-awesome/")>-1||n[i].href.indexOf("font-awesome")>-1)'
)


class BuildError(RuntimeError):
    """A download failed verification or a patch did not apply."""


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def fetch(url: str) -> bytes:
    """Download ``url`` and return its bytes only if they match the pinned hash."""
    expected = SOURCES.get(url)
    if expected is None:
        raise BuildError(f"{url} is not in SOURCES; refusing to download an unpinned file")
    if not url.startswith("https://"):
        raise BuildError(f"{url} is not an https URL")
    print(f"  fetch {url}")
    with urllib.request.urlopen(url, timeout=30) as r:
        data = r.read()
    actual = sha256(data)
    if actual != expected:
        raise BuildError(f"SHA-256 mismatch for {url}\n    expected {expected}\n    got      {actual}")
    return data


def replace_once(src: str, old: str, new: str, *, expected: int = 1, what: str) -> str:
    """``str.replace`` that fails loudly when the upstream text is not what the patch expects."""
    found = src.count(old)
    if found != expected:
        raise BuildError(f"patch '{what}' expected {expected} occurrence(s) of {old!r}, found {found}")
    result = src.replace(old, new)
    if old in result and old not in new:
        raise BuildError(f"patch '{what}' left unpatched text behind")
    return result


def patch_font_awesome_css(raw: bytes) -> bytes:
    # The original has `../fonts/`, which resolves incorrectly when the file is
    # served at <prefix>/assets/font-awesome.min.css. Point at `fonts/` instead.
    css = raw.decode("utf-8")
    css = replace_once(css, "../fonts/", "fonts/", expected=6, what="font-awesome font paths")
    return css.encode("utf-8")


def patch_easymde_js(raw: bytes) -> bytes:
    js = raw.decode("utf-8")
    js = replace_once(js, _EASYMDE_FA_CHECK, _EASYMDE_FA_CHECK_PATCHED, what="easymde font-awesome detection")
    return js.encode("utf-8")


def patch_easymde_css(raw: bytes) -> bytes:
    # Older EasyMDE stylesheets @import Font Awesome from maxcdn, which violates
    # strict CSPs. Strip any such import and, whether or not one was present,
    # require that the result pulls in nothing external.
    css = raw.decode("utf-8")
    css = re.sub(r"@import\s+url\([^)]*font-awesome[^)]*\)\s*;?", "", css, flags=re.IGNORECASE)
    if "@import" in css:
        raise BuildError("easymde.min.css still contains an @import after patching")
    if re.search(r"url\(\s*['\"]?(?:https?:)?//", css, flags=re.IGNORECASE):
        raise BuildError("easymde.min.css references an external URL")
    return css.encode("utf-8")


def build() -> dict[str, bytes]:
    """Download, verify and patch everything. Returns {path under STATIC: bytes}; writes nothing."""
    out: dict[str, bytes] = {}

    print(f"Trix {VERSIONS['trix']}…")
    out["trix.css"] = fetch(f"{_TRIX}/trix.css")
    out["trix.umd.min.js"] = fetch(f"{_TRIX}/trix.umd.min.js")

    print(f"Font Awesome {VERSIONS['font-awesome']} (used by the EasyMDE toolbar)…")
    out["font-awesome.min.css"] = patch_font_awesome_css(fetch(f"{_FA}/css/font-awesome.min.css"))
    for fname in _FONT_FILES:
        out[f"fonts/{fname}"] = fetch(f"{_FA}/fonts/{fname}")

    print(f"EasyMDE {VERSIONS['easymde']}…")
    out["easymde.min.js"] = patch_easymde_js(fetch(f"{_EASYMDE}/easymde.min.js"))
    out["easymde.min.css"] = patch_easymde_css(fetch(f"{_EASYMDE}/easymde.min.css"))

    if set(out) != set(OUTPUTS):
        raise BuildError("OUTPUTS does not list exactly the files this script builds")
    for rel, data in out.items():
        actual = sha256(data)
        if actual != OUTPUTS[rel]:
            raise BuildError(f"built {rel} does not match OUTPUTS\n    expected {OUTPUTS[rel]}\n    got      {actual}")
    return out


def verify_committed(static: Path = STATIC) -> list[str]:
    """Compare the committed files with OUTPUTS. Offline. Returns a list of problems (empty = ok)."""
    problems: list[str] = []
    for rel, expected in OUTPUTS.items():
        path = static / rel
        if not path.is_file():
            problems.append(f"{rel}: missing")
            continue
        actual = sha256(path.read_bytes())
        if actual != expected:
            problems.append(f"{rel}: expected {expected}, found {actual}")
    if static.is_dir():
        for path in sorted(static.rglob("*")):
            rel = path.relative_to(static).as_posix()
            if path.is_file() and rel not in OUTPUTS and rel not in FIRST_PARTY:
                problems.append(f"{rel}: not pinned in OUTPUTS and not listed in FIRST_PARTY")
    return problems


def _display(path: Path) -> str:
    try:
        return str(path.relative_to(Path.cwd()))
    except ValueError:
        return str(path)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.strip().splitlines()[0])
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--verify", action="store_true", help="check the committed files against the pinned hashes (no network)"
    )
    mode.add_argument(
        "--check", action="store_true", help="download and build, compare with the committed files, write nothing"
    )
    args = parser.parse_args(argv)

    if args.verify:
        problems = verify_committed()
        for problem in problems:
            print(f"  MISMATCH {problem}", file=sys.stderr)
        if problems:
            return 1
        print(f"OK: {len(OUTPUTS)} vendored admin assets match their pinned hashes.")
        return 0

    try:
        built = build()
    except BuildError as exc:
        print(f"\nABORTED: {exc}", file=sys.stderr)
        return 1

    if args.check:
        drift = [
            rel for rel, data in built.items() if not (STATIC / rel).is_file() or (STATIC / rel).read_bytes() != data
        ]
        for rel in drift:
            print(f"  DIFFERS {rel}", file=sys.stderr)
        if drift:
            return 1
        print("OK: the build reproduces the committed files byte for byte.")
        return 0

    # Nothing is written until every download and patch has been verified.
    for rel, data in built.items():
        path = STATIC / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        print(f"  wrote {_display(path)}")

    print("\nDone. Run `make css` to rebuild admin.css from Tailwind.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
