import os
import tempfile
import unittest

from file_mtime_cluster_grouper_lib import (
    Bucket,
    bucket_bounds,
    bucket_key,
    group_paths,
    group_records,
    iter_files,
)


class BucketKeyTests(unittest.TestCase):
    def test_named_widths_align_to_epoch(self):
        # 2021-01-01T00:00:00Z is exactly divisible by a day.
        base = 1609459200
        self.assertEqual(bucket_key(base, "day"), base)
        self.assertEqual(bucket_key(base + 86399, "day"), base)
        self.assertEqual(bucket_key(base + 86400, "day"), base + 86400)

    def test_hourly_boundaries(self):
        self.assertEqual(bucket_key(0, "hour"), 0)
        self.assertEqual(bucket_key(3599.9, "hour"), 0)
        self.assertEqual(bucket_key(3600.0, "hour"), 3600)

    def test_minute_and_week(self):
        self.assertEqual(bucket_key(125, "minute"), 120)
        self.assertEqual(bucket_key(0, "week"), 0)
        self.assertEqual(bucket_key(604800, "week"), 604800)

    def test_negative_mtime_floors_toward_negative_infinity(self):
        # -1s belongs to the hour starting at -3600, not at 0.
        self.assertEqual(bucket_key(-1, "hour"), -3600)
        self.assertEqual(bucket_key(-3600, "hour"), -3600)
        self.assertEqual(bucket_key(-3601, "hour"), -7200)

    def test_custom_second_width(self):
        self.assertEqual(bucket_key(17, 10), 10)
        self.assertEqual(bucket_key(20, 10), 20)

    def test_invalid_widths_raise(self):
        with self.assertRaises(ValueError):
            bucket_key(0, "fortnight")
        with self.assertRaises(ValueError):
            bucket_key(0, 0)
        with self.assertRaises(ValueError):
            bucket_key(0, -5)
        with self.assertRaises(TypeError):
            bucket_key(0, True)
        with self.assertRaises(TypeError):
            bucket_key(0, 1.5)

    def test_bucket_bounds(self):
        self.assertEqual(bucket_bounds(0, "day"), (0, 86400))
        self.assertEqual(bucket_bounds(86400, "day"), (86400, 172800))
        self.assertEqual(bucket_bounds(-1, "hour"), (-3600, 0))


class GroupRecordsTests(unittest.TestCase):
    def test_groups_sorted_by_start_and_preserves_order(self):
        records = [
            ("b", 100.0),
            ("a", 50.0),
            ("c", 101.0),
            ("d", 200.0),
        ]
        buckets = group_records(records, 100)
        self.assertEqual([b.start for b in buckets], [0, 100, 200])
        self.assertEqual(buckets[0].paths, ("a",))
        self.assertEqual(buckets[1].paths, ("b", "c"))
        self.assertEqual(buckets[2].paths, ("d",))

    def test_empty_input_yields_no_buckets(self):
        self.assertEqual(group_records([], "day"), [])

    def test_duplicate_paths_are_kept(self):
        records = [("x", 1.0), ("x", 2.0)]
        buckets = group_records(records, 10)
        self.assertEqual(len(buckets), 1)
        self.assertEqual(buckets[0].paths, ("x", "x"))

    def test_bucket_length_and_end(self):
        buckets = group_records([("p", 5.0)], 10)
        self.assertEqual(len(buckets[0]), 1)
        self.assertEqual(buckets[0].end, 10)
        self.assertEqual(buckets[0].start, 0)

    def test_bucket_is_frozen(self):
        bucket = Bucket(start=0, end=1, paths=("a",))
        with self.assertRaises(Exception):
            bucket.start = 5  # type: ignore[misc]


class GroupPathsTests(unittest.TestCase):
    def test_uses_injected_clock(self):
        times = {"/a": 10.0, "/b": 11.0, "/c": 25.0}
        buckets = group_paths(["/a", "/b", "/c"], 10, clock=times.__getitem__)
        self.assertEqual([b.start for b in buckets], [10, 20])
        self.assertEqual(buckets[0].paths, ("/a", "/b"))
        self.assertEqual(buckets[1].paths, ("/c",))

    def test_missing_paths_are_skipped(self):
        def clock(path):
            if path == "/missing":
                raise FileNotFoundError(path)
            return 42.0

        buckets = group_paths(["/missing", "/ok"], "hour", clock=clock)
        self.assertEqual(len(buckets), 1)
        self.assertEqual(buckets[0].paths, ("/ok",))

    def test_default_clock_uses_real_stat(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = os.path.join(tmp, "file.txt")
            with open(target, "w", encoding="utf-8") as handle:
                handle.write("x")
            os.utime(target, (1000.0, 1000.0))
            buckets = group_paths([target], 100)
            self.assertEqual(len(buckets), 1)
            self.assertEqual(buckets[0].paths, (target,))
            self.assertEqual(buckets[0].start, 1000)


class IterFilesTests(unittest.TestCase):
    def _make_tree(self, tmp):
        top = os.path.join(tmp, "top.txt")
        with open(top, "w", encoding="utf-8") as handle:
            handle.write("top")
        sub = os.path.join(tmp, "sub")
        os.mkdir(sub)
        nested = os.path.join(sub, "nested.txt")
        with open(nested, "w", encoding="utf-8") as handle:
            handle.write("nested")
        return top, nested

    def test_recursive_finds_nested(self):
        with tempfile.TemporaryDirectory() as tmp:
            top, nested = self._make_tree(tmp)
            found = {path for path, _ in iter_files(tmp, recursive=True)}
            self.assertEqual(found, {top, nested})

    def test_non_recursive_skips_subdirectories(self):
        with tempfile.TemporaryDirectory() as tmp:
            top, nested = self._make_tree(tmp)
            found = {path for path, _ in iter_files(tmp, recursive=False)}
            self.assertEqual(found, {top})

    def test_mtimes_are_floats(self):
        with tempfile.TemporaryDirectory() as tmp:
            top, _ = self._make_tree(tmp)
            os.utime(top, (500.0, 500.0))
            by_path = dict(iter_files(tmp, recursive=False))
            self.assertIsInstance(by_path[top], float)
            self.assertAlmostEqual(by_path[top], 500.0, places=3)

    def test_missing_root_yields_nothing(self):
        self.assertEqual(list(iter_files("/definitely/not/here")), [])

    def test_symlink_skipped_by_default(self):
        with tempfile.TemporaryDirectory() as tmp:
            top, _ = self._make_tree(tmp)
            link = os.path.join(tmp, "link.txt")
            try:
                os.symlink(top, link)
            except (OSError, NotImplementedError):
                self.skipTest("symlinks unavailable")
            found = {path for path, _ in iter_files(tmp, recursive=False)}
            self.assertNotIn(link, found)
            self.assertIn(top, found)


if __name__ == "__main__":
    unittest.main()
