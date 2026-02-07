# LgrepToolSelectionOptimization

> **Version:** 1.0.0
> **Updated:** 2026-02-07

## Purpose

Capability: LgrepToolSelectionOptimization

## Requirements

### Prefer semantic search for intent-based discovery

**ID:** `rq-1` | **Priority:** **[MUST]**

Agents MUST prefer `lgrep_search` as the first search action for intent-based code discovery prompts, measured against a fixed acceptance scenario matrix with a >=90% pass threshold.

**Tags:** `tool-selection`, `semantic-search`

#### Scenarios

**Semantic prompts choose lgrep first** (`rq-1.1`)

**Given:**
- A fixed semantic-discovery prompt fixture set

**When:** The tool-choice harness runs

**Then:**
- `lgrep_search` is selected as first search action in at least 90% of fixture cases

---

### Prefer exact-match tools for exact queries

**ID:** `rq-2` | **Priority:** **[MUST]**

Agents MUST prefer exact-match tools for exact identifier and regex prompts, measured against a fixed acceptance scenario matrix with a >=90% pass threshold.

**Tags:** `tool-selection`, `exact-match`

#### Scenarios

**Exact prompts avoid semantic-first behavior** (`rq-2.1`)

**Given:**
- A fixed exact-identifier and regex prompt fixture set

**When:** The tool-choice harness runs

**Then:**
- Exact-match tools are selected as first search action in at least 90% of fixture cases

---

### First semantic search auto-setup

**ID:** `rq-3` | **Priority:** **[MUST]**

First semantic search in a fresh project MUST succeed without manual user invocation of `lgrep_index` and MUST emit explicit status for start, retry, success, and failure outcomes.

**Tags:** `onboarding`, `auto-index`, `observability`

#### Scenarios

**Cold project auto-indexes on first search** (`rq-3.1`)

**Given:**
- A valid project path with no loaded in-memory state

**When:** `lgrep_search` is called

**Then:**
- Index initialization is auto-triggered
- Search completes without the user manually calling `lgrep_index`

**Auto-setup failure is actionable** (`rq-3.2`)

**Given:**
- First-run search where credentials are missing or indexing fails

**When:** Auto-setup is attempted

**Then:**
- A clear remediation-oriented error is returned
- Partial initialization state is not persisted

**Concurrent cold searches are single-flight** (`rq-3.3`)

**Given:**
- Concurrent first-run semantic searches for the same project path

**When:** Auto-index initialization starts

**Then:**
- Only one initialization/index flow executes
- All callers observe consistent terminal outcomes

---

### Decision matrix and transport guidance are validated

**ID:** `rq-4` | **Priority:** **[MUST]**

Documentation MUST provide a tool-choice decision matrix and validated setup workflow, including stdio as local default and streamable-http as explicit opt-in with security guidance.

**Tags:** `documentation`, `transport`, `security`

#### Scenarios

**Documented setup path is test-validated** (`rq-4.1`)

**Given:**
- README, skill docs, and example configuration

**When:** A user follows the documented setup path

**Then:**
- At least one integration test reproduces the documented workflow end-to-end

**Transport defaults and safeguards are explicit** (`rq-4.2`)

**Given:**
- Documentation for streamable-http mode

**When:** A user evaluates transport options

**Then:**
- Docs explicitly state stdio as local default
- Docs list streamable-http opt-in security controls (localhost binding, auth expectations, and origin guidance)

---
