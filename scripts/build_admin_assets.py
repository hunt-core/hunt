#!/usr/bin/env python3
"""
Download and patch vendor JS/CSS assets for the Hunt admin panel.

Run via: make assets   (or directly: python scripts/build_admin_assets.py)
"""

from __future__ import annotations

import re
import sys
import urllib.request
from pathlib import Path

STATIC = Path(__file__).parent.parent / "src" / "hunt" / "admin" / "static"
FONTS = STATIC / "fonts"


def fetch(url: str) -> bytes:
    print(f"  fetch {url}")
    with urllib.request.urlopen(url, timeout=30) as r:  # noqa: S310
        return r.read()


def write(path: Path, data: bytes | str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(data, str):
        path.write_text(data, encoding="utf-8")
    else:
        path.write_bytes(data)
    print(f"  wrote {path.relative_to(Path.cwd())}")


# ---------------------------------------------------------------------------
# Trix 2.1.10
# ---------------------------------------------------------------------------
print("Trix…")
write(STATIC / "trix.css",         fetch("https://unpkg.com/trix@2.1.10/dist/trix.css"))
write(STATIC / "trix.umd.min.js",  fetch("https://unpkg.com/trix@2.1.10/dist/trix.umd.min.js"))

# ---------------------------------------------------------------------------
# Font Awesome 4.7.0 (used by EasyMDE toolbar)
# ---------------------------------------------------------------------------
print("Font Awesome…")
fa_css_raw = fetch("https://unpkg.com/font-awesome@4.7.0/css/font-awesome.min.css").decode()
# Patch font paths: original has `../fonts/` which would resolve incorrectly
# when served at /hunt-admin/assets/font-awesome.min.css.
# Change to `fonts/` (relative → same directory level).
fa_css_patched = fa_css_raw.replace("../fonts/", "fonts/")
write(STATIC / "font-awesome.min.css", fa_css_patched)

for fname in (
    "fontawesome-webfont.eot",
    "fontawesome-webfont.woff2",
    "fontawesome-webfont.woff",
    "fontawesome-webfont.ttf",
    "fontawesome-webfont.svg",
):
    write(FONTS / fname, fetch(f"https://unpkg.com/font-awesome@4.7.0/fonts/{fname}"))

# ---------------------------------------------------------------------------
# EasyMDE 2.18.0
# ---------------------------------------------------------------------------
print("EasyMDE…")
write(STATIC / "easymde.min.js", fetch("https://unpkg.com/easymde@2.18.0/dist/easymde.min.js"))

easymde_css_raw = fetch("https://unpkg.com/easymde@2.18.0/dist/easymde.min.css").decode()
# EasyMDE's CSS imports Font Awesome from maxcdn.bootstrapcdn.com which violates
# strict CSPs. Remove the @import and serve FA ourselves.
easymde_css_patched = re.sub(
    r'@import\s+url\([^)]*font-awesome[^)]*\)\s*;?',
    "",
    easymde_css_raw,
    flags=re.IGNORECASE,
)
write(STATIC / "easymde.min.css", easymde_css_patched)

print("\nDone. Run `make css` to rebuild admin.css from Tailwind.")
sys.exit(0)
