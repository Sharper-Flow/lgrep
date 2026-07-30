# Lgrep Candidate Reference Lookup

> **Version:** 1.0.0
> **Updated:** 2026-07-30

## Purpose

Capability: Lgrep Candidate Reference Lookup

## Requirements

### Candidate lookup reports distinct filters, visible truncation, and result freshness

**ID:** `rq-lookupHonesty01` | **Priority:** **[MUST]**

Candidate reference lookup MUST make its declared usage filters observably distinct. A filter that promises to include test occurrences MUST return test occurrences when they exist, even when production occurrences alone would exceed the result cap. Responses MUST report how many production and test occurrences matched and how many of each were returned, so truncation is visible rather than inferred. Each returned occurrence MUST carry a freshness indicator derived from comparing the backing file's current content against the indexed content, and the response MUST summarise how many backing files were stale. A backing file that no longer exists MUST be reported as stale rather than raising an error. Freshness is reported, never repaired: lookup MUST NOT re-index as a side effect. Freshness work MUST be bounded by the returned result slice, not by repository size.

**Tags:** `correctness`, `observability`, `freshness`, `search`, `candidate-semantics`

#### Scenarios

**Include-tests surfaces tests beyond the cap** (`rq-lookupHonesty01.1`)

**Given:**
- A symbol has more production occurrences than the result cap
- The same symbol has at least one test occurrence

**When:** The caller requests the include-tests filter

**Then:**
- The response contains at least one test occurrence
- The response differs from the production-first response for the same query

**Truncation is reported** (`rq-lookupHonesty01.2`)

**Given:**
- Matching occurrences exceed the result cap

**When:** The caller runs a lookup

**Then:**
- The response reports the number of matching production and test occurrences
- The response reports how many of each were returned

**Stale occurrences are flagged and recoverable** (`rq-lookupHonesty01.3`)

**Given:**
- A file is indexed
- The file is then modified so its content no longer matches the index

**When:** The caller runs a lookup returning occurrences from that file

**Then:**
- Those results are marked stale
- The response summarises the count of stale backing files
- Re-indexing the repository clears the stale marks

---
