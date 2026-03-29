# Technical Risk Flags — Dual-Track Proposal

## Overview

Replace the fragile string-matching in `_run_risk_council_and_decision()` with a **dual-track** system:

| Track | Source | Purpose |
|-------|--------|---------|
| **Agent Verdict** | Technical Agent LLM (Gemini) | Contextual, nuanced judgment — **has final call** |
| **Deterministic Flags** | Raw Python on TradingView numbers | Objective baseline — **sanity check** |

Both run in parallel during Phase 1. The console prints them side-by-side so you can visually compare. The Agent verdict is authoritative and gets passed to the Portfolio Manager; the deterministic flags are shown for transparency and logged.

---

## 1. Updated Technical Agent Prompt

**File:** `research_service.py` → `_create_technical_agent_prompt()`

The key change: after the prose playbook, the agent MUST append a `## STRUCTURED_VERDICT` JSON block with explicit boolean flags and an overall risk tier.

```python
def _create_technical_agent_prompt(self, state: MarketState, raw_data: Dict, drop_str: str) -> str:
    indicators = raw_data.get('indicators', {})
    transcript = raw_data.get('transcript_text', "No transcript available.")
    transcript_snippet = transcript[:2000] + "..." if len(transcript) > 2000 else transcript

    return f"""
You are the **Technical Analyst Agent**.
Your goal is to analyze the price action and technical health of {state.ticker}.
Correlate technical signals with the **Fundamental Context** from the earnings transcript.

CONTEXT: The stock has dropped {drop_str} recently. This is a "Buy the Dip" evaluation.

INPUT DATA:
1. TECHNICAL INDICATORS:
{json.dumps(indicators, indent=2)}

2. QUARTERLY REPORT SNIPPET (Truncated):
{transcript_snippet}

═══════════════════════════════════════════════════
TASK — TWO PARTS:
═══════════════════════════════════════════════════

PART 1: DETAILED PLAYBOOK (prose)
Analyze the following dimensions:
- **Oversold / Overbought Status**: Is RSI < 30 (oversold) or > 70 (overbought)? Stochastic K/D levels? CCI extremes?
- **Trend Health**: Is MACD histogram positive or negative? Is price above or below SMA50 / SMA200? ADX strength?
- **Momentum Divergence**: Is MACD histogram declining while price is above SMA50? (bearish divergence signal)
- **Volatility Context**: Where is price relative to Bollinger Bands? Is ATR elevated vs. normal?
- **Volume Signal**: Is relative volume (rvol) elevated (> 2x = capitulation/panic)?
- **Earnings Cross-Reference**: Does the transcript explain the price action? CEO/CFO commentary on outlook?
Use headers: "Technical Signal", "Oversold Status", "Trend Analysis", "Momentum", "Context from Report", "Verdict".

PART 2: STRUCTURED VERDICT (JSON)
After your playbook, you MUST output a section starting with exactly:
## STRUCTURED_VERDICT
followed by a JSON code block. This is parsed programmatically — do NOT deviate from the format.

The JSON must contain these exact fields:

```json
{{
  "overbought": <boolean — true if RSI > 70 OR (Stoch K > 80 AND RSI > 65)>,
  "oversold": <boolean — true if RSI < 30 OR (Stoch K < 20 AND RSI < 35)>,
  "bearish_divergence": <boolean — true if momentum is fading despite price holding above key averages>,
  "weak_trend": <boolean — true if ADX < 20 indicating choppy/trendless market>,
  "below_sma200": <boolean — true if close < SMA200, indicating long-term downtrend>,
  "volume_spike": <boolean — true if relative volume > 2.5x (capitulation/panic selling)>,
  "cci_extreme": "OVERBOUGHT" | "OVERSOLD" | "NEUTRAL",
  "macd_bearish": <boolean — true if MACD histogram < 0>,
  "bb_position": "BELOW_LOWER" | "NEAR_LOWER" | "MIDDLE" | "NEAR_UPPER" | "ABOVE_UPPER",
  "overall_risk_tier": "HIGH_RISK" | "MODERATE_RISK" | "LOW_RISK",
  "risk_flags_summary": ["<human-readable string for each active flag — e.g. 'RSI at 74 — overbought'>"]
}}
```

Rules for `overall_risk_tier`:
- HIGH_RISK: 2+ risk flags active (overbought, bearish_divergence, weak_trend, below_sma200)
- MODERATE_RISK: 1 risk flag active
- LOW_RISK: 0 risk flags active (oversold is a positive signal for dip-buying, not a risk)

Rules for `bb_position`:
- BELOW_LOWER: close < bb_lower
- NEAR_LOWER: close within 1% of bb_lower
- MIDDLE: between bb_lower + 1% and bb_upper - 1%
- NEAR_UPPER: close within 1% of bb_upper
- ABOVE_UPPER: close > bb_upper

IMPORTANT: Base your boolean flags on the actual indicator values provided, not on general market knowledge. If an indicator is missing or null, set the corresponding flag to false and note it in risk_flags_summary.
"""
```

