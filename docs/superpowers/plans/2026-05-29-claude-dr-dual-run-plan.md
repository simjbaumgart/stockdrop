# Claude Opus 4.8 Deep Research — Live Dual-Run + Comparison

> **For Claude Code:** This is a kickoff brief, not the final task list.
> First use **`superpowers:writing-plans`** to expand it into a task-by-task plan,
> then **`superpowers:executing-plans`** to implement it, following the house
> TDD convention (write the failing test first). Work on branch
> `feat/claude-deep-research-shadow`. **No GitHub** — commit locally only.
>
> **Supersedes** `docs/superpowers/plans/2026-05-29-claude-deep-research-provider.md`
> (on branch `feature/claude-deep-research`) — the opt-in provider + offline shadow.
> That work, the Claude provider returning the correct result-dict shape, is the
> foundation this builds on.

**Skills to use (in order):**
- `superpowers:writing-plans` — turn this brief into a detailed plan.
- `superpowers:test-driven-development` — failing test first for each task.
- `superpowers:executing-plans` — implement task-by-task.
- Any project skills under `.claude/skills/` relevant to the touched services.

---

## Goal

Today the two Deep Research (DR) systems cannot be compared on a live, like-for-like
basis. Close that gap in three steps:

1. **Live dual-run** — when DR triggers, run **both** the Gemini DR and the Claude
   Opus 4.8 DR on the same stock, and store both results.
2. **Tailored Claude prompt** — give Claude its own real prompt, not a regex swap of
   Gemini's.
3. **Proper comparison** — compare verdict, action, **and the buy/sell numbers**,
   using metrics that survive the data's quirks.

Gemini stays **authoritative** for any live trading decision during the comparison
period. Claude runs as a **challenger** alongside it. This is a measurement system,
not a provider switch.

---

## Established findings — DO NOT re-derive these

All of the below were verified against the code and the `subscribers.db` data on
2026-05-29. Treat them as given.

**Current architecture**
- Routing is **either/or**, not dual: `DEEP_RESEARCH_PROVIDER` env var sends each
  decision to Gemini *or* Claude (`deep_research_service.py` `_provider()`, ~line 1081).
- Claude only sees stocks via the **offline** script
  `scripts/analysis/claude_deep_research_shadow.py`, which replays already-stored
  Gemini decisions. There is no live parallel run.
- The Claude provider (`claude_deep_research_service.py`) already returns the correct
  result shape (two-phase: research loop → structured synthesis). The hard part works.

**Prompt**
- Claude reuses Gemini's `_construct_prompt` verbatim, then `_deglooglify` regex-swaps
  only the literal phrase "Google Search" → "web search". This leaves Gemini-shaped
  logic intact, e.g. "paywalled sources — NOT available via web search", which is
  **false for Claude** (its `web_fetch` can reach some of those pages).

**Cost accounting bug**
- `_record_cost(..., research, {})` passes an **empty** synthesis-usage dict, and
  `_synthesize` never returns usage — so synthesis tokens are **not counted**.
- `_synthesize` re-sends `transcript[:120000]` to a **fresh, uncached** Opus call.
  This is the only uncached call and the cost blind spot. Fix accounting first.

**Level anchoring contamination (gates the buy/sell comparison)**
- The base columns (`entry_price_low`, `stop_loss`, `take_profit_*`, `sell_price_*`,
  `ceiling_exit`, `risk_reward_ratio`) are **byte-identical** to the `deep_research_*`
  columns on every row — the DR overwrites the PM's levels in place
  (`deep_research_service.py` ~line 771).
- The shadow's `_rebuild_context` builds the `pm_decision` it feeds Claude from those
  base columns. So Claude is reviewing **Gemini's already-refined levels**, anchored on
  the exact numbers we want to compare. A clean comparison needs the PM's **pre-DR**
  levels as the baseline.

**Enum alignment (verified in DB)**
- `action`: Gemini stores `BUY, BUY_LIMIT, WATCH, AVOID` — **exact match** to Claude's
  enum. `agree_action` is valid as-is.
- `review_verdict`: real values match (`CONFIRMED, UPGRADED, ADJUSTED, OVERRIDDEN`), but
  Gemini also stores two failure sentinels Claude can never emit: `ERROR_PARSING` (3
  rows) and `INCOMPLETE_TRADING_LEVELS` (1 row). Exclude these from agreement.

**Agreement metric is base-rate-inflated**
- Verdicts are ~96% `ADJUSTED`/`OVERRIDDEN`; actions are dominated by `BUY_LIMIT`/`AVOID`.
  A raw "X/N agree" fraction overstates real alignment. Report a **confusion matrix**
  and **Cohen's κ**, not just a percentage.

