"""Tests for token_tracker.py (tk-XDl0Oxm5).

RED phase: tests fail before token_tracker.py exists.
GREEN phase: all pass after implementation.
"""


class TestTokenTrackerImport:
    """Verify the module is importable with the expected API."""

    def test_token_tracker_importable(self):
        """TokenTracker must be importable from lgrep.storage.token_tracker."""
        from lgrep.storage.token_tracker import TokenTracker  # noqa: F401

    def test_estimate_savings_importable(self):
        """estimate_savings helper must be importable."""
        from lgrep.storage.token_tracker import estimate_savings  # noqa: F401

    def test_cost_avoided_importable(self):
        """cost_avoided helper must be importable."""
        from lgrep.storage.token_tracker import cost_avoided  # noqa: F401


class TestTokenTrackerBasic:
    """Basic TokenTracker functionality."""

    def test_initial_state_is_zero(self, tmp_path):
        """Fresh tracker should have zero session and total savings."""
        from lgrep.storage.token_tracker import TokenTracker

        tracker = TokenTracker(storage_path=tmp_path / "tokens.json")
        assert tracker.session_tokens == 0
        assert tracker.total_tokens == 0

    def test_record_savings_increments_session(self, tmp_path):
        """record_savings should increment session_tokens."""
        from lgrep.storage.token_tracker import TokenTracker

        tracker = TokenTracker(storage_path=tmp_path / "tokens.json")
        tracker.record_savings(500)
        assert tracker.session_tokens == 500

    def test_record_savings_increments_total(self, tmp_path):
        """record_savings should increment total_tokens."""
        from lgrep.storage.token_tracker import TokenTracker

        tracker = TokenTracker(storage_path=tmp_path / "tokens.json")
        tracker.record_savings(500)
        assert tracker.total_tokens == 500

    def test_multiple_record_savings_accumulate(self, tmp_path):
        """Multiple record_savings calls should accumulate."""
        from lgrep.storage.token_tracker import TokenTracker

        tracker = TokenTracker(storage_path=tmp_path / "tokens.json")
        tracker.record_savings(100)
        tracker.record_savings(200)
        tracker.record_savings(300)
        assert tracker.session_tokens == 600
        assert tracker.total_tokens == 600


class TestTokenTrackerPersistence:
    """TokenTracker must persist total across sessions."""

    def test_total_persists_across_instances(self, tmp_path):
        """Total tokens must survive creating a new TokenTracker instance."""
        from lgrep.storage.token_tracker import TokenTracker

        path = tmp_path / "tokens.json"

        # Session 1
        t1 = TokenTracker(storage_path=path)
        t1.record_savings(1000)
        t1.flush()

        # Session 2 — new instance, same file
        t2 = TokenTracker(storage_path=path)
        assert t2.total_tokens == 1000
        assert t2.session_tokens == 0  # session resets

    def test_session_resets_on_new_instance(self, tmp_path):
        """Session tokens must reset to 0 on new instance."""
        from lgrep.storage.token_tracker import TokenTracker

        path = tmp_path / "tokens.json"
        t1 = TokenTracker(storage_path=path)
        t1.record_savings(500)
        t1.flush()

        t2 = TokenTracker(storage_path=path)
        assert t2.session_tokens == 0

    def test_total_accumulates_across_sessions(self, tmp_path):
        """Total must accumulate across multiple sessions."""
        from lgrep.storage.token_tracker import TokenTracker

        path = tmp_path / "tokens.json"

        for _i in range(3):
            t = TokenTracker(storage_path=path)
            t.record_savings(100)
            t.flush()

        t_final = TokenTracker(storage_path=path)
        assert t_final.total_tokens == 300

    def test_missing_file_starts_fresh(self, tmp_path):
        """Missing storage file should start with zero totals."""
        from lgrep.storage.token_tracker import TokenTracker

        path = tmp_path / "nonexistent" / "tokens.json"
        tracker = TokenTracker(storage_path=path)
        assert tracker.total_tokens == 0
        assert tracker.session_tokens == 0


class TestEstimateSavings:
    """estimate_savings helper function."""

    def test_estimate_savings_returns_int(self):
        """estimate_savings must return an integer token count."""
        from lgrep.storage.token_tracker import estimate_savings

        result = estimate_savings(symbols=5, avg_tokens_per_symbol=200)
        assert isinstance(result, int)
        assert result > 0

    def test_estimate_savings_scales_with_symbols(self):
        """More symbols should yield more estimated savings."""
        from lgrep.storage.token_tracker import estimate_savings

        small = estimate_savings(symbols=1, avg_tokens_per_symbol=100)
        large = estimate_savings(symbols=10, avg_tokens_per_symbol=100)
        assert large > small

    def test_estimate_savings_zero_symbols(self):
        """Zero symbols should return 0."""
        from lgrep.storage.token_tracker import estimate_savings

        assert estimate_savings(symbols=0, avg_tokens_per_symbol=100) == 0


class TestCostAvoided:
    """cost_avoided helper function."""

    def test_cost_avoided_returns_float(self):
        """cost_avoided must return a float dollar amount."""
        from lgrep.storage.token_tracker import cost_avoided

        result = cost_avoided(tokens=1_000_000)
        assert isinstance(result, float)
        assert result > 0

    def test_cost_avoided_scales_linearly(self):
        """Double the tokens should double the cost avoided."""
        from lgrep.storage.token_tracker import cost_avoided

        single = cost_avoided(tokens=1_000_000)
        double = cost_avoided(tokens=2_000_000)
        assert abs(double - 2 * single) < 0.0001

    def test_cost_avoided_zero_tokens(self):
        """Zero tokens should return 0.0."""
        from lgrep.storage.token_tracker import cost_avoided

        assert cost_avoided(tokens=0) == 0.0


class TestMetaEnvelope:
    """TokenTracker must produce _meta envelope dicts."""

    def test_meta_envelope_has_required_fields(self, tmp_path):
        """meta() must return dict with session_tokens, total_tokens, cost_avoided_usd."""
        from lgrep.storage.token_tracker import TokenTracker

        tracker = TokenTracker(storage_path=tmp_path / "tokens.json")
        tracker.record_savings(500)
        meta = tracker.meta()

        assert "session_tokens" in meta
        assert "total_tokens" in meta
        assert "cost_avoided_usd" in meta
        assert meta["session_tokens"] == 500
        assert meta["total_tokens"] == 500
        assert isinstance(meta["cost_avoided_usd"], float)