### How to Extract the Verdict

Add a new helper method to parse the structured verdict from the Technical Agent's response:

```python
def _extract_technical_verdict(self, tech_report: str) -> dict:
    """
    Extracts the STRUCTURED_VERDICT JSON from the Technical Agent's output.
    Returns the parsed dict, or a fallback if parsing fails.
    """
    fallback = {
        "overbought": False, "oversold": False, "bearish_divergence": False,
        "weak_trend": False, "below_sma200": False, "volume_spike": False,
        "cci_extreme": "NEUTRAL", "macd_bearish": False,
        "bb_position": "MIDDLE", "overall_risk_tier": "MODERATE_RISK",
        "risk_flags_summary": ["[PARSE ERROR] Could not extract structured verdict from Technical Agent"]
    }

    try:
        # Find the STRUCTURED_VERDICT section
        marker = "## STRUCTURED_VERDICT"
        if marker not in tech_report:
            logger.warning("Technical Agent did not produce STRUCTURED_VERDICT section")
            return fallback

        verdict_section = tech_report.split(marker, 1)[1]

        # Extract JSON from code block or raw text
        import re
        json_match = re.search(r'```(?:json)?\s*(\{.*?\})\s*```', verdict_section, re.DOTALL)
        if json_match:
            return json.loads(json_match.group(1))

        # Try raw JSON (no code block)
        json_match = re.search(r'(\{.*\})', verdict_section, re.DOTALL)
        if json_match:
            return json.loads(json_match.group(1))

    except (json.JSONDecodeError, Exception) as e:
        logger.error(f"Failed to parse technical verdict JSON: {e}")

    return fallback
```

---

## 2. Deterministic Risk Flags (Hardcoded Python)

**New method** in `ResearchService` — runs on raw indicator numbers, zero LLM involvement.

