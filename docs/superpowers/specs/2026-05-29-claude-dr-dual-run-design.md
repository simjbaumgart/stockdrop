# Claude DR Dual-Run + Comparison — Design Note

**Status:** design, pre-implementation. Companion to `docs/superpowers/plans/2026-05-29-claude-dr-dual-run-plan.md`. Update as decisions change during implementation.

## Purpose

The Gemini and Claude Opus 4.8 Deep Research systems can't be compared live, like-for-like. This builds a **measurement system**: on every DR trigger, run both, store both, compare verdict + action + the buy/sell numbers. **Gemini stays authoritative** for any live trade during the comparison period; Claude is a **challenger**. Not a provider switch.

## Decisions (resolved)

1. **Authoritative provider — Gemini.** Claude's result never drives a live trade during the comparison period. It is recorded and compared only.
2. **Storage — a dedicated `dr_comparison` table.** Do not add columns to the already-88-wide `decision_points`. Follow the migration pattern in `app/database.py`.
3. **Pre-DR level baseline — DELEGATED (investigation, Step 3c).** The clean comparison needs the PM's *pre-DR* entry/stop/TP, but the DR overwrites those base columns in place (`deep_research_service.py` ~771), so the base columns are byte-identical to `deep_research_*`. The source of the pre-DR levels must be *found* (trace the write-back; inspect `council1.json`/`council2.json`/any pre-DR snapshot) and proven pre-DR with a guard test. **If no clean source exists, fail loudly** — never silently fall back to the contaminated base columns.

## Comparison metrics

**Verdict & action (categorical):**
- Exclude the two Gemini failure sentinels `ERROR_PARSING` (3 rows) and `INCOMPLETE_TRADING_LEVELS` (1 row) from verdict agreement — Claude can never emit them, so they force spurious disagreement.
- `action` enums match exactly (`BUY/BUY_LIMIT/WATCH/AVOID`); `review_verdict` real values match (`CONFIRMED/UPGRADED/ADJUSTED/OVERRIDDEN`).
- Report a **confusion matrix + Cohen's κ** for both verdict and action — NOT a raw agreement %. Base rates are skewed (verdicts ~96% ADJUSTED/OVERRIDDEN; actions dominated by BUY_LIMIT/AVOID), so raw agreement is inflated.

**Buy/sell numbers (continuous) — the main new capability:**
Capture for both models: `entry_price_low/high`, `stop_loss`, `take_profit_1/2`, `sell_price_low/high`, `ceiling_exit`, `risk_reward_ratio`, and text `entry_trigger`/`exit_trigger`. Compare numerically (never equality):
- entry zone → band-overlap fraction + midpoint % delta
- `stop_loss`, each TP, each sell bound, `ceiling_exit` → % delta
- `risk_reward_ratio` → absolute delta
- triggers → side-by-side text; flag empties
- **Materiality flag** (real disagreement) when: entry midpoints differ **>3%**, OR stop differs **>5%**, OR entry bands don't overlap. (Also auto-surfaces incoherent levels, e.g. STEP's `risk_reward_ratio=0.0` with TP below entry.)

## Cost accounting fix

`_record_cost(..., research, {})` drops synthesis usage; `_synthesize` re-sends `transcript[:120000]` to a fresh **uncached** Opus call (the only uncached call). Make `_synthesize` return its usage and fold it into the cost record so per-decision $ is honest. (Research phase is already cache-efficient: cache_read ~221k vs 17 fresh on MPNGY.)

## Prompt mandate (Step 2 summary)

Replace the `_deglooglify` regex with a purpose-built Claude prompt that keeps the review scaffolding (Elm-Partners "what's priced in", `knife_catch_warning`, `council_blindspots`, external-driver dominance) but adds a genuine deep-research mandate: provider-neutral source framing, a search-first multi-hop directive naming primary sources (EDGAR, earnings transcript, IR, Form 4, 13F, short interest), awareness of which council agents already ran (Technical/News/Sentiment/Competitive/Seeking Alpha) so it targets gaps, the bull/bear disagreement points as priority targets, a relaxed STEP 1 ("establish the true cause, then reconcile") with an uncapped claim count + a dated event timeline, and a new **"what I could NOT verify"** output field (schema-extended). Context assembly must pass condensed council1 sensor summaries + bull/bear disagreement and stop hard-truncating bull/bear to 4,000 chars (summarize instead).

## Measurement strategy

- The news-stripped historical shadow (`raw_news=[]`) is **only** a judgment-divergence check, not a cost/depth benchmark — its inputs differ from production.
- Definitive cost/depth/quality come from **2–3 live dual-runs on fresh drops**, where Claude receives real `raw_news`.

## Open question — `code_execution` vs docs (measure, don't assume)

The Phase-1 research loop currently includes `code_execution_20260120` (in-code comment: "MUST be present for web_search dynamic filtering"). The claude-api skill says the opposite — dynamic filtering is built into `web_search_20260209` and pairing `code_execution` "creates a second execution environment that can confuse the model." The live dual-run gives a clean A/B surface: run a couple decisions with and without `code_execution`, compare search count / cost / quality, then keep or drop. Until settled, leave it in (the live test passed with it).
