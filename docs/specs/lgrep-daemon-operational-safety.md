# Lgrep Daemon Operational Safety

> **Version:** 1.0.0
> **Updated:** 2026-07-30

## Purpose

Capability: Lgrep Daemon Operational Safety

## Requirements

### Destructive maintenance over MCP requires an explicit capability grant

**ID:** `rq-destructiveGrant01` | **Priority:** **[MUST]**

Destructive maintenance tools exposed over MCP default to preview-only. They perform destructive work in exactly one condition: an explicit out-of-band capability grant is present in the server environment. Transport kind is excluded from the authority decision, because a proxy can front a local stdio pipe with a shared network surface, so a transport reported as local is uninformative about caller identity. When the grant is absent, the tool returns a preview result whose message names the required grant and the equivalent command-line invocation. The command-line entry point applies its own caller's arguments directly, since that caller already holds local shell authority.

**Tags:** `security`, `maintenance`, `mcp`, `structural-correctness`, `least-privilege`

#### Scenarios

**Proxied local transport yields a preview-only run** (`rq-destructiveGrant01.1`)

**Given:**
- A destructive maintenance tool is exposed over MCP
- The server runs as a stdio subprocess behind a shared proxy
- The capability grant is absent

**When:** A client requests a destructive run

**Then:**
- The tool performs a preview-only run
- The response reports that the destructive run was refused
- The response names the required grant and the command-line equivalent

**Explicit grant authorizes the destructive run** (`rq-destructiveGrant01.2`)

**Given:**
- The capability grant is present in the server environment

**When:** A client requests a destructive run

**Then:**
- The tool applies the caller's requested mode
- The response reports the work actually performed

**Command-line path stays independent of the grant** (`rq-destructiveGrant01.3`)

**Given:**
- The capability grant is absent

**When:** An operator runs the destructive command-line invocation with the execute flag

**Then:**
- The command performs the deletion
- The reported outcome matches the operator's requested mode

---
