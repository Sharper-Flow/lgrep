"""Bounded-memory probe: repeated semantic query cycles keep process RSS flat.

The probe runs in a fresh interpreter so the glibc arena cap applies before
any native thread exists, as it does at daemon startup. It opens several
owner stores through the shared LanceDB session, runs hybrid, vector, and
full-table reads on a lane-sized thread pool, and lets the idle trimmer return
freed heap after each cycle. Vectors are synthetic; no embedding API is used.
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


def run_probe(root: Path) -> dict:
    """Run the query cycles in this process and return the RSS samples."""
    import random
    import threading
    from concurrent.futures import ThreadPoolExecutor

    from lgrep import memory

    capped = memory.cap_malloc_arenas()

    from lgrep.storage import EMBEDDING_DIM, ChunkStore, CodeChunk
    from lgrep.storage._chunk_store import lance_session

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

    def trim() -> bool:
        ran = memory.trim_heap()
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
        "arena_capped": capped,
        "trims": memory.allocator_info()[1],
        "lance_cache_bytes": lance_session().size_bytes,
        "rss_samples": samples,
    }


@pytest.mark.skipif(not Path("/proc/self/status").exists(), reason="reads RSS from /proc")
def test_repeated_query_cycles_keep_rss_flat(tmp_path):
    environment = os.environ.copy()
    environment.update(
        HOME=str(tmp_path / "home"),
        LGREP_CACHE_DIR=str(tmp_path / "cache"),
        LGREP_SYMBOLS_DIR=str(tmp_path / "symbols"),
    )
    code = (
        "import json, sys; from pathlib import Path; "
        "from tests.native.test_bounded_memory import run_probe; "
        "print(json.dumps(run_probe(Path(sys.argv[1]))))"
    )
    repo = Path(__file__).resolve().parents[2]
    # The child loads conftest.py's sys.path insert only via PYTHONPATH: the
    # native runner runs with --no-install-project, so src/ is the sole lgrep
    # source, and repo root resolves the tests.native import.
    environment["PYTHONPATH"] = os.pathsep.join(
        p for p in (str(repo / "src"), str(repo), environment.get("PYTHONPATH")) if p
    )
    completed = subprocess.run(
        [sys.executable, "-c", code, str(tmp_path / "stores")],
        cwd=repo,
        env=environment,
        capture_output=True,
        text=True,
        timeout=600,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr[-4000:]
    report = json.loads(completed.stdout.strip().splitlines()[-1])
    samples = report["rss_samples"]
    start = statistics.median(samples[:SAMPLE_WINDOW])
    end = statistics.median(samples[-SAMPLE_WINDOW:])
    print(json.dumps({**report, "start_bytes": start, "end_bytes": end}))
    assert len(samples) == MEASURED_CYCLES
    assert end - start <= RSS_GROWTH_SLACK_BYTES, report
    if report["arena_capped"]:
        assert report["trims"] >= MEASURED_CYCLES
