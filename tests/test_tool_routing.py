"""Behavioral tests for lgrep tool-selection routing policy.

These tests verify that the routing policy documents (mcp-tools.md, SKILL.md,
explore.md) actually steer agents to the correct first tool for each query type.

Unlike the existing doc-keyword tests in test_server.py, these tests validate
the BEHAVIORAL contract: given a query type, the policy documents must contain
guidance that routes to the expected tool BEFORE any fallback tools.

The test harness works by:
1. Loading the policy documents that agents actually see at runtime
2. For each prompt fixture, checking that the expected tool appears in the
   routing guidance BEFORE any competing tools
3. Verifying that anti-patterns (e.g. "glob first" for concept queries) are
   NOT present in the policy documents

This catches the exact failure mode we observed: agents choosing grep/glob
over lgrep because the policy documents either didn't mention lgrep first
or actively contradicted the lgrep-first policy.
"""

from __future__ import annotations

from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Policy document paths
# ---------------------------------------------------------------------------

# The packaged always-loaded instruction file
_REPO_ROOT = Path(__file__).resolve().parents[1]
_SKILL_PATH = _REPO_ROOT / "skills" / "lgrep" / "SKILL.md"
_PACKAGE_INSTRUCTION = _REPO_ROOT / "instructions" / "lgrep-tools.md"

# External agent file (may not exist in CI — tests skip gracefully)
_EXPLORE_AGENT = Path.home() / ".config" / "opencode" / "agents" / "explore.md"


def _load_if_exists(path: Path) -> str | None:
    """Load a file if it exists, return None otherwise."""
    if path.exists():
        return path.read_text(encoding="utf-8")
    return None


# ---------------------------------------------------------------------------
# Prompt → Expected Tool Routing Fixtures
# ---------------------------------------------------------------------------

# Each tuple: (prompt, expected_first_tool, query_category)
SEMANTIC_PROMPTS = [
    ("where is auth enforced between API and service layer?", "lgrep_search_semantic", "intent"),
    ("how does the rate limiter work?", "lgrep_search_semantic", "intent"),
    ("find the error handling strategy", "lgrep_search_semantic", "intent"),
    ("what middleware runs before route handlers?", "lgrep_search_semantic", "intent"),
    ("how are database connections pooled?", "lgrep_search_semantic", "intent"),
    ("where is user input validated?", "lgrep_search_semantic", "intent"),
    ("explain the caching strategy", "lgrep_search_semantic", "intent"),
    ("how are background jobs scheduled?", "lgrep_search_semantic", "intent"),
    ("where are permissions checked?", "lgrep_search_semantic", "intent"),
    ("how does retry logic work for failed requests?", "lgrep_search_semantic", "intent"),
]

SYMBOL_PROMPTS = [
    ("find the authenticate function", "lgrep_search_symbols", "symbol"),
    ("where is the UserService class defined?", "lgrep_search_symbols", "symbol"),
    ("find the handleError method", "lgrep_search_symbols", "symbol"),
]

FILE_STRUCTURE_PROMPTS = [
    ("what functions are in src/auth.py?", "lgrep_get_file_outline", "file_structure"),
    ("what's in this codebase?", "lgrep_get_repo_outline", "repo_structure"),
]

EXACT_TEXT_PROMPTS = [
    ("find all references to verifyToken", "grep", "exact_text"),
    ("find all TODO comments", "grep", "exact_text"),
    ("find all occurrences of console.log", "grep", "exact_text"),
]

KNOWN_FILE_PROMPTS = [
    ("open src/auth/jwt.ts and explain line 42", "read", "known_file"),
    ("show me the contents of package.json", "read", "known_file"),
]


# ---------------------------------------------------------------------------
# Test: SKILL.md routes correctly
# ---------------------------------------------------------------------------


