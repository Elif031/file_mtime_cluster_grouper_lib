"""Core grouping logic for file mtime clustering.

Design decisions worth stating up front, because they are the ones a reader
will hit:

* Buckets are aligned to the epoch (UTC), not to local midnight. A "day"
bucket therefore starts at 00:00:00 UTC. This keeps grouping deterministic
regardless of the machine's timezone, which matters for archive/rotation
workflows that run across hosts.

* Bucket boundaries are computed with integer arithmetic on ``int(mtime)``.
Files whose mtimes differ by less than a second can still land in different
buckets if they straddle a boundary; that is the correct, documented
behaviour rather than a bug.

* Negative mtimes (pre-1970) are supported. Python's ``//`` floors toward
negative infinity, which is what we want: ``-1 // 3600 == -1`` so a file at
-1s falls into the bucket starting at -3600s.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Callable, Dict, Iterable, Iterator, List, Optional, Sequence, Tuple

# Supported bucket widths in seconds. Named so callers do not have to guess
# what "hourly" means numerically.
WIDTHS: Dict[str, int] = {
    "minute": 60,
    "hour": 3600,
    "day": 86400,
    "week": 604800,
}


@dataclass(frozen=True)
class Bucket:
    """A half-open time range ``[start, end)`` with the paths that fall in it.

    ``start`` and ``end`` are integer epoch seconds. ``paths`` preserves the
    order in which the input was supplied, so callers that sort beforehand get
    a stable output order for free.
    """

    start: int
    end: int
    paths: Tuple[str, ...]

    def __len__(self) -> int:
        return len(self.paths)


def _resolve_width(width: object) -> int:
    """Turn a width name or number into a positive number of seconds."""
    if isinstance(width, str):
        try:
            seconds = WIDTHS[width]
        except KeyError:
            raise ValueError(
                "unknown width %r; expected one of %s or a positive int"
                % (width, sorted(WIDTHS))
            ) from None
    elif isinstance(width, bool):
        # bool is a subclass of int; reject it explicitly so True does not
        # silently mean "1 second".
        raise TypeError("width must be a name or int, not bool")
    elif isinstance(width, int):
        seconds = width
    else:
        raise TypeError("width must be a name or int, got %r" % (type(width).__name__,))

    if seconds <= 0:
        raise ValueError("width must be positive, got %r" % (seconds,))
    return seconds


def bucket_key(mtime: float, width: object) -> int:
    """Return the integer start of the bucket containing ``mtime``.

    ``width`` is either a name in :data:`WIDTHS` or a positive number of
    seconds. Alignment is to the Unix epoch in UTC; see the module docstring.
    """
    seconds = _resolve_width(width)
    return (int(mtime) // seconds) * seconds


def bucket_bounds(mtime: float, width: object) -> Tuple[int, int]:
    """Return ``(start, end)`` for the bucket containing ``mtime``."""
    seconds = _resolve_width(width)
    start = (int(mtime) // seconds) * seconds
    return start, start + seconds


def group_records(
    records: Iterable[Tuple[str, float]],
    width: object = "day",
) -> List[Bucket]:
    """Group ``(path, mtime)`` pairs into buckets ordered by start time.

    Records with the same path are kept as separate entries; deduplication is
    the caller's job because only the caller knows whether two records for the
    same path mean "same file" or "two versions".
    """
    seconds = _resolve_width(width)
    collected: Dict[int, List[str]] = {}
    for path, mtime in records:
        start = (int(mtime) // seconds) * seconds
        collected.setdefault(start, []).append(path)
    return [
        Bucket(start=start, end=start + seconds, paths=tuple(collected[start]))
        for start in sorted(collected)
    ]


def iter_files(
    root: str,
    *,
    recursive: bool = True,
    follow_symlinks: bool = False,
) -> Iterator[Tuple[str, float]]:
    """Yield ``(path, mtime)`` for regular files under ``root``.

    ``os.scandir`` is used instead of ``os.walk`` so a single unreadable
    directory does not abort the whole traversal; such entries are skipped.
    Symlinks are not followed by default, which matches the usual archive
    expectation that a link is not a second copy of its target.
    """
    stack: List[str] = [root]
    while stack:
        current = stack.pop()
        try:
            entries = list(os.scandir(current))
        except OSError:
            # Permission errors, races with deletion, etc. Skip the directory
            # rather than failing the entire grouping pass.
            continue
        for entry in entries:
            try:
                if entry.is_symlink() and not follow_symlinks:
                    continue
                if entry.is_dir(follow_symlinks=follow_symlinks):
                    if recursive:
                        stack.append(entry.path)
                    continue
                if not entry.is_file(follow_symlinks=follow_symlinks):
                    continue
                stat = entry.stat(follow_symlinks=follow_symlinks)
            except OSError:
                continue
            yield entry.path, stat.st_mtime


def group_paths(
    paths: Iterable[str],
    width: object = "day",
    *,
    clock: Optional[Callable[[str], float]] = None,
) -> List[Bucket]:
    """Stat each path and group the results into buckets.

    ``clock`` is an injectable ``path -> mtime`` callable. It exists so tests
    (and callers with a virtual filesystem) can supply deterministic times
    without touching the real clock. When omitted, ``os.stat().st_mtime`` is
    used. Paths that cannot be stat'ed are skipped.
    """
    getter = clock if clock is not None else _default_clock
    records: List[Tuple[str, float]] = []
    for path in paths:
        try:
            mtime = getter(path)
        except OSError:
            continue
        records.append((path, mtime))
    return group_records(records, width)


def _default_clock(path: str) -> float:
    return os.stat(path).st_mtime
