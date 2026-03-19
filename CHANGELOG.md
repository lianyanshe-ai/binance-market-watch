# Changelog

All notable changes to this repository will be documented in this file.

## 2026-03-19

### Added

- Added built-in USDS-M futures analysis entrypoint at `skills/binance-market-watch/scripts/binance_usds_futures_advisor.py`
- Added futures endpoint reference at `skills/binance-market-watch/references/usds-futures-endpoints.md`
- Added release update document [`UPDATE-2026-03-19.md`](./UPDATE-2026-03-19.md)

### Changed

- Expanded `binance-market-watch` into a unified standalone skill that covers:
  - Hourly Top10 market monitoring
  - Binance USDS-M public futures analysis
- Updated `skills/binance-market-watch/SKILL.md` to document both workflows and their CLI usage
- Updated `skills/binance-market-watch/agents/openai.yaml` to reflect the expanded prompt surface
- Updated `skills/binance-market-watch/references/implementation.md` and `references/openclaw-setup.md` to document the integrated setup and cron usage
- Reworked `README.md` into a GitHub-ready bilingual guide covering installation, quick start, built-in futures analysis, and Telegram cron examples

### Verification

- Verified the built-in futures script help output
- Ran `python3 -m py_compile` against the release package Python entrypoints
- Real Binance heartbeat/report integration had already been validated in the workspace before syncing this release repository