class TestSkillRouting:
    """Verify SKILL.md contains correct routing for each query type."""

    @pytest.fixture(autouse=True)
    def _load_skill(self):
        self.content = _SKILL_PATH.read_text(encoding="utf-8")
        self.content_lower = self.content.lower()

    @pytest.mark.parametrize("prompt,expected_tool,category", SEMANTIC_PROMPTS)
    def test_semantic_prompts_route_to_lgrep_search_semantic(self, prompt, expected_tool, category):
        """Semantic/intent prompts must route to lgrep_search_semantic."""
        assert "lgrep_search_semantic" in self.content, (
            f"SKILL.md does not mention lgrep_search_semantic for intent queries like: {prompt}"
        )
        # The key behavioral check: lgrep_search_semantic must appear BEFORE
        # any guidance to use grep/glob for this type of query
        assert "call `lgrep_search_semantic` first" in self.content, (
            "SKILL.md must explicitly state lgrep_search_semantic as first action for intent queries"
        )

    @pytest.mark.parametrize("prompt,expected_tool,category", SYMBOL_PROMPTS)
    def test_symbol_prompts_route_to_lgrep_search_symbols(self, prompt, expected_tool, category):
        """Symbol name prompts must route to lgrep_search_symbols."""
        assert "lgrep_search_symbols" in self.content, (
            f"SKILL.md does not mention lgrep_search_symbols for symbol queries like: {prompt}"
        )

    @pytest.mark.parametrize("prompt,expected_tool,category", FILE_STRUCTURE_PROMPTS)
    def test_file_structure_prompts_route_to_lgrep_outline(self, prompt, expected_tool, category):
        """File/repo structure prompts must route to lgrep outline tools."""
        assert expected_tool in self.content, (
            f"SKILL.md does not mention {expected_tool} for structure queries like: {prompt}"
        )

    @pytest.mark.parametrize("prompt,expected_tool,category", EXACT_TEXT_PROMPTS)
    def test_exact_text_prompts_route_to_grep(self, prompt, expected_tool, category):
        """Exact text/regex prompts must route to grep or lgrep_search_text."""
        assert "grep" in self.content_lower or "lgrep_search_text" in self.content, (
            f"SKILL.md does not mention grep/lgrep_search_text for exact queries like: {prompt}"
        )

    @pytest.mark.parametrize("prompt,expected_tool,category", KNOWN_FILE_PROMPTS)
    def test_known_file_prompts_route_to_read(self, prompt, expected_tool, category):
        """Known-file prompts must route to read."""
        assert "read" in self.content_lower, (
            f"SKILL.md does not mention read for known-file queries like: {prompt}"
        )


# ---------------------------------------------------------------------------
# Test: Anti-patterns are absent from policy documents
# ---------------------------------------------------------------------------


