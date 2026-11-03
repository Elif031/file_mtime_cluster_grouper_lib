"""Group files into buckets by modification-time ranges.

Public API re-exported here so callers can ``import file_mtime_cluster_grouper_lib``
and use the names directly.
"""

from .core import (
    Bucket,
    bucket_bounds,
    bucket_key,
    group_paths,
    group_records,
    iter_files,
)

__all__ = [
    "Bucket",
    "bucket_bounds",
    "bucket_key",
    "group_paths",
    "group_records",
    "iter_files",
]
