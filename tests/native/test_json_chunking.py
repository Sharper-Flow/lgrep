"""Acceptance checks for JSON text routing and unchanged AST behavior."""

from unittest.mock import Mock

import pytest
from structlog.testing import capture_logs

from lgrep.chunking import ChunkInfo, CodeChunker, detect_language
from lgrep.watcher import SUPPORTED_EXTENSIONS


@pytest.mark.parametrize(
    ("filename", "content", "expected"),
    [
        (
            "pretty.json",
            '{\n  "a": 1,\n  "b": 2\n}',
            [ChunkInfo('{\n  "a": 1,', 5, 0, 1, 2), ChunkInfo('  "b": 2\n}', 5, 1, 3, 4)],
        ),
        ("compact.json", '{"a":1,"b":2}', [ChunkInfo('{"a":1,"b":2}', 2, 0, 1, 1)]),
        (
            "partial.json",
            "{\n broken: 1\n}",
            [ChunkInfo("{\n broken: 1", 5, 0, 1, 2), ChunkInfo("}", 2, 1, 3, 3)],
        ),
        ("upper.JSON", '{"a":1}', [ChunkInfo('{"a":1}', 2, 0, 1, 1)]),
    ],
    ids=["pretty", "compact", "partial", "upper"],
)
def test_json_uses_text_chunking(tmp_path, monkeypatch, filename, content, expected):
    import chonkie.experimental

    constructor = Mock(wraps=chonkie.experimental.CodeChunker)
    monkeypatch.setattr(chonkie.experimental, "CodeChunker", constructor)
    path = tmp_path / filename
    path.write_text(content, encoding="utf-8")
    chunker = CodeChunker(chunk_size=6)
    with capture_logs() as events:
        results = [chunker.chunk_file(path), chunker.chunk_file(path, content)]
    constructor.assert_not_called()
    assert not any(event["event"] == "chunker_creation_failed" for event in events)
    for result in results:
        assert result.language == "json"
        assert result.error is None
        assert result.chunks == expected
    assert detect_language(path) == "json"
    assert ".json" in SUPPORTED_EXTENSIONS


@pytest.mark.parametrize("content", ["", " \n\t "], ids=["empty", "whitespace"])
def test_empty_json_stays_empty(content):
    result = CodeChunker().chunk_file("empty.json", content)
    assert result.language is None
    assert result.chunks == []
    assert result.error is None


def test_supported_language_still_uses_ast(monkeypatch):
    import chonkie.experimental

    constructor = Mock(wraps=chonkie.experimental.CodeChunker)
    monkeypatch.setattr(chonkie.experimental, "CodeChunker", constructor)
    chunker = CodeChunker()
    source = "def compute_answer():\n    return 42\n"
    results = [chunker.chunk_file("sample.py", source) for _ in range(2)]
    constructor.assert_called_once_with(language="python", chunk_size=500)
    assert all(result.chunks and result.language == "python" for result in results)
    assert results[0].chunks == results[1].chunks


def test_unexpected_constructor_failure_still_warns(monkeypatch):
    import chonkie.experimental

    constructor = Mock(side_effect=RuntimeError("unexpected constructor failure"))
    monkeypatch.setattr(chonkie.experimental, "CodeChunker", constructor)
    chunker = CodeChunker()
    source = "def compute_answer():\n    return 42\n"
    with capture_logs() as events:
        results = [chunker.chunk_file("sample.py", source) for _ in range(2)]
    assert constructor.call_count == 2
    failures = [event for event in events if event["event"] == "chunker_creation_failed"]
    assert len(failures) == 2
    assert all(event["error"] == "unexpected constructor failure" for event in failures)
    assert all(result.chunks and result.error is None for result in results)