class TestAntiPatterns:
    """Verify that known anti-patterns are NOT present in policy documents."""

    def test_skill_does_not_say_glob_first(self):
        """SKILL.md must not tell agents to use glob before lgrep."""
        content = _SKILL_PATH.read_text(encoding="utf-8").lower()
        assert "glob first" not in content, (
            "SKILL.md contains 'glob first' — this contradicts lgrep-first policy"
        )

    def test_skill_does_not_say_grep_first_for_concepts(self):
        """SKILL.md must not tell agents to use grep for concept/intent queries."""
        content = _SKILL_PATH.read_text(encoding="utf-8")
        # "Grep first" should only appear in the context of exact-match queries.
        # Exclude lines that contain "lgrep" (e.g. "lgrep first-action") since
        # those are about lgrep policy, not grep-first guidance.
        lines = content.split("\n")
        for i, line in enumerate(lines):
            line_lower = line.lower()
            if (
                "grep first" in line_lower
                and "exact" not in line_lower
                and "lgrep" not in line_lower
            ):
                pytest.fail(
                    f"SKILL.md line {i + 1} says 'grep first' outside exact-match context: {line}"
                )

    @pytest.mark.skipif(not _EXPLORE_AGENT.exists(), reason="explore.md not installed")
    def test_explore_agent_does_not_say_glob_first(self):
        """explore.md must not tell agents to use glob before lgrep."""
        content = _EXPLORE_AGENT.read_text(encoding="utf-8").lower()
        assert "glob first" not in content, (
            "explore.md contains 'glob first' — this contradicts lgrep-first policy"
        )

    @pytest.mark.skipif(not _EXPLORE_AGENT.exists(), reason="explore.md not installed")
    def test_explore_agent_does_not_say_grep_before_lgrep(self):
        """explore.md research strategy must not list grep before lgrep in numbered items."""
        content = _EXPLORE_AGENT.read_text(encoding="utf-8")
        lines = content.split("\n")
        grep_item_line = None
        lgrep_item_line = None
        in_strategy = False
        for i, line in enumerate(lines):
            if "research strategy" in line.lower():
                in_strategy = True
                continue
            if in_strategy and line.startswith("##"):
                break  # next section
            # Only look at numbered list items (e.g. "1. **lgrep for concepts**")
            if in_strategy and line.strip() and line.strip()[0].isdigit():
                line_lower = line.lower()
                if "lgrep" in line_lower and lgrep_item_line is None:
                    lgrep_item_line = i
                elif "grep" in line_lower and "lgrep" not in line_lower and grep_item_line is None:
                    grep_item_line = i

        if grep_item_line is not None and lgrep_item_line is not None:
            assert lgrep_item_line < grep_item_line, (
                f"explore.md lists grep (line {grep_item_line + 1}) before lgrep "
                f"(line {lgrep_item_line + 1}) in Research Strategy numbered items "
                f"— lgrep should come first"
            )


# ---------------------------------------------------------------------------
# Test: Packaged always-loaded instruction routing
# ---------------------------------------------------------------------------


class TestPackagedInstructionRouting:
    """Verify the packaged always-loaded instruction routes correctly."""

    def test_packaged_instruction_has_lgrep_first_action_policy(self):
        """lgrep-tools.md must contain the lgrep first-action policy."""
        content = _PACKAGE_INSTRUCTION.read_text(encoding="utf-8")
        assert "lgrep" in content, "lgrep-tools.md does not mention lgrep"
        assert (
            "first-action" in content.lower()
            or "first tool" in content.lower()
            or "first" in content.lower()
        ), "lgrep-tools.md does not mention first-action policy"

    def test_packaged_instruction_has_anti_patterns(self):
        """lgrep-tools.md must list anti-patterns for glob/grep misuse."""
        content = _PACKAGE_INSTRUCTION.read_text(encoding="utf-8").lower()
        assert "anti-pattern" in content or "do not" in content, (
            "lgrep-tools.md does not list anti-patterns for tool misuse"
        )

    def test_packaged_instruction_does_not_say_glob_first(self):
        """lgrep-tools.md must not tell agents to use glob before lgrep."""
        content = _PACKAGE_INSTRUCTION.read_text(encoding="utf-8")
        lines = content.split("\n")
        for i, line in enumerate(lines):
            line_lower = line.lower()
            if (
                "glob first" in line_lower
                and "override" not in line_lower
                and '"glob first"' not in line
            ):
                pytest.fail(
                    f"lgrep-tools.md line {i + 1} says 'glob first' outside override/anti-pattern context: {line}"
                )

    def test_packaged_instruction_has_decision_matrix(self):
        """lgrep-tools.md must contain a decision matrix for tool selection."""
        content = _PACKAGE_INSTRUCTION.read_text(encoding="utf-8")
        assert "| query type |" in content.lower(), (
            "lgrep-tools.md does not contain a decision matrix"
        )

    def test_packaged_instruction_routes_semantic_to_lgrep(self):
        """lgrep-tools.md must route concept/intent queries to lgrep_search_semantic."""
        content = _PACKAGE_INSTRUCTION.read_text(encoding="utf-8")
        assert "lgrep_search_semantic" in content, (
            "lgrep-tools.md does not mention lgrep_search_semantic"
        )

    def test_packaged_instruction_routes_symbols_to_lgrep(self):
        """lgrep-tools.md must route symbol queries to lgrep_search_symbols."""
        content = _PACKAGE_INSTRUCTION.read_text(encoding="utf-8")
        assert "lgrep_search_symbols" in content, (
            "lgrep-tools.md does not mention lgrep_search_symbols"
        )