**Caching — already solved, do not touch**
- Intra-decision caching works (cache_read ~221k vs 17 fresh tokens on MPNGY). The
  cross-decision win is marginal and TTL-fragile. Out of scope.

---

## Input files (read before planning)

- `app/services/claude_deep_research_service.py` — the Claude provider.
- `app/services/deep_research_service.py` — routing (`_provider`, ~1081), `_construct_prompt`
  (~1188), the level write-back (~771).
- `app/services/deep_research_schemas.py` — Claude's output schemas.
- `scripts/analysis/claude_deep_research_shadow.py` — the offline shadow + `_rebuild_context`.
- `app/services/token_pricing.py` — cost computation.
- `data/claude_shadow/*.json` — existing 3-decision shadow output (MPNGY, STEP, ZWS).
- `data/council_reports/<ticker>_<date>_council2.json` — candidate source of the PM's
  pre-DR levels (confirm — see Open Decisions).

---

## Step 1 — Live dual-run

Run Claude DR alongside Gemini DR whenever DR triggers, without slowing the live
(Gemini-authoritative) path. Claude takes ~285s per decision, so it must run
**off the critical path** — a background thread/queue, mirroring how the Gemini
deep-research worker is decoupled. Both result sets are persisted for comparison.

**Likely outputs:**
- `app/services/deep_research_service.py` — add a "dual"/"compare" mode to routing so
  both providers run; Gemini's result remains the one used for the live decision.
- New `app/services/dr_comparison_service.py` (or similar) — owns running the challenger
  and writing the paired record. Keep blocking I/O off the asyncio loop.
- DB migration — store the Claude challenger result in a **dedicated `dr_comparison`
  table** (DECIDED — do not add columns to the already-88-wide `decision_points`).
  Check `app/database.py` for the migration pattern first.
- Tests covering: both providers invoked, Gemini stays authoritative, challenger failure
  never breaks the live path.

---

## Step 2 — Tailored Claude prompt (genuine deep-research mandate)

Replace the regex `_deglooglify` with a purpose-built Claude prompt. **Keep the review
scaffolding** (the "what's priced in" / Elm Partners reminder, `knife_catch_warning`,
`council_blindspots`, the external-driver-dominance check) — it's good. But layer a real
deep-research mandate on top so it becomes "investigate independently, informed by what
the council found," not "audit the council's 3 claims." Same desired output schema (plus
one new field, below).

**Prompt requirements:**
- **Provider-neutral source framing** — remove the "not available via web search" claim;
  state that `web_fetch` may reach some paywalled pages.
- **Search-first, multi-hop directive naming primary sources** — begin searching
  immediately, follow leads across rounds, don't stop after one. Explicitly name primary
  sources to pull: EDGAR filings, the earnings-call transcript, company IR pages, Form 4
  (insider trades), 13F (ownership shifts), short interest — not just secondary news.
  (Opus under-reaches for search when a system prompt is present, so this must be explicit.)
- **Tell Claude which council agents already ran and what each covered** (Technical, News,
  Sentiment, Competitive, Seeking Alpha) so it stops re-doing their work and spends its
  hops on what they couldn't see.
- **Feed the bull-vs-bear points of disagreement explicitly and make them the priority
  research targets.** The contested claims are where independent research pays off — this
  is where Claude caught Gemini's hallucinated "CH Equity Partners" entity in the shadow.
- **Relax STEP 1** from "verify the top 3 claims" to "independently establish the true
  cause of the drop, then reconcile with the council," and **uncap** the number of claims.
  Require a short **dated event timeline** (what happened, when, and the market's reaction
  vs. the news).
- **Add a "what I could NOT verify" field** to the output (and schema): force the model to
  list its unknowns. Unverified load-bearing claims are exactly the risk signal to surface.
- Keep prompt text in dedicated constants / a prompt module, not inline (house code style).
- Output must still validate against the schemas (now extended with the unknowns field).

**Data dependency (touches the context builder, not just the prompt):**
- Several of the above need data the live DR path does NOT currently pass — the live
  `_construct_prompt` only sends PM decision + bull + bear (truncated to 4,000 chars) +
  technical + news. It omits the Sentiment, Competitive, and Seeking Alpha sensor reports.
- So the context assembly must be extended to pass **condensed sensor-agent summaries**
  (from `data/council_reports/<ticker>_<date>_council1.json`) and the **bull/bear
  disagreement points**.
- **Stop hard-truncating bull/bear to 4,000 chars for the Claude path** — summarize to
  preserve substance instead of cutting mid-argument.

