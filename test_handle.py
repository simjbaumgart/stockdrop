import os
import json
import traceback
from dotenv import load_dotenv

load_dotenv()

from app.services.deep_research_service import deep_research_service

task = {
    'symbol': 'TEST',
    'decision_id': 1
}
result = {
    "review_verdict": "CONFIRMED",
    "action": "BUY",
    "conviction": "HIGH",
    "drop_type": "UNKNOWN",
    "risk_level": "Low",
    "catalyst_type": "Temporary",
    "entry_price_low": 100,
    "entry_price_high": 110,
    "stop_loss": 90,
    "take_profit_1": 120,
    "take_profit_2": 130,
    "upside_percent": 20,
    "downside_risk_percent": 10,
    "risk_reward_ratio": 2.0,
    "pre_drop_price": 125,
    "entry_trigger": "None",
    "reassess_in_days": 30,
    "sell_price_low": 115,
    "sell_price_high": 125,
    "ceiling_exit": 135,
    "exit_trigger": "None",
    "global_market_analysis": "Good",
    "local_market_analysis": "Good",
    "swot_analysis": {"strengths": [], "weaknesses": [], "opportunities": [], "threats": []},
    "verification_results": [],
    "council_blindspots": [],
    "knife_catch_warning": False,
    "reason": "Test"
}

try:
    deep_research_service._handle_completion(task, result)
    print("Success without exception in _handle_completion block!")
except Exception as e:
    print("Caught exception:")
    traceback.print_exc()