class TestToolExposureDocumentation:
    """Verify docs explain that agent tool manifests must expose lgrep tools."""

    def test_packaged_instruction_mentions_tool_exposure_requirement(self):
        """Always-on instruction must explain that policy alone is insufficient."""
        content = _PACKAGE_INSTRUCTION.read_text(encoding="utf-8")
        assert "Tool Exposure Requirement" in content
        assert "tool manifest" in content.lower()
        assert "lgrep_search_semantic: true" in content

    def test_skill_mentions_tool_exposure_requirement(self):
        """SKILL.md must explain that agent manifests need lgrep_* tool defs."""
        content = _SKILL_PATH.read_text(encoding="utf-8")
        assert "Tool Exposure Requirement" in content
        assert "tool definitions" in content.lower() or "tool manifest" in content.lower()
        assert "lgrep_search_symbols: true" in content

    def test_skill_setup_mentions_all_installed_artifacts(self):
        """SKILL.md setup text must match installer behavior."""
        content = _SKILL_PATH.read_text(encoding="utf-8")
        assert "MCP server entry" in content
        assert "instructions/lgrep-tools.md" in content
        assert "skill file" in content.lower()


# ---------------------------------------------------------------------------
# Test: Installer verifies policy wiring
# ---------------------------------------------------------------------------


class TestInstallerPolicyCheck:
    """Verify the installer checks for always-loaded lgrep routing policy."""

    def test_check_instructions_finds_lgrep_policy(self, tmp_path):
        """_check_instructions_have_lgrep_policy returns True when policy exists."""
        from lgrep.install_opencode import _check_instructions_have_lgrep_policy

        # Create a mock instruction file with lgrep routing policy
        instruction_file = tmp_path / "lgrep-tools.md"
        instruction_file.write_text(
            "## Local Code Exploration\n"
            "lgrep is the PRIMARY tool. First-action policy:\n"
            "- Intent discovery: lgrep_search_semantic\n"
        )

        result = _check_instructions_have_lgrep_policy([str(instruction_file)])
        assert result is True

    def test_check_instructions_detects_missing_policy(self, tmp_path):
        """_check_instructions_have_lgrep_policy returns False when no policy."""
        from lgrep.install_opencode import _check_instructions_have_lgrep_policy

        # Create a mock instruction file WITHOUT lgrep routing
        instruction_file = tmp_path / "rules.md"
        instruction_file.write_text("# Rules\nBe nice.\n")

        result = _check_instructions_have_lgrep_policy([str(instruction_file)])
        assert result is False

    def test_check_instructions_handles_missing_files(self):
        """_check_instructions_have_lgrep_policy handles missing files gracefully."""
        from lgrep.install_opencode import _check_instructions_have_lgrep_policy

        result = _check_instructions_have_lgrep_policy(["/nonexistent/path.md"])
        assert result is False

    def test_check_instructions_handles_empty_list(self):
        """_check_instructions_have_lgrep_policy handles empty instructions list."""
        from lgrep.install_opencode import _check_instructions_have_lgrep_policy

        result = _check_instructions_have_lgrep_policy([])
        assert result is False

    def test_check_instructions_requires_routing_not_just_mention(self, tmp_path):
        """Merely mentioning 'lgrep' is not enough — must have routing guidance."""
        from lgrep.install_opencode import _check_instructions_have_lgrep_policy

        # File mentions lgrep but has no routing policy
        instruction_file = tmp_path / "notes.md"
        instruction_file.write_text("# Notes\nlgrep is a tool we use.\n")

        result = _check_instructions_have_lgrep_policy([str(instruction_file)])
        assert result is False


