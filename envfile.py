#!/usr/bin/env python3
"""envfile.py - read a .env file into the environment.

The README and docs tell you to `copy .env.example .env`, so something has to
read it. No dependency needed: KEY=VALUE lines, `#` comments, optional quotes,
and `export ` prefixes are accepted. Real environment variables always win, so
a launcher .bat still overrides the file.
"""

from __future__ import annotations

import os
from pathlib import Path


def load(path: str | Path = ".env") -> int:
    """Load `path` into os.environ without overriding existing values.

    Returns the number of keys set. A missing file is not an error.
    """
    p = Path(path)
    if not p.is_file():
        return 0

    count = 0
    try:
        text = p.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return 0

    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export "):].lstrip()
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if not key:
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        if key not in os.environ:
            os.environ[key] = value
            count += 1
    return count


if __name__ == "__main__":
    n = load()
    print(f"{n} key(s) loaded from .env")
