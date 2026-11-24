#!/usr/bin/env python3
"""Point the maude.exe download links in docs/ at a release tag.

Usage: docs-set-release.py <tag> [docs_dir]

Rewrites, in every .md under docs_dir:
  .../dirkpetersen/maude/releases/download/vX.Y.Z/maude.exe[.sha256]
  link text "Download maude.exe (vX.Y.Z)"
Only exact vMAJOR.MINOR.MINOR tags are accepted; anything else is a no-op
(exit 0) so a docs build never breaks.
"""
import pathlib
import re
import sys

SEMVER = r"v[0-9]+\.[0-9]+\.[0-9]+"
URL = re.compile(r"(dirkpetersen/maude/releases/download/)" + SEMVER + r"(/maude\.exe)")
TEXT = re.compile(r"(Download maude\.exe \()" + SEMVER + r"(\))")


def main() -> int:
    tag = sys.argv[1].strip() if len(sys.argv) > 1 else ""
    root = pathlib.Path(sys.argv[2] if len(sys.argv) > 2 else "docs")
    if not re.fullmatch(SEMVER, tag):
        print(f"docs-set-release: no usable tag ({tag!r}), links unchanged")
        return 0
    changed = 0
    for p in sorted(root.rglob("*.md")):
        s = p.read_text(encoding="utf-8")
        n = URL.sub(lambda m: m.group(1) + tag + m.group(2), s)
        n = TEXT.sub(lambda m: m.group(1) + tag + m.group(2), n)
        if n != s:
            p.write_text(n, encoding="utf-8", newline="")
            changed += 1
            print(f"docs-set-release: {p} -> {tag}")
    print(f"docs-set-release: {changed} file(s) updated to {tag}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