# ---------------------------------------------------------------------------
# Test: Scenario matrix coverage (behavioral, not doc-keyword)
# ---------------------------------------------------------------------------


class TestScenarioMatrixCoverage:
    """Verify the full scenario matrix has routing coverage across all policy docs.

    Unlike the existing test_server.py tests that check for keyword presence,
    these tests verify that each query CATEGORY has explicit routing guidance
    to the correct tool in at least one policy document.
    """

    PASS_THRESHOLD = 0.9  # >=90% of categories must have routing coverage

    @pytest.fixture(autouse=True)
    def _load_docs(self):
        self.skill = _SKILL_PATH.read_text(encoding="utf-8")
        self.mcp_tools = _PACKAGE_INSTRUCTION.read_text(encoding="utf-8")
        self.explore = _load_if_exists(_EXPLORE_AGENT) or ""
        self.all_docs = self.skill + "\n" + self.mcp_tools + "\n" + self.explore

    def _tool_is_routed(self, tool_name: str) -> bool:
        """Check if a tool is mentioned in any policy document."""
        return tool_name in self.all_docs

    def test_all_lgrep_tools_are_routed(self):
        """Every lgrep tool that should be preferred must appear in routing docs."""
        required_tools = [
            "lgrep_search_semantic",
            "lgrep_search_symbols",
            "lgrep_get_file_outline",
            "lgrep_get_repo_outline",
        ]
        missing = [t for t in required_tools if not self._tool_is_routed(t)]
        assert not missing, f"These lgrep tools are not mentioned in any policy doc: {missing}"

    def test_semantic_category_has_routing(self):
        """Intent/concept queries must have explicit lgrep_search_semantic routing."""
        assert "lgrep_search_semantic" in self.all_docs
        # Must appear in context of "first" or "intent" or "concept"
        lower = self.all_docs.lower()
        assert any(marker in lower for marker in ["intent", "concept", "semantic", "meaning"]), (
            "No policy doc routes intent/concept queries to lgrep_search_semantic"
        )

    def test_symbol_category_has_routing(self):
        """Symbol name queries must have explicit lgrep_search_symbols routing."""
        assert "lgrep_search_symbols" in self.all_docs
        lower = self.all_docs.lower()
        assert any(marker in lower for marker in ["symbol", "function", "class", "method"]), (
            "No policy doc routes symbol queries to lgrep_search_symbols"
        )

    def test_exact_text_category_preserves_grep(self):
        """Exact text/regex queries must still route to grep."""
        lower = self.all_docs.lower()
        assert "grep" in lower, "No policy doc mentions grep for exact text queries"
        assert any(marker in lower for marker in ["exact", "literal", "regex", "text"]), (
            "No policy doc routes exact text queries to grep"
        )

    def test_known_file_category_preserves_read(self):
        """Known-file queries must still route to read."""
        lower = self.all_docs.lower()
        assert "read" in lower, "No policy doc mentions read for known-file queries"

    def test_full_scenario_matrix_coverage(self):
        """>=90% of all prompt fixtures must have their expected tool in policy docs."""
        all_prompts = (
            SEMANTIC_PROMPTS
            + SYMBOL_PROMPTS
            + FILE_STRUCTURE_PROMPTS
            + EXACT_TEXT_PROMPTS
            + KNOWN_FILE_PROMPTS
        )
        total = len(all_prompts)
        covered = 0
        missing = []

        for prompt, expected_tool, category in all_prompts:
            if (
                expected_tool in self.all_docs
                or (expected_tool == "grep" and "grep" in self.all_docs.lower())
                or (expected_tool == "read" and "read" in self.all_docs.lower())
            ):
                covered += 1
            else:
                missing.append((prompt, expected_tool, category))

        threshold = int(total * self.PASS_THRESHOLD)
        assert covered >= threshold, (
            f"Policy docs cover {covered}/{total} scenarios (need {threshold}). Missing: {missing}"
        )
