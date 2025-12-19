import requests
import os
import json
import time
import logging
import threading
from queue import Queue, Empty
from typing import Dict, Optional, List, Any
from datetime import datetime

# Configure logging
logger = logging.getLogger(__name__)

class DeepResearchService:
    def __init__(self):
        self.api_key = os.getenv("GEMINI_API_KEY")
        self.base_url = "https://generativelanguage.googleapis.com/v1beta/interactions"
        self.queue = Queue()
        self.is_running = True # Set to True to enable worker
        
        # Monitor Thread State
        self.lock = threading.Lock()
        self.active_tasks_count = 0 

        if not self.api_key:
            logger.warning("GEMINI_API_KEY not found. Deep Research Service will be disabled.")
            self.is_running = False
        else:
             # Start the single worker thread
            self.worker_thread = threading.Thread(target=self._worker_loop, daemon=True)
            self.worker_thread.start()
            
            # Start Monitoring
            self._start_monitor_thread()

    def _start_monitor_thread(self):
        """
        Starts a background thread that prints the queue status every 4 minutes.
        """
        def monitor_loop():
            while True:
                time.sleep(240) # 4 minutes
                with self.lock:
                    active = self.active_tasks_count
                
                queued = self.queue.qsize()
                
                print(f"\n[Deep Research Monitor] Active Agent: {active} | Queue Size: {queued} | {datetime.now().strftime('%H:%M:%S')}")

        t = threading.Thread(target=monitor_loop, daemon=True)
        t.start()
        
    def _worker_loop(self):
        """
        Consumes tasks from the queue and executes them one by one.
        This enforces the 'maximum of one deep research from our API' constraint.
        """
        logger.info("[Deep Research] Worker thread started.")
        while self.is_running:
            try:
                # Get task from queue (blocking)
                task_wrapper = self.queue.get()
                
                task_type = task_wrapper.get('type')
                task_payload = task_wrapper.get('payload')
                
                symbol_display = task_payload.get('symbol', 'BATCH') if task_type == 'individual' else "BATCH_COMPARISON"
                
                with self.lock:
                    self.active_tasks_count = 1
                    
                logger.info(f"[Deep Research] Starting task: {task_type} for {symbol_display}")
                
                if task_type == 'individual':
                     self._process_individual_task(task_payload)
                elif task_type == 'batch_comparison':
                     self._process_batch_task(task_payload)
                     
                with self.lock:
                    self.active_tasks_count = 0
                    
                self.queue.task_done()
                
            except Exception as e:
                logger.error(f"[Deep Research] Worker Loop Error: {e}")
                with self.lock:
                    self.active_tasks_count = 0

    def queue_research_task(self, symbol, raw_news, technical_data, drop_percent, decision_id, transcript_text="", transcript_date=None, transcript_warning=None):
        """
        Queues an individual deep research task.
        """
        payload = {
            'symbol': symbol,
            'raw_news': raw_news,
            'technical_data': technical_data,
            'drop_percent': drop_percent,
            'decision_id': decision_id,
            'transcript_text': transcript_text,
            'transcript_date': transcript_date,
            'transcript_warning': transcript_warning
        }
        self.queue.put({'type': 'individual', 'payload': payload})
        logger.info(f"[Deep Research] Queued INDIVIDUAL task for {symbol}")

    def queue_batch_comparison_task(self, candidates: List[Dict], batch_id: int):
        """
        Queues a batch comparison task.
        candidates: List of decision_data dictionaries (Top 3).
        batch_id: Database ID for tracking.
        """
        payload = {
            'candidates': candidates,
            'batch_id': batch_id
        }
        self.queue.put({'type': 'batch_comparison', 'payload': payload})
        logger.info(f"[Deep Research] Queued BATCH COMPARISON task for {len(candidates)} candidates (Batch ID: {batch_id})")


    def _process_individual_task(self, payload):
        """
        Executes individual deep research.
        """
        symbol = payload['symbol']
        try:
             result = self.execute_deep_research(
                symbol, 
                payload['raw_news'], 
                payload['technical_data'], 
                payload['drop_percent'], 
                payload['transcript_text'], 
                payload['transcript_date'], 
                payload['transcript_warning']
            )
             if result:
                 self._handle_completion(payload, result)
        except Exception as e:
            logger.error(f"[Deep Research] Individual Task Failed for {symbol}: {e}")

    def _process_batch_task(self, payload):
        """
        Executes batch comparison.
        """
        try:
            self.execute_batch_comparison(payload['candidates'], payload.get('batch_id'))
        except Exception as e:
             logger.error(f"[Deep Research] Batch Task Failed: {e}")
             if payload.get('batch_id'):
                 from app.database import update_batch_status
                 update_batch_status(payload['batch_id'], 'FAILED')

    def _handle_completion(self, task, result):
        """
        Handles the completed research result (DB update, PDF save).
        """
        symbol = task['symbol']
        decision_id = task.get('decision_id')
        verdict = result.get('verdict', 'UNKNOWN')
        logger.info(f"[Deep Research] Task Completed for {symbol}. Verdict: {verdict}")
        
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

            # Save to file
            self._save_result_to_file(symbol, result)
            
        except Exception as e:
            logger.error(f"[Deep Research] Error updating DB: {e}")

    def _save_result_to_file(self, symbol, result):
        try:
            output_dir = "data/deep_research_reports"
            os.makedirs(output_dir, exist_ok=True)
            
            date_str = datetime.now().strftime("%Y-%m-%d")
            filename = f"deep_research_{symbol}_{date_str}_{int(time.time())}.json"
            filepath = os.path.join(output_dir, filename)
            
            with open(filepath, "w") as f:
                json.dump(result, f, indent=2)
            logger.info(f"[Deep Research] Saved result to {filepath}")

            # Also save as PDF
            filename_base = filename.replace(".json", "") # Strip extension
            self._save_result_to_pdf(symbol, result, filename_base)

        except Exception as e:
            logger.error(f"[Deep Research] Error saving to file: {e}")

    def _save_result_to_pdf(self, symbol, result, filename_base):
        try:
            from reportlab.lib.pagesizes import letter
            from reportlab.lib import colors
            from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
            from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
            
            output_dir = "data/deep_research_reports"
            filepath = os.path.join(output_dir, f"{filename_base}.pdf")
            
            doc = SimpleDocTemplate(filepath, pagesize=letter)
            styles = getSampleStyleSheet()
            
            # Custom Style
            title_style = styles['Title']
            heading_style = styles['Heading2']
            normal_style = styles['BodyText']
            
            story = []
            
            # Title
            story.append(Paragraph(f"Deep Research Report: {symbol}", title_style))
            story.append(Spacer(1, 12))
            story.append(Paragraph(f"Date: {datetime.now().strftime('%Y-%m-%d %H:%M')}", normal_style))
            story.append(Spacer(1, 12))
            
            # Verdict & Risk
            verdict = result.get('verdict', 'UNKNOWN')
            risk = result.get('risk_level', 'Unknown')
            
            # Color code verdict
            v_color = "black"
            if "BUY" in verdict: v_color = "green"
            elif "AVOID" in verdict or "SELL" in verdict: v_color = "red"
            
            story.append(Paragraph(f"<b>VERDICT:</b> <font color='{v_color}'>{verdict}</font>", styles['Heading3']))
            story.append(Paragraph(f"<b>RISK LEVEL:</b> {risk}", styles['Heading3']))
            story.append(Spacer(1, 12))
            
            # Catalyst & Reasoning
            story.append(Paragraph("Catalyst & Market Context", heading_style))
            story.append(Paragraph(f"<b>Catalyst Type:</b> {result.get('catalyst_type', 'N/A')}", normal_style))
            story.append(Spacer(1, 6))
            story.append(Paragraph(f"<b>Global Context:</b> {result.get('global_market_analysis', 'N/A')}", normal_style))
            story.append(Spacer(1, 6))
            story.append(Paragraph(f"<b>Local/Sector Context:</b> {result.get('local_market_analysis', 'N/A')}", normal_style))
            story.append(Spacer(1, 12))
            
            # Key Reasoning
            story.append(Paragraph("Key Reasoning", heading_style))
            for point in result.get('reasoning_bullet_points', []):
                 story.append(Paragraph(f"• {point}", normal_style))
                 story.append(Spacer(1, 4))
            story.append(Spacer(1, 12))

            # SWOT Analysis
            swot = result.get('swot_analysis', {})
            story.append(Paragraph("SWOT Analysis", heading_style))
            
            data = [
                [Paragraph("<b>Strengths</b>", normal_style), Paragraph("<b>Weaknesses</b>", normal_style)],
                [
                    Paragraph("<br/>".join([f"- {s}" for s in swot.get('strengths', [])]), normal_style),
                    Paragraph("<br/>".join([f"- {w}" for w in swot.get('weaknesses', [])]), normal_style)
                ],
                [Paragraph("<b>Opportunities</b>", normal_style), Paragraph("<b>Threats</b>", normal_style)],
                [
                    Paragraph("<br/>".join([f"- {o}" for o in swot.get('opportunities', [])]), normal_style),
                    Paragraph("<br/>".join([f"- {t}" for t in swot.get('threats', [])]), normal_style)
                ]
            ]
            
            table = Table(data, colWidths=[230, 230])
            table.setStyle(TableStyle([
                ('GRID', (0,0), (-1,-1), 1, colors.grey),
                ('BACKGROUND', (0,0), (1,0), colors.lightgrey),
                ('BACKGROUND', (0,2), (1,2), colors.lightgrey),
                ('VALIGN', (0,0), (-1,-1), 'TOP'),
                ('PADDING', (0,0), (-1,-1), 6),
            ]))
            story.append(table)
            story.append(Spacer(1, 12))
            
            # Build
            doc.build(story)
            logger.info(f"[Deep Research] Saved PDF to {filepath}")
            
        except ImportError:
            logger.error("ReportLab not installed. Cannot generate PDF.")
        except Exception as e:
            logger.error(f"[Deep Research] Error generating PDF: {e}")

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
            if not outputs: 
                logger.warning("[Deep Research] No outputs found in poll data.")
                return None
            
            logger.info(f"[Deep Research] Parsing outputs. Count: {len(outputs)}")
            
            # Iterate through all outputs to find the best candidate
            for output in reversed(outputs):
                text = output.get('text', str(output)) if isinstance(output, dict) else str(output)
                
                # Cleaning
                text = text.strip()
                if text.startswith("```json"):
                    text = text[7:]
                if text.startswith("```"):
                     text = text[3:]
                if text.endswith("```"):
                    text = text[:-3]
                text = text.strip()
                
                # Try explicit JSON parsing first
                import json
                try:
                    return json.loads(text)
                except:
                    pass
                
                # Regex fallback
                import re
                # Look for largest outer braces
                json_match = re.search(r'\{.*\}', text, re.DOTALL)
                if json_match:
                    try:
                        return json.loads(json_match.group(0))
                    except:
                        pass
                        
            # If we reach here, no valid JSON found in any output.
            # Return raw of the last one as fallback for debugging so we don't lose the data
            final_text = str(outputs[-1]) if outputs else "No Output"
            
            logger.warning(f"[Deep Research] JSON Parse failed. Using Raw Fallback. Length: {len(final_text)}")
            
            # Construct a dummy JSON so the system doesn't crash and user can read the text
            return {
                "verdict": "ERROR_PARSING",
                "risk_level": "Unknown",
                "catalyst_type": "Parse Error",
                "global_market_analysis": "See Raw Report",
                "local_market_analysis": "See Raw Report",
                "swot_analysis": {
                    "strengths": [], "weaknesses": [], "opportunities": [], "threats": []
                },
                "reasoning_bullet_points": [
                    "JSON Parsing Failed.",
                    "Raw Output Below:",
                    final_text[:3000] # Truncate if too huge, but usually fine
                ],
                "knife_catch_warning": "True",
                "raw_report_full": final_text
            }
            
        except Exception as e:
            logger.error(f"[Deep Research] Error parsing: {e}")
            return None

    def execute_batch_comparison(self, candidates, batch_id=None):
        """
        Runs a separate agent to compare 3 stocks and pick the winner.
        Takes 'candidates' which are decision_data dicts from StockService.
        """
        try:
            symbols = [x['symbol'] for x in candidates]
            logger.info(f"[Deep Research] Starting Batch Comparison for: {symbols}")
            print(f"\n{'='*60}")
            print(f"🚀 [DEEP RESEARCH] STARTING BATCH COMPARISON")
            print(f"{'='*60}")
            print(f"Comparing Candidates: {', '.join(symbols)}")
            print(f"{'='*60}\n")
            
            # Construct Prompt
            candidates_text = ""
            for item in candidates:
                symbol = item.get('symbol')
                drop = item.get('change_percent', 0.0)
                reasoning = item.get('reasoning', '') # Full Council Report
                rec = item.get('recommendation', 'N/A')
                score = item.get('ai_score', 'N/A')
                
                # Truncate reasoning to avoid token limits if necessary
                # reasoning contains all reports (Bull/Bear/Tech/Macro). It's rich.
                
                candidates_text += f"""
=== CANDIDATE: {symbol} ===
Drop: {drop:.2f}%
Recommendation: {rec} (Score: {score})
Analysis Summary:
{reasoning[:3000]}... (truncated)
===========================
"""

            prompt = f"""
You are the **Lead Portfolio Manager**. You have received detailed analysis reports for 4 potential turnaround candidates.
Your goal is to allocate capital to the **Single Best Opportunity**.

CRITERIA:
1. **Highest Likelihood to Recover:** Which stock has the most overstated drop vs fundamentals?
2. **Biggest Monetary Gain (1-12 Months):** Which stock offers the best asymmetric upside?
3. **Safety:** Avoid "Falling Knives" or structural decliners.

CANDIDATES DATA:
{candidates_text}

TASK:
Compare these 4 candidates based on the provided analysis.
Rank them from 1 to 4.
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
            
            # Using Requests to call Gemini Pro (or Deep Research agent if preferred)
            # The user says "runs the deep research on the comparison". 
            # We can use the Deep Research Agent endpoint if we want "Deep Research" capabilities, 
            # Or the standard Gemini 1.5 Pro endpoint.
            # Given the complexity, let's use the 'gemini-1.5-pro' standard interaction for reasoning.
            # If we wanted the Deep Research Agent (with seach etc), we would use self.execute_deep_research style.
            # But here we provide all context.
            
            # Use Deep Research Agent for Comparison
            # As per user request, we use 'deep-research-pro-preview-12-2025'
            # This is an AGENT, so we must use the /interactions endpoint and Poll.
            
            headers = {
                "Content-Type": "application/json",
                "x-goog-api-key": self.api_key
            }
            
            payload = {
                "input": prompt,
                "agent": "deep-research-pro-preview-12-2025",
                "background": True
            }
            
            logger.info(f"[Deep Research] Starting Batch Comparison via Agent...")
            response = requests.post(self.base_url, headers=headers, json=payload)
            
            if response.status_code != 200:
                logger.error(f"[Deep Research] API Error: {response.text}")
                print(f"[Deep Research] API Error: {response.text}")
                return

            data = response.json()
            interaction_id = data.get('id') or data.get('name')
            if not interaction_id:
                logger.error("[Deep Research] No Interaction ID returned.")
                return
                
            logger.info(f"[Deep Research] Batch Comparison Started (ID: {interaction_id}). Polling...")
            
            # Poll for completion
            max_retries = 100
            poll_interval = 15
            poll_url = f"{self.base_url}/{interaction_id}"
            
            result_text = None
            
            for i in range(max_retries):
                if i % 2 == 0: 
                     print(f"[Deep Research] Polling batch comparison... ({i}/{max_retries})")
                time.sleep(poll_interval)
                resp = requests.get(poll_url, headers=headers)
                if resp.status_code != 200: continue
                
                poll_data = resp.json()
                status = poll_data.get('status', poll_data.get('state', 'UNKNOWN'))
                
                if status in ['completed', 'COMPLETED']:
                    logger.info("[Deep Research] Batch Comparison Completed.")
                    # Parse Output using existing helper
                    parsed_result = self._parse_output(poll_data)
                    if parsed_result:
                         # We expect the agent to output the JSON we asked for.
                         # _parse_output tries to find JSON.
                         # If it returns a dict, we can try to serialize it or use it.
                         result_text = json.dumps(parsed_result, indent=2)
                    else:
                         # Fallback to raw text if parsing failed but completed
                         outputs = poll_data.get('outputs', [])
                         if outputs:
                             result_text = str(outputs[-1].get('text', ''))
                    break
                elif status in ['failed', 'FAILED']:
                    logger.error(f"[Deep Research] Batch Comparison Failed: {poll_data}")
                    print(f"[Deep Research] Batch Comparison Failed.")
                    return
            
            if not result_text:
                logger.error("[Deep Research] Batch Comparison Timeout or No Result.")
                print("[Deep Research] Batch Comparison Timeout.")
                return

            logger.info(f"[Deep Research] Comparison Result: {result_text}")
            
            # Save Comparison Result
            os.makedirs("data/comparisons", exist_ok=True)
            filename = f"data/comparisons/batch_{int(time.time())}.json"
            with open(filename, "w") as f:
                 f.write(result_text)
            logger.info(f"[Deep Research] Saved comparison to {filename}")
            
            try:
                res_json = json.loads(result_text)
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
                
                # Update DB Status to COMPLETED
                if batch_id:
                     from app.database import update_batch_status
                     update_batch_status(batch_id, 'COMPLETED')
                     logger.info(f"[Deep Research] Batch {batch_id} marked as COMPLETED.")
                     
            except:
                print(f"\n[Deep Research] Comparison Complete. (Could not parse JSON output)")
                print(result_text)
                # Still mark as completed if text was returned? Or failed?
                # If we have result text but failed to parse, let's mark as COMPLETED for now to avoid infinite loops,
                # but maybe log the error.
                if batch_id:
                     from app.database import update_batch_status
                     update_batch_status(batch_id, 'COMPLETED')

        except Exception as e:
            logger.error(f"[Deep Research] Comparison Agent Failed: {e}")
            print(f"[Deep Research] Comparison Agent Error: {e}")
            if batch_id:
                 from app.database import update_batch_status
                 update_batch_status(batch_id, 'FAILED')

    def execute_batch_comparison(self, candidates, batch_id=None):
        """
        Runs a separate agent to compare 3 stocks and pick the winner.
        Takes 'candidates' which are decision_data dicts from StockService.
        """

deep_research_service = DeepResearchService()
