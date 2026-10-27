"""Closed remote coverage causes shared by indexing, history, and response schemas."""

from typing import Literal, get_args

RemoteTruncationReason = Literal[
    "tree_truncated",
    "max_files",
    "fetch_failed",
    "parse_failed",
    "deadline",
]

REMOTE_TRUNCATION_REASONS: tuple[RemoteTruncationReason, ...] = get_args(RemoteTruncationReason)
