# Changelog

All notable changes to this project are documented in this file.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
This project adheres to [Semantic Versioning](https://semver.org/).

## [v0.1.1] — 2026-05-01

### Documentation

- Surfaced JPO API rate limits at the top level: README and README.en gained
  a dedicated "Rate limits (operations)" section.
- Added "JPO API レート制約とクォータ" section to OPERATIONS.md with a tool →
  endpoint mapping table and operational thresholds.
- Clarified the quota cost of `jpo_fetch_full_record` in its tool description:
  one call consumes 1 unit from each of 4 separate daily quotas
  (`case_number_reference`, `app_progress`, `registration_info`,
  `cite_doc_info`). The previous wording suggested a single quota was drawn
  on, which understated the per-call cost.

### Chore

- Resized the Open Graph banner to 1280×640 PNG and re-optimized it to fit
  GitHub's 1 MB upload cap.

## [v0.1.0] — 2026-05-01

First public-ready cut. Implements Phase 1A (official JPO tools), Phase 1B
(isolated external keyword search), and Phase 1.5 (operational hardening —
OAuth persistence, access log, ops docs).

### Added

- **12 official JPO tools** (`tools_official/`, names start with `jpo_*`):
  number conversion, examination progress, registration, citations,
  divisional / priority apps, applicant lookup, patent documents (binary
  ZIP + signed URL), J-PlatPat URL, OPD family / doc list, composite
  full-record fetch.
- **1 isolated keyword-search tool**
  (`tools_external/external_search_patents_by_keyword`) via Google
  Patents XHR. Code-level boundary test forbids any import from the
  external module into the official module — no silent fallback.
- **OAuth 2.1 server** with Dynamic Client Registration + PKCE +
  master-password consent, **persisted to SQLite** so issued tokens
  survive container restarts. Unauthenticated mode also supported for
  LAN-only deploys.
- **JSONL access log** at `logs/access.jsonl` recording every JPO and
  external call with timestamp, endpoint, elapsed time, outcome, and —
  for JPO calls — the live `remainAccessCount` quota counter. CLI
  summary script in `scripts/summarize_logs.py`.
- **Docker Compose** deployment with Caddy + Let's Encrypt for HTTPS
  exposure. Tested with iPhone Claude / claude.ai Custom Connectors.
- **63 tests** (provider, access log, raw-response binary detection,
  tool-boundary isolation, status-code parsing, normalization). CI on
  push/PR via GitHub Actions (`pytest` + `ruff`).

### Architectural rules (do not regress)

- `tools_official/` and `tools_external/` are completely separate code
  hierarchies; cross-imports are blocked by a unit test.
- Every response carries an explicit `source` field (`"jpo_official"` or
  `"google_patents_unofficial"`) so the LLM can never misattribute data.
- Same-source retry only: HTTP 401 / `statusCode 210` triggers a token
  refresh; `statusCode 303` triggers exponential backoff. No
  cross-source fallback under any error.

### Known limitations

- LAN-deploy + public-OAuth deploy are documented; multi-user setups
  are out of scope for v0.1.x.
- Phase 2 (refusal-reason PDF structured extraction, AI-assisted review,
  EPO OPS / WIPO PATENTSCOPE supplements) deferred.

[v0.1.1]: https://github.com/kitepon-rgb/IP-MCP/compare/v0.1.0...v0.1.1
[v0.1.0]: https://github.com/kitepon-rgb/IP-MCP/releases/tag/v0.1.0
