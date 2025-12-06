from dataclasses import dataclass, field
from typing import Dict, List, Optional

@dataclass
class MarketState:
    ticker: str
    date: str
    reports: Dict[str, str] = field(default_factory=dict) # Keys: 'fundamental', 'technical', 'sentiment', 'news'
    debate_transcript: List[str] = field(default_factory=list)
    trade_proposal: Optional[dict] = None
    risk_assessment: Optional[dict] = None
    final_decision: Optional[dict] = None
