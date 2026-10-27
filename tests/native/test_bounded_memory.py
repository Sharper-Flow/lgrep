"""Bounded-memory probe: repeated semantic query cycles keep process RSS flat.

The probe opens several owner stores, runs hybrid, vector, and full-table
reads on a lane-sized thread pool, and runs the idle trimmer after each cycle.
It runs twice, each arm in a fresh interpreter so the glibc arena cap applies
before any native thread exists, as it does at daemon startup:

- the bounded arm caps malloc arenas, connects every store through the shared
  LanceDB session, and trims freed heap when idle;
- the unbounded control arm skips the cap, trims nothing, and gives each store
  its own default LanceDB connection.

Both arms stay roughly flat once warm, so flatness alone cannot tell them
apart. The probe also requires the bounded arm to plateau well below the
control. Vectors are synthetic; no embedding API is used.
"""

from __future__ import annotations

import json
import os
import statistics
import subprocess
import sys
from pathlib import Path

import pytest

STORES = 3
ROWS_PER_STORE = 3000
THREADS = 6
WARMUP_CYCLES = 5
MEASURED_CYCLES = 25
SAMPLE_WINDOW = 3

# Allowed RSS growth from the start to the end of the measured cycles. Bounded
# runs drifted at most 7 MiB over 75 cycles; the rest is room for runner noise.
RSS_GROWTH_SLACK_BYTES = 24 * 1024 * 1024

# Largest allowed ratio of bounded to unbounded end RSS. Bounded runs ended at
# 0.64-0.69 of the control; two unbounded arms end near 1.0.
BOUNDED_END_RATIO = 0.85

ARM_TIMEOUT_S = 600


def run_probe(root: Path, *, bounded: bool = True) -> dict:
    """Run the query cycles in this process and return the RSS samples.

    With ``bounded`` false the process keeps the allocator and LanceDB
    defaults: no arena cap, no heap trim, and one default session per store.
    """
    import random
    import threading
    from concurrent.futures import ThreadPoolExecutor

    from lgrep import memory

    capped = memory.cap_malloc_arenas() if bounded else False

    import lancedb

    from lgrep.storage import EMBEDDING_DIM, ChunkStore, CodeChunk, _chunk_store

    if not bounded:
        _chunk_store.connect_lance = lambda path: lancedb.connect(
            str(path), read_consistency_interval=_chunk_store.LANCE_READ_CONSISTENCY_INTERVAL
        )

    rng = random.Random(56)
    words = ["store", "worker", "session", "lance", "query", "memory", "index", "trim"]
    stores = []
    for s in range(STORES):
        store = ChunkStore(root / f"store-{s}")
        for batch in range(ROWS_PER_STORE // 500):
            store.add_chunks(
                [
                    CodeChunk(
                        id=f"{s}-{batch}-{i}",
                        file_path=f"pkg{batch}/mod{i % 25}.py",
                        chunk_index=i,
                        start_line=1,
                        end_line=20,
                        content=" ".join(rng.choice(words) for _ in range(120)),
                        vector=[rng.uniform(-1, 1) for _ in range(EMBEDDING_DIM)],
                        file_hash=f"hash-{batch}-{i % 25}",
                        indexed_at=1.0,
                        embedding_model="voyage-code-3",
                    )
                    for i in range(500)
                ]
            )
        store.prepare_hybrid_indexes()
        stores.append(store)
    vectors = [[rng.uniform(-1, 1) for _ in range(EMBEDDING_DIM)] for _ in range(8)]

    def query(i: int) -> None:
        store = stores[i % STORES]
        store.search_hybrid(vectors[i % 8], words[i % len(words)], limit=20)
        store.search_vector(vectors[(i + 3) % 8], limit=20)
        store.get_file_hashes()

    trimmed = threading.Event()
    trim_heap = memory.trim_heap if bounded else lambda: False

    def trim() -> bool:
        ran = trim_heap()
        trimmed.set()
        return ran

    trimmer = memory.IdleTrimmer(lambda: True, interval_s=0.0, trim=trim)
    samples = []
    with ThreadPoolExecutor(THREADS) as pool:
        for cycle in range(WARMUP_CYCLES + MEASURED_CYCLES):
            list(pool.map(query, range(cycle * 24, cycle * 24 + 24)))
            trimmed.clear()
            trimmer.notify_idle()
            trimmed.wait(10)
            if cycle >= WARMUP_CYCLES:
                samples.append(memory.process_rss_swap_bytes()[0])
    trimmer.close()
    return {
        "bounded": bounded,
        "arena_capped": capped,
        "trims": memory.allocator_info()[1],
        "lance_cache_bytes": _chunk_store.lance_session().size_bytes if bounded else None,
        "rss_samples": samples,
    }


def _start_arm(tmp_path: Path, arm: str) -> subprocess.Popen:
    environment = os.environ.copy()
    environment.update(
        HOME=str(tmp_path / "home"),
        LGREP_CACHE_DIR=str(tmp_path / "cache"),
        LGREP_SYMBOLS_DIR=str(tmp_path / "symbols"),
    )
    repo = Path(__file__).resolve().parents[2]
    # The child never loads conftest.py, so PYTHONPATH stands in for its
    # sys.path insert of src/. The child's working directory, the repository
    # root, resolves the tests.native import.
    environment["PYTHONPATH"] = os.pathsep.join(
        p for p in (str(repo / "src"), environment.get("PYTHONPATH")) if p
    )
    code = (
        "import json, sys; from pathlib import Path; "
        "from tests.native.test_bounded_memory import run_probe; "
        "print(json.dumps(run_probe(Path(sys.argv[1]), bounded=sys.argv[2] == 'bounded')))"
    )
    return subprocess.Popen(
        [sys.executable, "-c", code, str(tmp_path / "stores" / arm), arm],
        cwd=repo,
        env=environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )


def _finish_arm(process: subprocess.Popen) -> dict:
    stdout, stderr = process.communicate(timeout=ARM_TIMEOUT_S)
    assert process.returncode == 0, stderr[-4000:]
    report = json.loads(stdout.strip().splitlines()[-1])
    samples = report["rss_samples"]
    assert len(samples) == MEASURED_CYCLES
    report["start_bytes"] = statistics.median(samples[:SAMPLE_WINDOW])
    report["end_bytes"] = statistics.median(samples[-SAMPLE_WINDOW:])
    return report


@pytest.mark.skipif(not Path("/proc/self/status").exists(), reason="reads RSS from /proc")
def test_repeated_query_cycles_keep_rss_flat(tmp_path):
    processes = {arm: _start_arm(tmp_path, arm) for arm in ("bounded", "unbounded")}
    try:
        bounded, unbounded = (_finish_arm(processes[arm]) for arm in ("bounded", "unbounded"))
    finally:
        for process in processes.values():
            if process.poll() is None:
                process.kill()
                process.communicate()
    print(json.dumps({"bounded": bounded, "unbounded": unbounded}))
    assert bounded["end_bytes"] - bounded["start_bytes"] <= RSS_GROWTH_SLACK_BYTES, bounded
    assert bounded["end_bytes"] <= BOUNDED_END_RATIO * unbounded["end_bytes"], (bounded, unbounded)
    if bounded["arena_capped"]:
        assert bounded["trims"] >= WARMUP_CYCLES + MEASURED_CYCLES, bounded
