"""Tests for chunk-table maintenance: version cleanup, index retraining, and
reads that survive maintenance run by another process."""

import random
import warnings
from datetime import timedelta

import lancedb
import pytest

from lgrep.storage import CHUNKS_TABLE, EMBEDDING_DIM, ChunkStore, _chunk_store, optimize_table
from lgrep.tools.prune_orphans import optimize_caches
from tests.test_storage import make_chunk

# Deletes every superseded version, as a run long after the writes would.
_NO_RETENTION = timedelta(0)


def _random_vector(rng: random.Random) -> list[float]:
    return [rng.uniform(-1, 1) for _ in range(EMBEDDING_DIM)]


def _fill(store: ChunkStore, batches: int, per_batch: int = 1, start: int = 0) -> None:
    """Add rows in separate writes so each leaves a table version."""
    rng = random.Random(start)
    for b in range(batches):
        store.add_chunks(
            [
                make_chunk(
                    file_path=f"f{start + b}.py",
                    chunk_index=i,
                    vector=_random_vector(rng),
                )
                for i in range(per_batch)
            ]
        )


def _optimize(db_path, **kwargs):
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        return optimize_table(db_path, retention=_NO_RETENTION, **kwargs)


def _versions(db_path) -> int:
    return len(lancedb.connect(str(db_path)).open_table(CHUNKS_TABLE).list_versions())


class TestOptimizeTable:
    def test_deletes_superseded_versions_and_keeps_rows(self, tmp_path):
        db = tmp_path / "db"
        store = ChunkStore(db)
        _fill(store, batches=20)
        before = _versions(db)

        report = _optimize(db)

        assert report["versions_before"] == before
        assert report["versions_after"] < 5 < before
        assert _versions(db) == report["versions_after"]
        assert ChunkStore(db).count_chunks() == 20

    def test_dry_run_changes_nothing(self, tmp_path):
        db = tmp_path / "db"
        _fill(ChunkStore(db), batches=10)
        before = _versions(db)

        report = _optimize(db, dry_run=True)

        assert report == {"versions_before": before, "vector_index_retrained": False}
        assert _versions(db) == before

    def test_retrains_index_that_most_rows_lie_outside(self, tmp_path):
        db = tmp_path / "db"
        store = ChunkStore(db)
        _fill(store, batches=1, per_batch=300)
        store.table.create_index(metric="cosine", vector_column_name="vector")
        _fill(store, batches=1, per_batch=400, start=1)

        report = _optimize(db)

        assert report["vector_index_retrained"] is True
        stats = ChunkStore(db).table.index_stats("vector_idx")
        assert (stats.num_indexed_rows, stats.num_unindexed_rows) == (700, 0)

    def test_keeps_index_that_covers_most_rows(self, tmp_path):
        db = tmp_path / "db"
        store = ChunkStore(db)
        _fill(store, batches=1, per_batch=300)
        store.table.create_index(metric="cosine", vector_column_name="vector")
        _fill(store, batches=1, per_batch=10, start=1)

        assert _optimize(db)["vector_index_retrained"] is False


class TestReadsAcrossMaintenance:
    def test_open_store_reads_and_writes_after_external_optimize(self, tmp_path, monkeypatch):
        """A long-lived store keeps working once another process prunes its version."""
        monkeypatch.setattr(_chunk_store, "LANCE_READ_CONSISTENCY_INTERVAL", timedelta(0))
        db = tmp_path / "db"
        store = ChunkStore(db)
        _fill(store, batches=20)
        query = _random_vector(random.Random(0))
        assert store.search_vector(query, limit=3).results

        _optimize(db)

        assert len(store.search_vector(query, limit=3).results) == 3
        _fill(store, batches=1, start=100)
        assert store.count_chunks() == 21


class TestVectorSearchRanking:
    def test_exact_match_ranks_first_through_compressed_index(self, tmp_path):
        """Near-duplicate rows have near-equal PQ distances; re-ranking by
        exact distance must still return the queried row first. Without
        re-ranking about half of these 40 queries miss."""
        rng = random.Random(7)
        store = ChunkStore(tmp_path / "db")
        rows = []
        for cluster in range(500):
            base = _random_vector(rng)
            for member in range(8):
                vector = [x + rng.gauss(0, 0.05) for x in base]
                rows.append(
                    make_chunk(file_path=f"c{cluster}_{member}.py", chunk_index=0, vector=vector)
                )
        store.add_chunks(rows)
        store.table.create_index(metric="cosine", vector_column_name="vector")

        queried = rows[::100]
        hits = sum(
            store.search_vector(chunk.vector, limit=1).results[0].file_path == chunk.file_path
            for chunk in queried
        )
        assert hits == len(queried)


class TestOptimizeCaches:
    def test_reports_each_cache_and_skips_non_caches(self, tmp_path):
        root = tmp_path / "cache"
        _fill(ChunkStore(root / "0123456789ab"), batches=5)
        (root / "symbols").mkdir()
        (root / "fedcba987654").mkdir()  # cache-shaped, no chunks table

        report = optimize_caches(cache_dir=root, dry_run=True)

        assert report["dry_run"] is True
        assert [c["path"] for c in report["caches"]] == [str(root / "0123456789ab")]
        assert report["caches"][0]["versions_before"] == _versions(root / "0123456789ab")
        assert report["reclaimed_bytes"] == 0
        assert report["failures"] == []

    def test_execute_reclaims_space(self, tmp_path):
        root = tmp_path / "cache"
        _fill(ChunkStore(root / "0123456789ab"), batches=20)

        with warnings.catch_warnings():
            warnings.simplefilter("ignore", UserWarning)
            report = optimize_caches(cache_dir=root, dry_run=False, retention=_NO_RETENTION)

        (entry,) = report["caches"]
        assert entry["bytes_after"] < entry["bytes_before"]
        assert report["reclaimed_bytes"] == entry["bytes_before"] - entry["bytes_after"]

    def test_failure_is_reported_and_sweep_continues(self, tmp_path):
        root = tmp_path / "cache"
        _fill(ChunkStore(root / "0123456789ab"), batches=2)
        (root / "aaaaaaaaaaaa" / f"{CHUNKS_TABLE}.lance").mkdir(parents=True)

        report = optimize_caches(cache_dir=root, dry_run=True)

        assert [f["path"] for f in report["failures"]] == [str(root / "aaaaaaaaaaaa")]
        assert [c["path"] for c in report["caches"]] == [str(root / "0123456789ab")]


@pytest.fixture(autouse=True)
def _quiet_lance_deprecations():
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        yield