```python
def _compute_deterministic_risk_flags(self, indicators: dict) -> dict:
    """
    Compute risk flags from raw TradingView indicators.
    Runs in parallel with the Technical Agent.
    This is the objective baseline — no LLM interpretation.
    """
    flags = {
        "overbought": False,
        "oversold": False,
        "bearish_divergence": False,
        "weak_trend": False,
        "below_sma200": False,
        "volume_spike": False,
        "cci_extreme": "NEUTRAL",
        "macd_bearish": False,
        "bb_position": "MIDDLE",
        "overall_risk_tier": "LOW_RISK",
        "risk_flags_summary": []
    }

    # Safely extract values (TradingView may return None for some)
    rsi = indicators.get('rsi')
    macd_hist = indicators.get('macd_hist')
    adx = indicators.get('adx')
    close = indicators.get('close')
    sma50 = indicators.get('sma50')
    sma200 = indicators.get('sma200')
    stoch_k = indicators.get('stoch_k')
    stoch_d = indicators.get('stoch_d')
    cci = indicators.get('cci')
    rvol = indicators.get('rvol')
    bb_lower = indicators.get('bb_lower')
    bb_upper = indicators.get('bb_upper')
    atr = indicators.get('atr')

    risk_count = 0  # Count of negative risk flags

    # --- Flag 1: Overbought ---
    if rsi is not None and rsi > 70:
        flags["overbought"] = True
        flags["risk_flags_summary"].append(f"RSI at {rsi:.1f} — overbought (>70)")
        risk_count += 1
    elif stoch_k is not None and rsi is not None and stoch_k > 80 and rsi > 65:
        flags["overbought"] = True
        flags["risk_flags_summary"].append(f"Double overbought: Stoch K={stoch_k:.1f}, RSI={rsi:.1f}")
        risk_count += 1

    # --- Flag 2: Oversold (positive for dip-buying — NOT a risk) ---
    if rsi is not None and rsi < 30:
        flags["oversold"] = True
        flags["risk_flags_summary"].append(f"RSI at {rsi:.1f} — oversold (<30, favorable for dip)")
    elif stoch_k is not None and rsi is not None and stoch_k < 20 and rsi < 35:
        flags["oversold"] = True
        flags["risk_flags_summary"].append(f"Double oversold: Stoch K={stoch_k:.1f}, RSI={rsi:.1f} (favorable)")

    # --- Flag 3: Bearish Momentum Divergence ---
    # MACD histogram negative while price still above SMA50 = momentum fading
    if macd_hist is not None and close is not None and sma50 is not None:
        if macd_hist < 0 and close > sma50:
            flags["bearish_divergence"] = True
            flags["risk_flags_summary"].append(
                f"Bearish divergence: MACD hist={macd_hist:.3f} (<0) but close ${close:.2f} > SMA50 ${sma50:.2f}"
            )
            risk_count += 1

    # --- Flag 4: Weak/Choppy Trend ---
    if adx is not None and adx < 20:
        flags["weak_trend"] = True
        flags["risk_flags_summary"].append(f"ADX at {adx:.1f} — weak/choppy trend (<20)")
        risk_count += 1

    # --- Flag 5: Below SMA200 (Long-term Downtrend) ---
    if close is not None and sma200 is not None and close < sma200:
        flags["below_sma200"] = True
        pct_below = ((sma200 - close) / sma200) * 100
        flags["risk_flags_summary"].append(
            f"Below SMA200: close ${close:.2f} vs SMA200 ${sma200:.2f} ({pct_below:.1f}% below)"
        )
        risk_count += 1

    # --- Flag 6: Volume Spike ---
    if rvol is not None and rvol > 2.5:
        flags["volume_spike"] = True
        flags["risk_flags_summary"].append(f"Volume spike: relative volume {rvol:.1f}x (>2.5x normal)")
        # Not counted as risk — could be capitulation (bullish for dip) or panic (bearish)

    # --- Flag 7: CCI Extreme ---
    if cci is not None:
        if cci > 200:
            flags["cci_extreme"] = "OVERBOUGHT"
            flags["risk_flags_summary"].append(f"CCI at {cci:.1f} — overbought extreme (>200)")
        elif cci < -200:
            flags["cci_extreme"] = "OVERSOLD"
            flags["risk_flags_summary"].append(f"CCI at {cci:.1f} — deeply oversold (<-200, favorable)")

    # --- Flag 8: MACD Bearish ---
    if macd_hist is not None and macd_hist < 0:
        flags["macd_bearish"] = True
        if not flags["bearish_divergence"]:  # Don't double-report
            flags["risk_flags_summary"].append(f"MACD histogram negative: {macd_hist:.3f}")

    # --- Bollinger Band Position ---
    if close is not None and bb_lower is not None and bb_upper is not None:
        bb_range = bb_upper - bb_lower
        if bb_range > 0:
            if close < bb_lower:
                flags["bb_position"] = "BELOW_LOWER"
                flags["risk_flags_summary"].append(f"Price BELOW BB lower: ${close:.2f} < ${bb_lower:.2f}")
            elif close <= bb_lower + bb_range * 0.05:
                flags["bb_position"] = "NEAR_LOWER"
            elif close >= bb_upper - bb_range * 0.05:
                flags["bb_position"] = "NEAR_UPPER"
            elif close > bb_upper:
                flags["bb_position"] = "ABOVE_UPPER"
            else:
                flags["bb_position"] = "MIDDLE"

    # --- Overall Risk Tier ---
    if risk_count >= 2:
        flags["overall_risk_tier"] = "HIGH_RISK"
    elif risk_count == 1:
        flags["overall_risk_tier"] = "MODERATE_RISK"
    else:
        flags["overall_risk_tier"] = "LOW_RISK"

    # Add summary line if no flags triggered
    if not flags["risk_flags_summary"]:
        flags["risk_flags_summary"].append("No risk flags detected — indicators are neutral")

    return flags
```

### Key Design Choices

- **Oversold is NOT a risk flag** — it's actually favorable for dip-buying. It's tracked but doesn't increase `risk_count`.
- **Volume spike is NOT counted as risk** — it's ambiguous (could be capitulation = bullish, or panic = bearish). Flagged for visibility only.
- **CCI oversold (<-200) is favorable** — same as RSI oversold, noted but not penalized.
- Thresholds are conservative and match standard TA textbook values.

---

## 3. Console Comparison Output

**New method** that prints both tracks side-by-side with MATCH/MISMATCH indicators:

