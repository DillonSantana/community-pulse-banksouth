"""Fail a pull request that silently drops something already on the base branch.

The hub is one large index.html, so parallel PRs collide in the same file. A
conflict resolved by keeping one side deletes the other side's work. That's how
the County signals page was lost once. This check compares the PR's index.html
against the base branch's copy:

  - no leftover conflict markers in any tracked text file
  - every page (data-page), nav item (data-nav) and element id on the base still
    exists in the PR
  - every top-level JavaScript function on the base still exists in the PR

Removing something on purpose is fine. Say so in the PR, and a reviewer can merge
anyway (this check doesn't block merging unless branch protection makes it
required).

Usage: python check_dropped_features.py <base-ref>      (e.g. origin/main)
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

FILES = ["index.html"]
MARKER = re.compile(r"^(<{7}|>{7})( |$)|^={7}$", re.M)
PATTERNS = {
    "page": re.compile(r'data-page="([\w-]+)"'),
    "nav item": re.compile(r'data-nav="([\w-]+)"'),
    "element id": re.compile(r'\sid="([A-Za-z][\w-]*)"'),
    "function": re.compile(r"^function\s+([A-Za-z_$][\w$]*)\s*\(", re.M),
}


def git(*args: str) -> str:
    return subprocess.run(["git", *args], check=True, capture_output=True, text=True).stdout


def features(html: str) -> dict[str, set[str]]:
    return {kind: set(rx.findall(html)) for kind, rx in PATTERNS.items()}


def main(base: str) -> int:
    problems: list[str] = []

    for path in git("ls-files").splitlines():
        p = Path(path)
        if p.suffix.lower() not in {".html", ".js", ".css", ".py", ".md", ".json", ".yml", ".yaml", ".txt", ".csv"}:
            continue
        try:
            text = p.read_text(errors="ignore")
        except OSError:
            continue
        for m in MARKER.finditer(text):
            line = text.count("\n", 0, m.start()) + 1
            problems.append(f"{path}:{line}: leftover merge conflict marker")

    for path in FILES:
        try:
            old = git("show", f"{base}:{path}")
        except subprocess.CalledProcessError:
            continue                      # file is new in this PR
        new = Path(path).read_text() if Path(path).exists() else ""
        before, after = features(old), features(new)
        for kind in PATTERNS:
            for name in sorted(before[kind] - after[kind]):
                problems.append(f"{path}: {kind} '{name}' exists on {base} but is missing here")

    if problems:
        print("This PR removes things that exist on the base branch:\n")
        for p in problems:
            print(f"  - {p}")
        print("\nIf a conflict was resolved by taking one side, the other side's work was "
              "dropped. Re-resolve keeping both. If the removal is intentional, say so in "
              "the PR description so the reviewer can merge anyway.")
        return 1
    print(f"OK: nothing from {base} was dropped.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else "origin/main"))
