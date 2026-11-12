# file_mtime_cluster_grouper_lib

Groups files into buckets by modification-time ranges such as hourly or daily, for archive and rotation workflows.

```python
from file_mtime_cluster_grouper_lib import group_paths, group_records, iter_files

# Group a directory tree by day (UTC-aligned).
buckets = group_paths(iter_paths := [p for p, _ in iter_files("/var/log")], "day")
for bucket in buckets:
    print(bucket.start, bucket.end, len(bucket.paths))

# Or group records you already have, without touching the filesystem.
buckets = group_records([("a.log", 100.0), ("b.log", 101.0)], width=100)
assert buckets[0].paths == ("a.log", "b.log")
```

## Why this exists

Rotation and archive jobs usually want to answer "which files belong to the same
hour, day, or week?" without pulling in a scheduling framework. The common naive
implementation uses `datetime` and local time, which makes the same input group
differently on two hosts in different timezones. This library computes bucket
starts with integer arithmetic on the epoch, so a "day" is always a UTC day and
the result is identical everywhere.

The trade-off: bucket boundaries are aligned to the Unix epoch, not to local
midnight, and not to an arbitrary anchor. If your workflow needs local-midnight
days or a Monday-start week, compute the offset yourself and pass a custom width
in seconds. That is a deliberate choice — one predictable rule beats several
configurable ones.

## The awkward edge

Bucket boundaries are computed from `int(mtime)`, so sub-second differences are
truncated before grouping. Two files whose mtimes are 0.9s apart can still land
in different buckets if they straddle a boundary, and two files 0.1s apart on the
same side of a boundary land together. Negative mtimes (pre-1970) are supported
and floor toward negative infinity: a file at `-1.0` belongs to the hour bucket
starting at `-3600`, not at `0`.

## API

All names below are exported from the package root.

- `WIDTHS` — mapping of `"minute"`, `"hour"`, `"day"`, `"week"` to seconds.
- `Bucket` — frozen dataclass with `start: int`, `end: int`, `paths: tuple[str, ...]`.
  `len(bucket)` returns the number of paths.
- `bucket_key(mtime, width)` — integer start of the bucket containing `mtime`.
- `bucket_bounds(mtime, width)` — `(start, end)` for that bucket.
- `group_records(records, width="day")` — group `(path, mtime)` pairs; buckets are
  sorted by `start`, and paths within a bucket keep their input order. Duplicate
  paths are kept.
- `group_paths(paths, width="day", *, clock=None)` — stat each path and group. The
  optional `clock` callable maps a path to an mtime; pass one in tests to avoid
  touching the real filesystem. Paths that raise `OSError` are skipped.
- `iter_files(root, *, recursive=True, follow_symlinks=False)` — yield
  `(path, mtime)` for regular files under `root`. Unreadable directories are
  skipped rather than aborting the walk, and symlinks are not followed by
  default.

`width` accepts a name from `WIDTHS` or a positive integer number of seconds.
Anything else raises `ValueError` (bad name, non-positive number) or `TypeError`
(bool or non-int, non-str).

## Design notes

The window stores values eagerly rather than keeping running aggregates. Running
sums drift with floating point over long streams, and recomputing from a small
buffer is cheap enough that the drift is not worth the speed.