```python
def _print_risk_flag_comparison(self, agent_verdict: dict, deterministic_flags: dict):
    """
    Prints a side-by-side comparison of Agent vs. Deterministic risk flags.
    Shows MATCH (✓) or MISMATCH (✗) for each flag.
    """
    print("\n" + "─"*60)
    print("  TECHNICAL RISK FLAGS — DUAL-TRACK COMPARISON")
    print("─"*60)

    # Define the flags to compare (booleans)
    bool_flags = [
        ("overbought",          "Overbought"),
        ("oversold",            "Oversold (favorable)"),
        ("bearish_divergence",  "Bearish Divergence"),
        ("weak_trend",          "Weak Trend (ADX)"),
        ("below_sma200",        "Below SMA200"),
        ("volume_spike",        "Volume Spike"),
        ("macd_bearish",        "MACD Bearish"),
    ]

    match_count = 0
    total_flags = 0

    for key, label in bool_flags:
        agent_val = agent_verdict.get(key, False)
        determ_val = deterministic_flags.get(key, False)
        match = agent_val == determ_val
        match_count += 1 if match else 0
        total_flags += 1

        symbol = "✓" if match else "✗"
        agent_str = "YES" if agent_val else "no"
        determ_str = "YES" if determ_val else "no"

        # Highlight mismatches
        if match:
            print(f"  {symbol} {label:<25} Agent: {agent_str:<5}  Calc: {determ_str:<5}")
        else:
            print(f"  {symbol} {label:<25} Agent: {agent_str:<5}  Calc: {determ_str:<5}  ← MISMATCH")

    # Compare categorical flags
    cat_flags = [
        ("cci_extreme",       "CCI Extreme"),
        ("bb_position",       "BB Position"),
        ("overall_risk_tier", "Risk Tier"),
    ]

    for key, label in cat_flags:
        agent_val = agent_verdict.get(key, "N/A")
        determ_val = deterministic_flags.get(key, "N/A")
        match = str(agent_val).upper() == str(determ_val).upper()
        match_count += 1 if match else 0
        total_flags += 1

        symbol = "✓" if match else "✗"
        if match:
            print(f"  {symbol} {label:<25} Agent: {agent_val:<16}  Calc: {determ_val:<16}")
        else:
            print(f"  {symbol} {label:<25} Agent: {agent_val:<16}  Calc: {determ_val:<16}  ← MISMATCH")

    # Summary line
    pct = (match_count / total_flags * 100) if total_flags > 0 else 0
    print("─"*60)
    print(f"  Agreement: {match_count}/{total_flags} ({pct:.0f}%)")

    # Print agent's reasoning (authoritative)
    agent_summary = agent_verdict.get("risk_flags_summary", [])
    if agent_summary:
        print(f"\n  [AGENT RISK FLAGS (authoritative)]:")
        for flag in agent_summary:
            print(f"   → {flag}")

    # Print deterministic reasoning (reference)
    determ_summary = deterministic_flags.get("risk_flags_summary", [])
    if determ_summary:
        print(f"\n  [DETERMINISTIC FLAGS (reference)]:")
        for flag in determ_summary:
            print(f"   · {flag}")

    print(f"\n  Final Risk Tier (Agent's call): {agent_verdict.get('overall_risk_tier', 'N/A')}")
    print("─"*60 + "\n")
```

### Example Console Output

```
────────────────────────────────────────────────────────────────
  TECHNICAL RISK FLAGS — DUAL-TRACK COMPARISON
────────────────────────────────────────────────────────────────
  ✓ Overbought                Agent: no     Calc: no
  ✓ Oversold (favorable)      Agent: YES    Calc: YES
  ✓ Bearish Divergence        Agent: no     Calc: no
  ✗ Weak Trend (ADX)          Agent: no     Calc: YES    ← MISMATCH
  ✓ Below SMA200              Agent: YES    Calc: YES
  ✓ Volume Spike              Agent: YES    Calc: YES
  ✓ MACD Bearish              Agent: YES    Calc: YES
  ✓ CCI Extreme               Agent: OVERSOLD         Calc: OVERSOLD
  ✓ BB Position                Agent: BELOW_LOWER      Calc: BELOW_LOWER
  ✗ Risk Tier                  Agent: MODERATE_RISK    Calc: HIGH_RISK      ← MISMATCH
────────────────────────────────────────────────────────────────
  Agreement: 8/10 (80%)

  [AGENT RISK FLAGS (authoritative)]:
   → RSI at 24.3 — deeply oversold, favorable for dip entry
   → Below SMA200 by 8.2% — long-term trend is bearish
   → Volume spike at 3.1x — likely capitulation selling
   → MACD histogram negative but flattening — momentum loss decelerating

  [DETERMINISTIC FLAGS (reference)]:
   · RSI at 24.3 — oversold (<30, favorable for dip)
   · ADX at 18.2 — weak/choppy trend (<20)
   · Below SMA200: close $142.30 vs SMA200 $154.90 (8.1% below)
   · Volume spike: relative volume 3.1x (>2.5x normal)
   · MACD histogram negative: -1.234

  Final Risk Tier (Agent's call): MODERATE_RISK
────────────────────────────────────────────────────────────────
```

