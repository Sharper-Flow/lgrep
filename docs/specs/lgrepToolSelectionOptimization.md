# LgrepToolSelectionOptimization

> **Version:** 1.1.0
> **Updated:** 2026-02-08

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

### Proposal quality checks are mandatory and measurable

**ID:** `rq-5` | **Priority:** **[MUST]**

Proposal quality validation MUST enforce mandatory checks for unambiguous wording, verifiable acceptance criteria, and requirement-to-scenario traceability. Full INVEST dimensions SHOULD be advisory guidance and MUST NOT hard-block progression by themselves.

**Tags:** `proposal-quality`, `validation`, `invest`, `adv-lifecycle`

#### Scenarios

**Proposal meeting mandatory checks passes** (`rq-5.1`)

**Given:**
- A proposal with measurable requirements and scenario trace links

**When:** Validation runs

**Then:**
- Validation reports pass for mandatory proposal-quality checks

**Proposal missing mandatory checks fails with remediation** (`rq-5.2`)

**Given:**
- A proposal with ambiguous wording or missing traceability

**When:** Validation runs

**Then:**
- Validation fails with actionable remediation guidance for each failed check

---

### Task and gate sequencing uses explicit transitions

**ID:** `rq-6` | **Priority:** **[MUST]**

Task and gate progression MUST be represented as explicit transition rules with mandatory and conditional paths. Invalid transitions MUST be rejected with actionable error messages using a structured error contract that includes `code`, `message`, `remediation`, and `allowed_next_transitions`. Approved bypass or override transitions MUST record actor, reason, and timestamp.

**Tags:** `workflow`, `gates`, `fsm`, `audit`, `adv-lifecycle`

#### Scenarios

**Valid transition across mandatory gate path succeeds** (`rq-6.1`)

**Given:**
- A change with required prerequisites complete for the next gate

**When:** A gate transition is requested

**Then:**
- Transition succeeds and new gate state is persisted

**Invalid transition is rejected with structured remediation** (`rq-6.2`)

**Given:**
- A requested transition that violates transition rules

**When:** A gate transition is requested

**Then:**
- Transition is rejected
- Error includes the unmet guard and an actionable next step
- Error payload includes code, message, remediation, and allowed_next_transitions

**Approved override transition is auditable** (`rq-6.3`)

**Given:**
- A transition requiring override and explicit approval

**When:** Override transition is executed

**Then:**
- An audit artifact records actor, reason, and timestamp

---

### Acceptance coverage uses compact deterministic matrix

**ID:** `rq-7` | **Priority:** **[MUST]**

Acceptance coverage MUST use command-level contract tests plus a deterministic 3-scenario integration matrix covering success, validation failure, and remediation outcomes.

**Tags:** `testing`, `acceptance`, `determinism`, `adv-lifecycle`

#### Scenarios

**Matrix cardinality and labels are fixed** (`rq-7.1`)

**Given:**
- Acceptance scenario fixtures

**When:** Acceptance harness loads scenarios

**Then:**
- Exactly three integration scenarios exist: success, validation failure, remediation

**Contract tests verify command outcomes** (`rq-7.2`)

**Given:**
- Command-level test fixtures for prep, validate, and apply

**When:** Contract tests run

**Then:**
- Expected success and failure outputs are observed deterministically

---

### Lifecycle command documentation is executable

**ID:** `rq-8` | **Priority:** **[MUST]**

Documentation MUST include command-level examples for `/adv-prep`, `/adv-validate`, and `/adv-apply` with required inputs and observable success and failure outcomes.

**Tags:** `documentation`, `lifecycle`, `examples`, `adv-lifecycle`

#### Scenarios

**Lifecycle examples define required inputs** (`rq-8.1`)

**Given:**
- Lifecycle command documentation

**When:** Examples are reviewed

**Then:**
- Each command example includes required input fields

**Lifecycle examples define observable outcomes** (`rq-8.2`)

**Given:**
- Lifecycle command documentation

**When:** Examples are reviewed

**Then:**
- Each command example includes observable success and failure outcomes

**Missing required inputs are documented with remediation** (`rq-8.3`)

**Given:**
- Lifecycle command documentation

**When:** Failure examples are reviewed

**Then:**
- Each command includes a missing-input failure example with remediation guidance

---

### Lifecycle operations emit auditable status artifacts

**ID:** `rq-9` | **Priority:** **[MUST]**

Lifecycle operations MUST emit structured status markers and auditable artifacts for prep, validate, and apply execution paths so failures can be diagnosed and overrides can be traced.

**Tags:** `observability`, `audit`, `operations`, `adv-lifecycle`

#### Scenarios

**Prep/validate/apply emit structured status markers** (`rq-9.1`)

**Given:**
- A lifecycle command execution

**When:** The command completes

**Then:**
- Structured status markers are emitted for outcome state

**Failure and override paths produce auditable artifacts** (`rq-9.2`)

**Given:**
- A failed transition or approved override

**When:** Execution terminates

**Then:**
- Artifacts include enough context to diagnose failure and trace authorization

---
