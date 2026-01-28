# Changelog

## [0.8.1] - 2026-01-28
### Changed
- Refactored `scripts/` directory by moving maintenance and verification scripts to `scripts/archive/`.
- Removed `PEEstimateManager` (logic now integrated directly into agent prompts).

### Added
- Added P/E estimates (Bull/Bear case forward P/E and EPS impact) to Council 2 (Bull & Bear Agents).
- Added `Risk Management Agent` to the second council to provide a "Devil's Advocate" perspective.
