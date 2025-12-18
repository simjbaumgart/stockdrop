import requests
import os
import json
import time
import logging
import threading
from queue import Queue
from typing import Dict, Optional
from datetime import datetime

# Configure logging
logger = logging.getLogger(__name__)

class DeepResearchService:
    def __init__(self):
        self.api_key = os.getenv("GEMINI_API_KEY")
        self.base_url = "https://generativelanguage.googleapis.com/v1beta/interactions"
        self.queue = Queue()
        self.is_running = False
        
        # Buffer for completed research to run Batch Comparison
        self.completed_research_batch = []
        
        if not self.api_key:
            logger.warning("GEMINI_API_KEY not found. Deep Research Service will be disabled.")
        else:
            # self._start_worker() # Method removed/deprecated
            pass

    def queue_research_task(self, symbol, raw_news, technical_data, drop_percent, decision_id, transcript_text="", transcript_date=None, transcript_warning=None):
        """
        Queues a deep research task to be executed in the background.
        This spawns a thread that calls execute_deep_research and then handles the completion.
        """
        task = {
            'symbol': symbol,
            'decision_id': decision_id,
            'drop_percent': drop_percent
        }
        
        def worker():
            try:
                result = self.execute_deep_research(
                    symbol, raw_news, technical_data, drop_percent, 
                    transcript_text, transcript_date, transcript_warning
                )
                if result:
                    self._handle_completion(task, result)
            except Exception as e:
                logger.error(f"[Deep Research] Worker Error for {symbol}: {e}")

        t = threading.Thread(target=worker)
        t.start()
        logger.info(f"[Deep Research] Queued task for {symbol}")


    def _handle_completion(self, task, result):
        """
        Handles the completed research result.
        Since the main analysis is long gone, we need to update the persistent state (DB).
        Now also buffers results for Batch Comparison.
        """
        symbol = task['symbol']
        decision_id = task.get('decision_id')
        verdict = result.get('verdict', 'UNKNOWN')
        logger.info(f"[Deep Research] Task Completed for {symbol}. Verdict: {verdict}")
        
        # Add to Batch Buffer
        self.completed_research_batch.append({
            "symbol": symbol,
            "result": result,
            "task_data": task
        })
        
        # Check Batch Size (4)
        if len(self.completed_research_batch) >= 4:
            logger.info(f"[Deep Research] Batch Buffer Full ({len(self.completed_research_batch)}). Triggering Comparison Analysis...")
            batch_to_process = self.completed_research_batch[:4]
            # Reset buffer (keep remaining if any, though usually 1 at a time)
            self.completed_research_batch = self.completed_research_batch[4:]
            
            # Run Comparison in a separate thread to not block worker
            comp_thread = threading.Thread(target=self._run_comparison_agent, args=(batch_to_process,))
            comp_thread.start()
        
        try:
            from app.database import update_deep_research_data
            
            # Calculate Score
            score_map = {
                "STRONG_BUY": 90,
                "SPECULATIVE_BUY": 75,
                "WAIT_FOR_STABILIZATION": 50,
                "HARD_AVOID": 10
            }
            score = score_map.get(verdict, 0)
            
            # Extract new fields
            swot = json.dumps(result.get('swot_analysis', {}))
            global_analysis = result.get('global_market_analysis', '')
            local_analysis = result.get('local_market_analysis', '')
            
            # Update DB
            success = update_deep_research_data(
                decision_id=decision_id,
                verdict=verdict,
                risk=result.get('risk_level', 'Unknown'),
                catalyst=result.get('catalyst_type', 'Unknown'),
                knife_catch=str(result.get('knife_catch_warning', 'False')),
                score=score,
                swot=swot,
                global_analysis=global_analysis,
                local_analysis=local_analysis
            )
            
            if success:
                logger.info(f"[Deep Research] Successfully updated DB for {symbol} (Score: {score})")
            else:
                logger.error(f"[Deep Research] Failed to update DB for {symbol}")

            # For now, let's also save to a file as a backup
            self._save_result_to_file(symbol, result)
            
        except Exception as e:
            logger.error(f"[Deep Research] Error updating DB: {e}")

    def _save_result_to_file(self, symbol, result):
        try:
            filename = f"deep_research_{symbol}_{int(time.time())}.json"
            with open(filename, "w") as f:
                json.dump(result, f, indent=2)
            logger.info(f"[Deep Research] Saved result to {filename}")
        except Exception as e:
            logger.error(f"[Deep Research] Error saving to file: {e}")

    def execute_deep_research(self, symbol, raw_news, technical_data, drop_percent, transcript_text, transcript_date=None, transcript_warning=None) -> Optional[Dict]:
        """
        The synchronous execution logic.
        """
        # 1. Construct the Prompt
        prompt = self._construct_prompt(symbol, raw_news, technical_data, drop_percent, transcript_text, transcript_date, transcript_warning)
        
        # 2. Start Interaction
        headers = {
            "Content-Type": "application/json",
            "x-goog-api-key": self.api_key
        }
        
        # Using the Deep Research Pro Preview model
        # MUST use background=True for this agent
        payload = {
            "input": prompt,
            "agent": "deep-research-pro-preview-12-2025", 
            "background": True 
        }
        
        try:
            response = requests.post(self.base_url, headers=headers, json=payload)
            if response.status_code != 200:
                logger.error(f"[Deep Research] API Error: {response.text}")
                return None
                
            data = response.json()
            interaction_id = data.get('id') or data.get('name')
            if not interaction_id:
                return None
                
            logger.info(f"[Deep Research] Task Started for {symbol} (ID: {interaction_id})")
            
            # 3. Poll
            max_retries = 100 # Increased for background safety
            poll_interval = 15
            poll_url = f"{self.base_url}/{interaction_id}"
            
            for i in range(max_retries):
                time.sleep(poll_interval)
                resp = requests.get(poll_url, headers=headers)
                if resp.status_code != 200: continue
                
                poll_data = resp.json()
                status = poll_data.get('status', poll_data.get('state', 'UNKNOWN'))
                
                if status in ['completed', 'COMPLETED']:
                    return self._parse_output(poll_data)
                elif status in ['failed', 'FAILED']:
                    logger.error(f"[Deep Research] Task Failed for {symbol}: {poll_data}")
                    return None
                    
            logger.error(f"[Deep Research] Task Timeout for {symbol}")
            return None
            
        except Exception as e:
            logger.error(f"[Deep Research] Execution Exception: {e}")
            return None

    def _construct_prompt(self, symbol, raw_news, technical_data, drop_percent, transcript_text="", transcript_date=None, transcript_warning=None) -> str:
        # Format News List
        news_str = ""
        for n in raw_news[:15]: 
            date = n.get('datetime_str', 'N/A')
            source = n.get('source', 'Unknown')
            headline = n.get('headline', 'No Headline')
            news_str += f"- {date} [{source}]: {headline}\n"

        # Format Technical Data
        tech_str = json.dumps(technical_data, indent=2)
        
        # Format Transcript (Full)
        transcript_section = ""
        if transcript_text:
             header = "You receive the latest Earnings Call Transcript"
             if transcript_date:
                 header += f" (Dated: {transcript_date})"
             
             warning_msg = ""
             if transcript_warning:
                 warning_msg = f"\nWARNING: {transcript_warning}\n"

             transcript_section = f"{header}:{warning_msg}\n{transcript_text}..." 

        return f"""
You are a Senior Market Analyst specializing in event-driven equities. Your goal is to determine if this drop is a temporary overreaction (Buy) or the start of a structural decline (Trap).

As you see in the report stock {symbol} dropped {drop_percent:.2f}% today.

You receive a recent news summary of the stock:
{news_str}

You receive technical data on the stock:
{tech_str}

{transcript_section}

> **Philosophical Context (The "Tomorrow's News" Paradox):**
> Remember the lesson of the Elm Partners study: Even traders with tomorrow's news often fail because they misjudge what is *already priced in*.
> - **Markets Anticipate:** A strong earnings report might cause a drop if the market expected *perfect* earnings.
> - **Size Matters:** Overconfidence kills. Do not recommend "STRONG_BUY" unless the edge is asymmetric and clear.
> - **Skepticism:** If the news is obvious (e.g., "Profits up"), assume the market knows. Look for the *reaction* to the news, not just the news itself.
> - **Humility:** Acknowledge unknowns. If the "Why" is murky, the risk is higher.

> **Directives:**
> 1. **Identify the Catalyst:** Using the News 'Why Is It Moving' data, identify the single most probable cause for the current price action. Explicitly state if there is NO clear news (a "Silent Mover").
> 2. **Global & Local Market Context:** Analyze the broader market conditions. Is the selling pressure specific to this stock, its sector, or the entire market? Consider global macro factors and local market sentiment.
> 3. **SWOT Analysis:** Perform a concise Strength, Weakness, Opportunity, and Threat analysis based on the provided data.
> 4. **Verify the Magnitude:** Apply the "Tomorrow's News" skepticism. Does the severity of the news (e.g., "Earnings down 50%") truly justify the drop, or was it priced in? If news is minor but reaction is massive, flag as "Speculative Sell".
> 5. **Technical Cross-Check:** Reference the TradingView RSI and MACD. Is the technical reaction proportionate to the fundamental news?
> 6. **Verdict:** Classify the setup as "Fundamental Drop," "Technical Drop," or "Unverified Volatility." Base your final decision on the synthesis of the Catalyst, Market Context, SWOT, Technicals, and Philosophical Context.

> **Output Format:**
A structured report including your verdict if the stock is currently a sell/hold/buy. The output must be valid JSON:
{{
  "verdict": "[STRONG_BUY | SPECULATIVE_BUY | WAIT_FOR_STABILIZATION | HARD_AVOID]",
  "risk_level": "[Low/Medium/Extreme]",
  "catalyst_type": "[Structural/Temporary/Noise]",
  "global_market_analysis": "Brief analysis of global market conditions affecting this stock.",
  "local_market_analysis": "Brief analysis of local market/sector conditions.",
  "swot_analysis": {{
    "strengths": ["point 1", "point 2"],
    "weaknesses": ["point 1", "point 2"],
    "opportunities": ["point 1", "point 2"],
    "threats": ["point 1", "point 2"]
  }},
  "reasoning_bullet_points": [ "Point 1", "Point 2", "Point 3" ],
  "knife_catch_warning": "True/False"
}}
"""

    def _parse_output(self, poll_data) -> Optional[Dict]:
        try:
            outputs = poll_data.get('outputs', [])
            if not outputs: return None
            
            final_output = outputs[-1]
            text = final_output.get('text', str(final_output)) if isinstance(final_output, dict) else str(final_output)
            
            import re
            json_match = re.search(r'\{.*\}', text, re.DOTALL)
            if json_match:
                try:
                    return json.loads(json_match.group(0))
                except:
                    pass
            
            return {"raw_report": text, "verdict": "UNKNOWN (Parse Error)"}
        except Exception as e:
            logger.error(f"[Deep Research] Error parsing: {e}")
            return None

    def _run_comparison_agent(self, batch):
        """
        Runs a separate agent to compare 4 deep research results and pick the winner.
        """
        try:
            symbols = [x['symbol'] for x in batch]
            logger.info(f"[Deep Research] Starting Comparison for batch: {symbols}")
            
            # --- CONSOLE VISIBILITY ---
            print(f"\n{'='*60}")
            print(f"🚀 [DEEP RESEARCH] STARTING BATCH COMPARISON")
            print(f"{'='*60}")
            print(f"Comparing Candidates: {', '.join(symbols)}")
            print(f"Goal: Identify #1 Highest Recovery Potential (1-12 Months)")
            print(f"{'='*60}\n")
            
            # Construct Prompt
            candidates_text = ""
            for item in batch:
                symbol = item['symbol']
                res = item['result']
                drop = item['task_data']['drop_percent']
                
                # Extract key info
                verdict = res.get('verdict', 'N/A')
                risk = res.get('risk_level', 'N/A')
                catalyst = res.get('catalyst_type', 'N/A')
                reasoning = "\n".join([f"- {p}" for p in res.get('reasoning_bullet_points', [])])
                swot = json.dumps(res.get('swot_analysis', {}), indent=1)
                
                candidates_text += f"""
=== CANDIDATE: {symbol} ===
Drop: {drop:.2f}%
Verdict: {verdict}
Risk: {risk}
Catalyst: {catalyst}
Key Reasoning:
{reasoning}
SWOT:
{swot}
===========================
"""

            prompt = f"""
You are the **Lead Portfolio Manager**. You have received Deep Research reports for 4 potential turnaround candidates.
Your goal is to allocate capital to the **Single Best Opportunity**.

CRITERIA:
1. **Highest Likelihood to Recover:** Which stock has the most overstated drop vs fundamentals?
2. **Biggest Monetary Gain (1-12 Months):** Which stock offers the best asymmetric upside?
3. **Safety:** Avoid "Falling Knives" or structural decliners.

CANDIDATES:
{candidates_text}

TASK:
Compare these 4 candidates. Rank them.
Pick the **#1 Top Pick**.

OUTPUT:
A JSON object:
{{
  "winner_symbol": "TICKER",
  "rationale": "Detailed explanation why this stock wins over the others.",
  "projected_timeline": "1-12 Months",
  "ranking": ["TICKER_1", "TICKER_2", "TICKER_3", "TICKER_4"]
}}
"""
            
            # Call Gemini (using flash model for speed/efficiency or Pro? Comparison is high value, use Pro if available.)
            # Reuse the execute logic or call directly. 
            # We can use requests here similar to execute_deep_research but maybe standard model is enough?
            # Let's use the 'gemini-1.5-pro' or 'gemini-3-pro-preview' standard endpoint if configured,
            # or usage requests to be consistent with DeepResearchService structure.
            
            # Simple synchronous call using requests
            headers = {
                "Content-Type": "application/json",
                "x-goog-api-key": self.api_key
            }
            payload = {
                "contents": [{"parts": [{"text": prompt}]}],
                "generationConfig": {"responseMimeType": "application/json"}
            }
            
            # Use Gemini 1.5 Pro or similar for reasoning
            url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-pro:generateContent?key={self.api_key}"
            
            response = requests.post(url, headers=headers, json=payload)
            if response.status_code == 200:
                data = response.json()
                text = data['candidates'][0]['content']['parts'][0]['text']
                logger.info(f"[Deep Research] Comparison Result: {text}")
                
                # Save Comparison Result
                os.makedirs("data/comparisons", exist_ok=True)
                filename = f"data/comparisons/batch_{int(time.time())}.json"
                with open(filename, "w") as f:
                    f.write(text)
                logger.info(f"[Deep Research] Saved comparison to {filename}")
                
                # --- CONSOLE VISIBILITY (OUTCOME) ---
                try:
                    res_json = json.loads(text)
                    winner = res_json.get('winner_symbol', 'UNKNOWN')
                    rationale = res_json.get('rationale', 'No rationale provided.')
                    ranking = res_json.get('ranking', [])
                    
                    print(f"\n{'='*60}")
                    print(f"🏆 [DEEP RESEARCH] BATCH WINNER: {winner}")
                    print(f"{'='*60}")
                    print(f"Ranking: {', '.join(ranking)}")
                    print(f"Rationale: {rationale}")
                    print(f"Timeline: {res_json.get('projected_timeline', 'N/A')}")
                    print(f"{'='*60}\n")
                except:
                    print(f"\n[Deep Research] Comparison Complete. (Could not parse JSON output for console display)")
                    print(text)
                
            else:
                 logger.error(f"[Deep Research] Comparison API Error: {response.text}")
                 print(f"[Deep Research] Comparison Failed: {response.text}")

        except Exception as e:
            logger.error(f"[Deep Research] Comparison Agent Failed: {e}")
            print(f"[Deep Research] Comparison Agent Error: {e}")

deep_research_service = DeepResearchService()
