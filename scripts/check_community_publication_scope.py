#!/usr/bin/env python3
"""Fail closed unless a community recommendation publication stages only community artifacts."""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path
from typing import Iterable

ROOT = Path(__file__).resolve().parents[1]
COMMUNITY_EXPORT = "exports/d2wd_community.txt"
CURATED_PATHS = ("recommendations/curated", "exports/d2wd_curated.txt")
TARGET_RE = re.compile(r"^recommendations/community/(pve|pvp)/[a-z0-9]+(?:-[a-z0-9]+)*_s[0-9]+(?:-[0-9]+)?\.json$")
COMMUNITY_JSON_RE = TARGET_RE


class PublicationScopeError(ValueError):
    """Raised when publication would include data outside the community write set."""


def validate_publication_scope(
    issue_number: int,
    target_path: str,
    staged_paths: Iterable[str],
    *,
    tracked_curated_changes: Iterable[str] = (),
    untracked_curated_paths: Iterable[str] = (),
    target_exists_in_head: bool = False,
    target_unchanged_from_head: bool = False,
) -> None:
    """Validate the staged write set and prove the curated area was untouched."""
    if not isinstance(issue_number, int) or isinstance(issue_number, bool) or issue_number < 1:
        raise PublicationScopeError("issue_number must be a positive integer.")

    target_match = TARGET_RE.fullmatch(target_path)
    if target_match is None:
        raise PublicationScopeError(
            "target_path must be a safe community JSON path: recommendations/community/pve|pvp/<exact-seasonal-weapon-id>.json."
        )

    curated_changes = sorted(set(tracked_curated_changes) | set(untracked_curated_paths))
    if curated_changes:
        formatted = "\n".join(f"- {path}" for path in curated_changes)
        raise PublicationScopeError(
            "Refusing to publish because curated files changed:\n" + formatted
        )

    staged = sorted(set(staged_paths))
    for path in staged:
        if path == COMMUNITY_EXPORT:
            continue
        match = COMMUNITY_JSON_RE.fullmatch(path)
        if match is None or path != target_path:
            raise PublicationScopeError(
                f"Refusing to stage {path!r}; only the target weapon/mode community JSON and "
                f"{COMMUNITY_EXPORT!r} may be committed."
            )

    # A new or modified publication record must be included. A no-op rerun is
    # permitted only when the same target already exists unchanged in HEAD.
    if target_path not in staged and not (target_exists_in_head and target_unchanged_from_head):
        raise PublicationScopeError(
            "The target community JSON is not staged and is not an unchanged, already-published record."
        )


def _git_output(root: Path, *args: str) -> list[str]:
    completed = subprocess.run(
        ["git", *args], cwd=root, check=True, capture_output=True, text=True
    )
    return [line for line in completed.stdout.splitlines() if line]


def _git_returncode(root: Path, *args: str) -> int:
    return subprocess.run(
        ["git", *args], cwd=root, capture_output=True, text=True, check=False
    ).returncode


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--issue-number", type=int, required=True)
    parser.add_argument("--target-path", required=True)
    parser.add_argument("--root", type=Path, default=ROOT, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    root = args.root.resolve()

    try:
        tracked_curated_changes = _git_output(
            root, "diff", "--name-only", "HEAD", "--", *CURATED_PATHS
        )
        untracked_curated_paths = _git_output(
            root, "ls-files", "--others", "--exclude-standard", "--", *CURATED_PATHS
        )
        staged_paths = _git_output(root, "diff", "--cached", "--name-only")
        target_exists_in_head = _git_returncode(root, "cat-file", "-e", f"HEAD:{args.target_path}") == 0
        target_unchanged_from_head = _git_returncode(root, "diff", "--quiet", "HEAD", "--", args.target_path) == 0

        validate_publication_scope(
            args.issue_number,
            args.target_path,
            staged_paths,
            tracked_curated_changes=tracked_curated_changes,
            untracked_curated_paths=untracked_curated_paths,
            target_exists_in_head=target_exists_in_head,
            target_unchanged_from_head=target_unchanged_from_head,
        )
    except (OSError, subprocess.CalledProcessError, PublicationScopeError) as exc:
        print(f"Community publication scope check failed: {exc}", file=sys.stderr)
        return 1

    print("Community publication write set is safe; curated JSON and wishlist are unchanged.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