**Likely outputs:**
- New prompt builder in a `claude_dr_prompts.py` module; remove reliance on `_deglooglify`.
- Extend `deep_research_schemas.py` (`INDIVIDUAL_SCHEMA` / `SELL_SCHEMA`) with the
  "could-not-verify" field.
- Extend the Claude context assembly to include council1 sensor summaries + bull/bear
  disagreement, and replace hard truncation with summarization.
- Tests asserting: the prompt contains the search-first + primary-source block, names the
  five council agents, omits Gemini-specific framing, and the new schema field round-trips.

---

## Step 3 — Proper comparison (verdict + action + buy/sell numbers)

Make the comparison trustworthy and extend it to the trading numbers.

**3a. Fix the metric (gates any quotable agreement number):**
- Exclude `ERROR_PARSING` / `INCOMPLETE_TRADING_LEVELS` rows from `agree_verdict`.
- Report a **confusion matrix** for both verdict and action, plus **Cohen's κ**.
- Populate the shadow context's news from the `council2` news summary (narrows the
  `raw_news=[]` gap; cannot fully close it for historical rows).

**3b. Add the buy/sell number comparison (the main new capability):**
Capture for **both** models: `entry_price_low/high`, `stop_loss`, `take_profit_1/2`,
`sell_price_low/high`, `ceiling_exit`, `risk_reward_ratio`, and the text fields
`entry_trigger` / `exit_trigger`.

Compare numerically (never by equality):
- entry zone — band overlap fraction + midpoint % delta
- `stop_loss`, each take-profit, each sell bound, `ceiling_exit` — % delta
- `risk_reward_ratio` — absolute delta
- triggers — side-by-side text; flag empties
- **materiality flag:** mark a real disagreement when entry midpoints differ >3%,
  stop differs >5%, or entry bands don't overlap. (This also auto-surfaces incoherent
  levels, e.g. STEP's `risk_reward_ratio = 0.0` with TP below entry.)

**3c. Fix the anchoring contamination (prerequisite for 3b to mean anything):**
- Feed Claude the PM's **pre-DR** levels, not the DR-overwritten base columns.
- **You must find the source of the pre-DR levels yourself** — do not assume.
  Trace the level write-back (`deep_research_service.py` ~771) backwards and inspect
  the council report files (`data/council_reports/<ticker>_<date>_council1.json` and
  `_council2.json`) and any pre-DR snapshot. The PM's original entry/stop/TP must exist
  somewhere before the DR overwrites the base columns.
- **Write a guard test that proves the baseline is genuinely pre-DR:** assert the
  recovered PM levels are NOT byte-identical to the `deep_research_*` columns on a known
  decision (they are identical today, which is the bug). If no clean pre-DR source
  exists, FAIL LOUDLY and surface it — do not silently fall back to the base columns,
  because that reintroduces the anchoring.

**Likely outputs:**
- `scripts/analysis/claude_deep_research_shadow.py` — metric fixes + level capture.
- New comparison module (e.g. `app/services/analytics/dr_level_compare.py`) with the
  numeric diff + materiality logic, unit-tested on the existing 3 shadow records.
- A summary report (markdown) showing verdict/action confusion matrices, κ, and the
  level-disagreement table.

---

## Measurement strategy (not just full-15 replay)

- **Do not** scale up the news-stripped historical shadow as a cost/depth benchmark —
  it multiplies a measurement taken on inputs the live path doesn't use.
- Use the full-15 historical replay only as a **judgment-divergence** check (where does
  Claude disagree with Gemini, and is it sharper — e.g. the STEP hallucination catch).
- Get the **definitive** cost/depth/quality read from 2–3 **live** dual-runs on fresh
  drops (Step 1), where Claude receives real `raw_news` for free.

---

## Documentation step

Because this spans architecture + prompt + metrics, write a short design note before
coding: `docs/superpowers/specs/2026-05-29-claude-dr-dual-run-design.md`, recording the
three decisions below and the comparison metrics. Update it if decisions change during
implementation.

---

## Decisions (resolved)

1. **Authoritative provider during the comparison period — DECIDED: Gemini stays
   live-authoritative.** Claude runs purely as a challenger; its result never drives a
   live trade during this period.
2. **Storage shape — DECIDED: a dedicated `dr_comparison` table.** Do not grow the
   88-column `decision_points`.
3. **Source of the PM's pre-DR levels — DELEGATED to you (Cursor), not pre-decided.**
   Find it yourself per Step 3c: trace the write-back, inspect the council files, and
   prove the recovered baseline is pre-DR with a guard test. Fail loudly if no clean
   source exists rather than falling back to the contaminated base columns.