---

## 4. Integration Into `_run_risk_council_and_decision()`

Replace the existing string-matching block (lines 381-404) with the new dual-track system.

### Updated Method

```python
def _run_risk_council_and_decision(self, state: MarketState, raw_data: Dict, drop_str: str) -> Dict:
    """
    Runs Risk Agents (Dual-Track Technical Flags + LLM) and then Fund Manager.
    """
    # ─── Track 1: Agent Verdict (already computed in Phase 1) ───
    tech_report = state.reports.get("technical", "")
    agent_verdict = self._extract_technical_verdict(tech_report)

    # ─── Track 2: Deterministic Flags (from raw numbers) ───
    indicators = raw_data.get('indicators', {})
    deterministic_flags = self._compute_deterministic_risk_flags(indicators)

    # ─── Console Comparison ───
    self._print_risk_flag_comparison(agent_verdict, deterministic_flags)

    # ─── Build safe_concerns from AGENT verdict (authoritative) ───
    safe_concerns = agent_verdict.get("risk_flags_summary", [])

    # ─── Append deterministic mismatches as advisory notes ───
    # If the deterministic check caught something the agent missed, note it
    bool_keys = ["overbought", "bearish_divergence", "weak_trend", "below_sma200"]
    for key in bool_keys:
        if deterministic_flags.get(key) and not agent_verdict.get(key):
            safe_concerns.append(f"[CALC NOTE] Deterministic check flagged {key} — agent disagreed")

    # 2. RiskyGuardian (Contextual/News Checks)
    risky_support = []
    news_report = state.reports.get("news", "")
    if "CORPORATE" in news_report.upper():
        risky_support.append("Corporate events identified.")

    # 3. Portfolio Manager (Final Decision)
    manager_prompt = self._create_fund_manager_prompt(state, safe_concerns, risky_support, drop_str)
    decision_json_str = self._call_agent(manager_prompt, "Fund Manager", state)
    decision = self._extract_json(decision_json_str)

    if not decision:
        decision = {
            "action": "AVOID", "conviction": "LOW",
            "reason": "Failed to generate decision JSON.",
            "drop_type": "UNKNOWN"
        }

    return decision
```

### Signature Change

Note: `_run_risk_council_and_decision` now takes `raw_data` as a parameter. Update the call site at line ~236:

**Before:**
```python
final_decision = self._run_risk_council_and_decision(state, drop_str)
```

**After:**
```python
final_decision = self._run_risk_council_and_decision(state, raw_data, drop_str)
```

---

## 5. Changes Summary

| File | Change | Lines |
|------|--------|-------|
| `research_service.py` | `_create_technical_agent_prompt()` — add STRUCTURED_VERDICT section | ~455-492 |
| `research_service.py` | New `_extract_technical_verdict()` method | New method |
| `research_service.py` | New `_compute_deterministic_risk_flags()` method | New method |
| `research_service.py` | New `_print_risk_flag_comparison()` method | New method |
| `research_service.py` | `_run_risk_council_and_decision()` — replace string-matching with dual-track | ~377-413 |
| `research_service.py` | Update call site: pass `raw_data` to risk council method | ~236 |

### What Gets Removed

The entire old string-matching block:
```python
# DELETE THIS:
safe_concerns = []
tech_report = state.reports.get("technical", "")
if "OVERBOUGHT" in tech_report.upper():
    safe_concerns.append("Technicals are Overbought.")
if "DIVERGENCE" in tech_report.upper():
    safe_concerns.append("Bearish Divergence detected.")
if "WEAK" in tech_report.upper() and "TREND" in tech_report.upper():
    safe_concerns.append("Trend detected as Weak.")
```

### What Stays the Same

- The Technical Agent still produces its full prose playbook (unchanged compatibility with bull/bear debate)
- The `safe_concerns` list still feeds into `_create_fund_manager_prompt()` exactly as before — just better populated
- The PM prompt, Bull/Bear prompts, and all other agents are untouched
- The return dict from `analyze_stock()` is unchanged

---

## 6. Future: Logging Comparison Data

Once this is running, you can optionally log the comparison to the database for backtesting:

```python
# In analyze_stock() return dict, add:
"technical_verdict": {
    "agent": agent_verdict,
    "deterministic": deterministic_flags,
    "agreement_pct": match_count / total_count * 100
}
```

This lets you later query: "When agent and deterministic disagreed, who was right?" — which feeds into whether to eventually auto-override the agent on specific flags.
