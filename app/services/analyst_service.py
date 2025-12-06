import json
from abc import ABC, abstractmethod
from typing import Dict, Any, List
from datetime import datetime
from app.services.finnhub_service import finnhub_service
from app.services.tradingview_service import tradingview_service
import logging

logger = logging.getLogger(__name__)

class BaseAnalyst(ABC):
    @abstractmethod
    def generate_report(self, ticker: str, data: Dict[str, Any]) -> str:
        """
        Generates a specific report based on the analyst's domain.
        Returns a string report.
        """
        pass

class FundamentalAnalyst(BaseAnalyst):
    def generate_report(self, ticker: str, data: Dict[str, Any]) -> str:
        """
        Input data expectations:
        - data['metrics']: Dict with PE, PB, PEG, etc. (from TradingView)
        """
        metrics = data.get('metrics', {})
        
        # Format Metrics Table
        report = "## Fundamental Valuation Metrics (TradingView Data)\n"
        report += "| Metric | Value |\n|---|---|\n"
        
        # Safe access with default "N/A"
        keys = ['pe_ratio', 'price_to_book', 'peg_ratio', 'debt_to_equity', 'profit_margin']
        for k in keys:
            val = metrics.get(k, "N/A")
            report += f"| {k.replace('_', ' ').title()} | {val} |\n"
            
        # Basic Interpretation
        pe = metrics.get('pe_ratio')
        peg = metrics.get('peg_ratio')
        
        report += "\n**Analysis:**\n"
        if isinstance(pe, (int, float)) and pe < 15:
            report += "- P/E Ratio suggests potential Undervaluation relative to generic market avg.\n"
        elif isinstance(pe, (int, float)) and pe > 50:
             report += "- P/E Ratio indicates High Growth expectations or Overvaluation.\n"
             
        if isinstance(peg, (int, float)):
             if peg < 1.0:
                 report += "- PEG < 1.0 suggests stock may be undervalued relative to growth.\n"
             elif peg > 2.0:
                 report += "- PEG > 2.0 suggests price might be ahead of earnings growth.\n"
                 
        return report

class TechnicalAnalyst(BaseAnalyst):
    def generate_report(self, ticker: str, data: Dict[str, Any]) -> str:
        """
        Input data expectations:
        - data['indicators']: Dict (RSI, MACD, BB, ADX) from TradingView/TA
        """
        indicators = data.get('indicators', {})
        
        # Defaults
        rsi = indicators.get('RSI', 50) or 50
        macd = indicators.get('MACD.macd', 0) or 0
        signal = indicators.get('MACD.signal', 0) or 0
        adx = indicators.get('ADX', 0) or 0
        
        report = "## Technical Analysis Report\n"
        
        # RSI Logic
        report += f"- **RSI (14):** {rsi:.2f} -> "
        if rsi > 70: report += "**CONDITION: OVERBOUGHT**. Potential pullback risk.\n"
        elif rsi < 30: report += "**CONDITION: OVERSOLD**. Potential mean reversion opportunity.\n"
        else: report += "Neutral zone.\n"
        
        # MACD Logic
        report += f"- **MACD:** {macd:.2f} vs Signal {signal:.2f} -> "
        if macd > signal: report += "Bullish Crossover active.\n"
        else: report += "Bearish Divergence/Trend.\n"
        
        # ADX Logic
        report += f"- **ADX Trend Strength:** {adx:.2f} -> "
        if adx > 25: report += "**TREND: STRONG**.\n"
        else: report += "Trend is weak/choppy.\n"
        
        return report

class SentimentAnalyst(BaseAnalyst):
    def generate_report(self, ticker: str, data: Dict[str, Any]) -> str:
        """
        Input data expectations:
        - data['news_items']: List of news dicts
        - data['transcript_text']: String (Earnings Call)
        """
        news_items = data.get('news_items', [])
        transcript = data.get('transcript_text', "")
        
        # Simple Sentiment Heuristic based on keywords in News + Transcript
        positive_keywords = ['growth', 'record', 'beat', 'up', 'buy', 'strong', 'positive', 'bull', 'expansion']
        negative_keywords = ['miss', 'down', 'sell', 'weak', 'negative', 'bear', 'recession', 'inflation', 'disappoint']
        
        score = 50 # Start neutral
        
        # Analyze News Headlines
        scanned_text = " ".join([item.get('headline', '') for item in news_items])
        
        # Analyze Transcript (First 2000 chars for brevity/speed)
        scanned_text += " " + transcript[:2000]
        scanned_text = scanned_text.lower()
        
        pos_count = sum(scanned_text.count(w) for w in positive_keywords)
        neg_count = sum(scanned_text.count(w) for w in negative_keywords)
        
        total = pos_count + neg_count
        if total > 0:
            # Normalize to 0-100 scale. 
            # If 100% positive -> 100. 50/50 -> 50.
            score = (pos_count / total) * 100
            
        normalized_score = score / 100.0 # 0.0 to 1.0 needed for compatibility
        
        report = "## Sentiment Analysis (News & Earnings)\n"
        report += f"Derived Sentiment Score: {normalized_score:.2f} (0=Despair, 1=Euphoria)\n"
        report += f"Analysis based on {len(news_items)} news items and latest earnings transcript.\n"
        
        if normalized_score > 0.75:
            report += "**STATUS: POSITIVE**. News/Mgmt language is optimistic.\n"
        elif normalized_score < 0.25:
            report += "**STATUS: NEGATIVE**. News/Mgmt language is cautious/pessimistic.\n"
        else:
            report += "**STATUS: NEUTRAL**. Mixed signals.\n"
            
        return report

class NewsAnalyst(BaseAnalyst):
    def generate_report(self, ticker: str, data: Dict[str, Any]) -> str:
        """
        Input data expectations:
        - data['news_items']: List of news dicts
        """
        news_items = data.get('news_items', [])
        
        report = "## News Contextualization\n"
        
        categories = {"Corporate": [], "Macro": [], "Geopolitical": []}
        
        # Simple keyword categorization (naive model)
        for item in news_items:
            headline = item.get('headline', '')
            if any(x in headline.lower() for x in ['fed', 'rate', 'inflation', 'cpi', 'jobs']):
                categories['Macro'].append(headline)
            elif any(x in headline.lower() for x in ['war', 'trade', 'china', 'tariff']):
                categories['Geopolitical'].append(headline)
            else:
                categories['Corporate'].append(headline)
                
        for cat, headlines in categories.items():
            if headlines:
                report += f"### {cat} Drivers\n"
                for h in headlines[:3]: # Top 3
                    report += f"- {h}\n"
        
        return report

class AnalystService:
    def __init__(self):
        self.fundamental = FundamentalAnalyst()
        self.technical = TechnicalAnalyst()
        self.sentiment = SentimentAnalyst()
        self.news = NewsAnalyst()
        
    def run_all_analysis(self, ticker: str, raw_data: Dict[str, Any]) -> Dict[str, str]:
        """
        Runs all analysts and returns a dictionary of reports.
        """
        return {
            "fundamental": self.fundamental.generate_report(ticker, raw_data),
            "technical": self.technical.generate_report(ticker, raw_data),
            "sentiment": self.sentiment.generate_report(ticker, raw_data),
            "news": self.news.generate_report(ticker, raw_data)
        }

analyst_service = AnalystService()
