"""JSON Schemas for Claude structured-output synthesis of Deep Research results.

Keys mirror what the Gemini DeepResearchService returns so the shared
_handle_completion / DB-write path works unchanged.
"""

_STR = {"type": "string"}
_NUM = {"type": "number"}
_NUM_OR_NULL = {"type": ["number", "null"]}

_SWOT = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "strengths": {"type": "array", "items": _STR},
        "weaknesses": {"type": "array", "items": _STR},
        "opportunities": {"type": "array", "items": _STR},
        "threats": {"type": "array", "items": _STR},
    },
    "required": ["strengths", "weaknesses", "opportunities", "threats"],
}

_VERIFICATION_ITEM = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "claim": _STR,
        "verdict": {"type": "string", "enum": ["VERIFIED", "DISPUTED"]},
        "source_url": _STR,
    },
    "required": ["claim", "verdict", "source_url"],
}

INDIVIDUAL_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "review_verdict": {"type": "string",
                           "enum": ["CONFIRMED", "UPGRADED", "ADJUSTED", "OVERRIDDEN"]},
        # Gate 4: OVERRIDDEN is only binding with a NAMED_EVENT basis;
        # JUDGMENT overrides are recorded as advisory, council action stands.
        "override_basis": {"type": "string", "enum": ["NAMED_EVENT", "JUDGMENT", "NONE"]},
        "named_event": {"type": ["string", "null"]},
        "action": {"type": "string", "enum": ["BUY", "BUY_LIMIT", "WATCH", "AVOID"]},
        "conviction": {"type": "string", "enum": ["HIGH", "MODERATE", "LOW"]},
        "drop_type": _STR,
        "risk_level": {"type": "string", "enum": ["Low", "Medium", "High", "Extreme"]},
        "catalyst_type": {"type": "string", "enum": ["Structural", "Temporary", "Noise"]},
        "entry_price_low": _NUM,
        "entry_price_high": _NUM,
        "stop_loss": _NUM,
        "take_profit_1": _NUM,
        "take_profit_2": _NUM_OR_NULL,
        "upside_percent": _NUM,
        "downside_risk_percent": _NUM,
        "risk_reward_ratio": _NUM,
        "pre_drop_price": _NUM,
        "entry_trigger": _STR,
        "reassess_in_days": _NUM,
        "sell_price_low": _NUM,
        "sell_price_high": _NUM,
        "ceiling_exit": _NUM,
        "exit_trigger": _STR,
        "global_market_analysis": _STR,
        "local_market_analysis": _STR,
        "swot_analysis": _SWOT,
        "verification_results": {"type": "array", "items": _VERIFICATION_ITEM},
        "council_blindspots": {"type": "array", "items": _STR},
        "knife_catch_warning": {"type": "boolean"},
        "reason": _STR,
        "could_not_verify": {"type": "array", "items": _STR},
    },
    "required": [
        "review_verdict", "override_basis", "named_event",
        "action", "conviction", "drop_type", "risk_level",
        "catalyst_type", "entry_price_low", "entry_price_high", "stop_loss",
        "take_profit_1", "upside_percent", "downside_risk_percent",
        "risk_reward_ratio", "entry_trigger", "reassess_in_days",
        "sell_price_low", "sell_price_high", "ceiling_exit", "exit_trigger",
        "global_market_analysis", "local_market_analysis", "swot_analysis",
        "verification_results", "council_blindspots", "knife_catch_warning", "reason",
        "could_not_verify",
    ],
}

SELL_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "thesis_status": {"type": "string", "enum": ["INTACT", "WEAKENING", "BROKEN"]},
        "sell_action": {"type": "string",
                        "enum": ["HOLD", "SELL_PARTIAL", "SELL_FULL", "TIGHTEN_STOP"]},
        "updated_sell_price_low": _NUM,
        "updated_sell_price_high": _NUM,
        "updated_ceiling_exit": _NUM,
        "updated_stop_loss": _NUM_OR_NULL,
        "exit_trigger": _STR,
        "next_reassess_in_days": _NUM,
        "thesis_reasoning": _STR,
        "action_reasoning": _STR,
        "key_observations": {"type": "array", "items": _STR},
    },
    "required": [
        "thesis_status", "sell_action", "updated_sell_price_low",
        "updated_sell_price_high", "updated_ceiling_exit", "exit_trigger",
        "next_reassess_in_days", "thesis_reasoning", "action_reasoning",
        "key_observations",
    ],
}

BATCH_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "winner_symbol": _STR,
        "rationale": _STR,
        "projected_timeline": _STR,
        "ranking": {"type": "array", "items": _STR},
    },
    "required": ["winner_symbol", "rationale", "projected_timeline", "ranking"],
}
