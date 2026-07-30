# Lgrep Dependency Integrity

> **Version:** 1.0.0
> **Updated:** 2026-07-30

## Purpose

Capability: Lgrep Dependency Integrity

## Requirements

### MCP SDK dependency is bounded and tool registration is guarded

**ID:** `rq-mcpSdkBound01` | **Priority:** **[MUST]**

The package MUST declare an upper bound on its MCP SDK dependency that excludes any major release whose server API the code has not been migrated to. An automated, offline, deterministic guard MUST fail when that declared bound is removed or widened to admit the unmigrated major release. A second automated guard MUST fail when the server cannot register its expected tool surface, so an import-time or registration-time break becomes a test failure instead of a silent loss of every tool at runtime. Version strings alone MUST NOT be the sole authority; the registration guard MUST assert the advertised tool names.

**Tags:** `dependencies`, `release-hygiene`, `mcp`, `structural-correctness`, `testing`

#### Scenarios

**Widened dependency bound fails verification** (`rq-mcpSdkBound01.1`)

**Given:**
- The package declares an MCP SDK requirement
- The code targets the currently supported major release

**When:** The declared requirement is changed to admit an unmigrated major release

**Then:**
- The dependency-bound guard exits non-zero
- The failure output names the offending requirement

**Missing tool registration fails verification** (`rq-mcpSdkBound01.2`)

**Given:**
- A resolved environment where the server module cannot register its tools

**When:** The registration guard runs

**Then:**
- The guard exits non-zero
- The failure identifies the missing expected tool names rather than only a version mismatch

**Supported environment passes verification** (`rq-mcpSdkBound01.3`)

**Given:**
- The declared bound excludes the unmigrated major release
- The resolved MCP SDK satisfies that bound

**When:** Both guards run

**Then:**
- Both guards exit zero
- The advertised tool surface includes the expected tool names

---
