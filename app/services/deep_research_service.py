import requests
import os
import json
import time
import logging
import threading
from queue import Queue, Empty
from typing import Dict, Optional, List, Any
from datetime import datetime
import sqlite3

# Configure logging
logger = logging.getLogger(__name__)

class DeepResearchService:
    def __init__(self):
        self.api_key = os.getenv("GEMINI_API_KEY")
        self.base_url = "https://generativelanguage.googleapis.com/v1beta/interactions"
        self.individual_queue = Queue() # High Priority
        self.batch_queue = Queue()      # Low Priority
        self.is_running = True # Set to True to enable worker
        
        # Monitor Thread State
        self.lock = threading.Lock()
        self.active_tasks_count = 0 
        
        # Rate Limiting & Monitoring
        self.last_api_call_time = 0
        self.current_task_start_time = None
        self.current_task_name = None 
        self.cooldown_seconds = 60
        
        # Change-detection for file sync (avoid redundant re-syncs)
        self._last_synced_files = set()

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
        Starts background threads for monitoring and batch scanning.
        """
        def monitor_loop():
            while True:
                time.sleep(60) # 1 minute
                with self.lock:
                    active = self.active_tasks_count
                
                queued_ind = self.individual_queue.qsize()
                queued_batch = self.batch_queue.qsize()
                
                current_job_info = ""
                if self.current_task_name and self.current_task_start_time:
                    duration = int(time.time() - self.current_task_start_time)
                    minutes, seconds = divmod(duration, 60)
                    current_job_info = f" | Job: {self.current_task_name} (Running: {minutes}m {seconds}s)"
                
                print(f"\n[Deep Research Monitor] Active Agent: {active} | Queue: {queued_ind} (Ind), {queued_batch} (Batch){current_job_info} | {datetime.now().strftime('%H:%M:%S')}")

        def scanner_loop():
            """Periodic scanner to catch unbatched candidates and sync file state."""
            while True:
                # 1. Sync DB from Files first
                self._sync_batches_from_files()
                
                # 2. Scan for new batches
                time.sleep(300) # 5 minutes
                if self.is_running:
                    self._scan_for_batches()

        t_mon = threading.Thread(target=monitor_loop, daemon=True)
        t_mon.start()
        
        t_scan = threading.Thread(target=scanner_loop, daemon=True)
        t_scan.start()

    def _sync_batches_from_files(self):
        """
        Scans data/comparisons for result files and updates DB status/winner.
        Also marks stuck 'STARTED' batches > 24h old as FAILED/SKIPPED if no file found.
        """
        try:
            import glob
            import json
            from app.database import update_batch_status, mark_batch_winner
            
            output_dir = "data/comparisons"
            json_files = glob.glob(os.path.join(output_dir, "batch_comparison_*.json"))
            
            # Change-detection: skip if file set hasn't changed since last sync
            current_files = set(os.path.basename(f) for f in json_files)
            if current_files == self._last_synced_files:
                logger.debug("[Deep Research Sync] No changes detected, skipping.")
                return
            self._last_synced_files = current_files
            
            synced_winners = []
            
            for filepath in json_files:
                try:
                    filename = os.path.basename(filepath)
                    
                    with open(filepath, 'r') as f:
                         data = json.load(f)
                         
                    winner = data.get('winner_symbol')
                    parts = filename.replace("batch_comparison_", "").split("_")
                    date_str = parts[0] # YYYY-MM-DD
                    
                    if winner:
                        mark_batch_winner(winner, date_str)
                        synced_winners.append(winner)
                        
                except Exception as e:
                    logger.error(f"[Deep Research Sync] Error processing file {filepath}: {e}")
            
            if synced_winners:
                logger.info(f"[Deep Research Sync] Synced {len(synced_winners)} winners from batch files.")
                    
            # Cleanup Stuck Batches (Before Dec 22)
            # Query DB for STARTED batches before Dec 22
            conn = sqlite3.connect("subscribers.db")
            cursor = conn.cursor()
            cursor.execute("UPDATE batch_comparisons SET status = 'SKIPPED' WHERE status = 'STARTED' AND date < '2025-12-22'")
            if cursor.rowcount > 0:
                 logger.info(f"[Deep Research Sync] Cleaned up {cursor.rowcount} old stuck batches.")
            conn.commit()
            conn.close()
            
        except Exception as e:
            logger.error(f"[Deep Research Sync] Error in sync loop: {e}")

    def _scan_for_batches(self):
        """
        Scans for completed deep research candidates that are:
        1. From the same day
        2. Have valid verdicts
        3. Have NOT been assigned a batch_id yet
        Groups them into batches of 4 and triggers comparison.
        """
        try:
            # 0. Recover Pending/Stuck Batches first
            self._recover_pending_batches()
            
            from app.database import get_distinct_dates_with_unbatched_candidates, get_unbatched_candidates_by_date, log_batch_run
            
            dates = get_distinct_dates_with_unbatched_candidates()
            for date_str in dates:
                candidates = get_unbatched_candidates_by_date(date_str)
                # Candidates are sorted by score DESC
                
                # Process in chunks of 4
                while len(candidates) >= 4:
                    chunk = candidates[:4]
                    candidates = candidates[4:] # Remaining
                    
                    symbols = [c['symbol'] for c in chunk]
                    logger.info(f"[Deep Research Scanner] Found 4 unbatched candidates for {date_str}: {symbols}")
                    
                    # Log Batch Run to get ID
                    batch_id = log_batch_run(symbols, date_str)
                    
                    if batch_id:
                        # Link candidates to this batch_id
                        try:
                            conn = sqlite3.connect("subscribers.db")
                            cursor = conn.cursor()
                            candidate_ids = [c['id'] for c in chunk]
                            ids_placeholders = ','.join(['?'] * len(candidate_ids))
                            
                            query = f"UPDATE decision_points SET batch_id = ? WHERE id IN ({ids_placeholders})"
                            cursor.execute(query, (batch_id, *candidate_ids))
                            conn.commit()
                            conn.close()
                            
                            # Queue the task
                            self.queue_batch_comparison_task(chunk, batch_id)
                            logger.info(f"[Deep Research Scanner] Triggered Batch {batch_id} for {date_str}")
                            
                        except Exception as e:
                            logger.error(f"[Deep Research Scanner] Error linking batch {batch_id}: {e}")
                            
        except Exception as e:
            logger.error(f"[Deep Research Scanner] Error in scanner loop: {e}")

    def _recover_pending_batches(self):
        """
        Finds batches that are 'PENDING' or 'STARTED' (but timed out/zombie) and re-queues them.
        """
        try:
            conn = sqlite3.connect("subscribers.db")
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            
            # Find PENDING batches
            # AND STARTED batches that are > 30 mins old (Zombie check)
            # We use a combined query or separate. Let's start with PENDING.
            cursor.execute("SELECT * FROM batch_comparisons WHERE status = 'PENDING'")
            rows = cursor.fetchall()
            
            for row in rows:
                batch_id = row['id']
                try:
                    candidates_json = row['candidate_symbols']
                    # Expecting "A,B,C" or JSON string ["A","B"]
                    # Our log_batch_run saves as "Symbol1,Symbol2,Symbol3,Symbol4"
                    # But wait, lines 317 in previous edit used json.dumps(symbols)
                    # Let's handle both.
                    
                    symbols = []
                    if candidates_json.startswith("["):
                        symbols = json.loads(candidates_json)
                    else:
                        symbols = candidates_json.split(",")
                    
                    logger.info(f"[Deep Research Recovery] Recovering Batch {batch_id} (PENDING). Symbols: {symbols}")
                    
                    # Reconstruct candidates dict (minimal info needed for execute_batch_comparison)
                    # execute_batch_comparison only needs symbol key in list of dicts.
                    candidates = [{'symbol': s} for s in symbols]
                    
                    # Update status to STARTED (to avoid double queueing if we scan again quickly)
                    # Actually, queue_batch_comparison_task will put it in queue. 
                    # We should probably mark it as QUEUED internally? 
                    # Database status 'STARTED' is used when we create it.
                    # PENDING was my manual set.
                    # If I set it to STARTED here, verify_batch_logic might not pick it up if I don't queue it.
                    # Queue it first.
                    
                    self.queue_batch_comparison_task(candidates, batch_id)
                    
                    # Update DB to STARTED so we don't pick it up again immediately
                    cursor.execute("UPDATE batch_comparisons SET status = 'STARTED', timestamp = CURRENT_TIMESTAMP WHERE id = ?", (batch_id,))
                    conn.commit()
                    
                except Exception as e:
                    logger.error(f"[Deep Research Recovery] Error recovering Batch {batch_id}: {e}")

            conn.close()
            
        except Exception as e:
            logger.error(f"[Deep Research Recovery] Error in recovery: {e}")
        
    def _worker_loop(self):
        """
        Consumes tasks from the queues and executes them one by one.
        PRIORITY: Individual Stock > Batch Comparison
        """
        logger.info("[Deep Research] Worker thread started.")
        while self.is_running:
            try:
                # 1. Rate Limiting Check (1 Minute Cooldown)
                time_since_last = time.time() - self.last_api_call_time
                if time_since_last < self.cooldown_seconds:
                     sleep_time = self.cooldown_seconds - time_since_last
                     # Only log sleep if we actually have work pending? 
                     # Or just sleep. Simple approach: sleep if we have work or checked queue.
                     # But better to check queues first before sleeping, unless we want strict rate limit enforcement.
                     # Let's check if there is work before sleeping to reduce log noise.
                     if not self.individual_queue.empty() or not self.batch_queue.empty():
                         logger.info(f"[Deep Research] Rate Limit: Sleeping for {sleep_time:.2f}s before next task...")
                         time.sleep(sleep_time)

                # 2. Get Task (Priority Logic)
                task_wrapper = None
                
                # Check Individual Queue First (Non-blocking)
                try:
                    task_wrapper = self.individual_queue.get_nowait()
                except Empty:
                    # Check Batch Queue if Individual is Empty
                    try:
                        task_wrapper = self.batch_queue.get_nowait()
                    except Empty:
                        pass
                
                if not task_wrapper:
                    time.sleep(1) # Idle wait
                    continue
                
                task_type = task_wrapper.get('type')
                task_payload = task_wrapper.get('payload')
                
                symbol_display = task_payload.get('symbol', 'BATCH') if task_type == 'individual' else "BATCH_COMPARISON"
                
                with self.lock:
                    self.active_tasks_count = 1
                    
                logger.info(f"[Deep Research] Starting task: {task_type} for {symbol_display}")
                
                 # Update Monitoring State
                with self.lock:
                    self.current_task_name = f"{task_type} ({symbol_display})"
                    self.current_task_start_time = time.time()
                
                if task_type == 'individual':
                     self._process_individual_task(task_payload)
                     self.individual_queue.task_done()
                elif task_type == 'batch_comparison':
                     self._process_batch_task(task_payload)
                     self.batch_queue.task_done()
                     
                with self.lock:
                    self.active_tasks_count = 0
                    self.current_task_name = None
                    self.current_task_start_time = None
                    self.last_api_call_time = time.time() # Mark completion time for cooldown logic
                
            except Exception as e:
                logger.error(f"[Deep Research] Worker Loop Error: {e}")
                with self.lock:
                    self.active_tasks_count = 0

    def queue_research_task(self, symbol: str, context: dict, decision_id: int):
        """
        Queues an individual deep research task (HIGH PRIORITY).
        context: Pre-built context dict from StockService._build_deep_research_context()
        """
        payload = {
            'symbol': symbol,
            'context': context,
            'decision_id': decision_id,
        }
        self.individual_queue.put({'type': 'individual', 'payload': payload})
        logger.info(f"[Deep Research] Queued INDIVIDUAL task for {symbol} (Priority: High)")

    def queue_batch_comparison_task(self, candidates: List[Dict], batch_id: int):
        """
        Queues a batch comparison task (LOW PRIORITY).
        candidates: List of decision_data dictionaries (Top 3).
        batch_id: Database ID for tracking.
        """
        payload = {
            'candidates': candidates,
            'batch_id': batch_id
        }
        self.batch_queue.put({'type': 'batch_comparison', 'payload': payload})
        logger.info(f"[Deep Research] Queued BATCH COMPARISON task for {len(candidates)} candidates (Priority: Low)")


    def _process_individual_task(self, payload):
        """
        Executes individual deep research.
        """
        symbol = payload['symbol']
        try:
            result = self.execute_deep_research(
                symbol=symbol,
                context=payload['context'],
                decision_id=payload.get('decision_id')
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

    def _calculate_deep_research_score(self, result: dict) -> int:
        """
        Composite scoring for deep research results.
        Components:
          - Verdict weight (40 pts max)
          - Conviction (25 pts max)
          - Risk/Reward bonus (20 pts max)
          - Knife catch penalty (-15)
          - Dispute penalty (-5 per disputed claim)
        """
        score = 0

        # Verdict weight (review_verdict)
        verdict_map = {
            "UPGRADED": 40,
            "CONFIRMED": 35,
            "ADJUSTED": 20,
            "OVERRIDDEN": 5,
            "ERROR_PARSING": 0,
        }
        review_verdict = result.get("review_verdict", "ERROR_PARSING")
        score += verdict_map.get(review_verdict, 0)

        # Conviction
        conviction_map = {"HIGH": 25, "MODERATE": 15, "LOW": 5}
        score += conviction_map.get(result.get("conviction", "LOW"), 5)

        # Risk/Reward bonus
        try:
            rr = float(result.get("risk_reward_ratio", 0))
            if rr >= 2.0:
                score += 20
            elif rr >= 1.5:
                score += 15
            elif rr >= 1.0:
                score += 10
        except (TypeError, ValueError):
            pass

        # Knife catch penalty
        if result.get("knife_catch_warning") in (True, "True", "true"):
            score -= 15

        # Dispute penalty: -5 per DISPUTED claim in verification_results
        for v in result.get("verification_results", []):
            if isinstance(v, str) and "DISPUTED" in v.upper():
                score -= 5

        return max(0, min(100, score))

    def _handle_completion(self, task, result):
        """
        Handles the completed research result (DB update, file save).
        """
        symbol = task['symbol']
        decision_id = task.get('decision_id')
        review_verdict = result.get('review_verdict', 'UNKNOWN')
        action = result.get('action', 'AVOID')
        logger.info(f"[Deep Research] Task Completed for {symbol}. Review Verdict: {review_verdict}, Action: {action}")
        
        try:
            from app.database import update_deep_research_data
            
            # Calculate composite score
            score = self._calculate_deep_research_score(result)
            
            # Extract fields
            swot = json.dumps(result.get('swot_analysis', {}))
            global_analysis = result.get('global_market_analysis', '')
            local_analysis = result.get('local_market_analysis', '')
            verification = json.dumps(result.get('verification_results', []))
            blindspots = json.dumps(result.get('council_blindspots', []))
            
            # Map review_verdict to the legacy verdict field for backward compat
            # CONFIRMED/UPGRADED -> action value, ADJUSTED -> action, OVERRIDDEN -> AVOID
            verdict_for_db = action  # Use the action as the verdict
            
            # Update DB with all new fields
            success = update_deep_research_data(
                decision_id=decision_id,
                verdict=verdict_for_db,
                risk=result.get('risk_level', 'Unknown'),
                catalyst=result.get('catalyst_type', 'Unknown'),
                knife_catch=str(result.get('knife_catch_warning', False)),
                score=score,
                swot=swot,
                global_analysis=global_analysis,
                local_analysis=local_analysis,
                # New fields
                review_verdict=review_verdict,
                action=action,
                conviction=result.get('conviction', 'LOW'),
                entry_low=result.get('entry_price_low'),
                entry_high=result.get('entry_price_high'),
                stop_loss=result.get('stop_loss'),
                tp1=result.get('take_profit_1'),
                tp2=result.get('take_profit_2'),
                upside=result.get('upside_percent'),
                downside=result.get('downside_risk_percent'),
                rr_ratio=result.get('risk_reward_ratio'),
                drop_type=result.get('drop_type'),
                entry_trigger=result.get('entry_trigger'),
                verification=verification,
                blindspots=blindspots,
                reason=result.get('reason', ''),
            )
            
            if success:
                logger.info(f"[Deep Research] Successfully updated DB for {symbol} (Score: {score}, Verdict: {review_verdict})")
            else:
                logger.error(f"[Deep Research] Failed to update DB for {symbol}")

            # Save to file
            self._save_result_to_file(symbol, result)
            
        except Exception as e:
            logger.error(f"[Deep Research] Error updating DB: {e}")

    def _check_and_trigger_batch(self):
        """
        Delegates to _scan_for_batches to ensure consistent logic (Same Date, Valid Count).
        """
        logger.info("[Deep Research] Trigger checking for new batches...")
        self._scan_for_batches()

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

    def execute_deep_research(self, symbol: str, context: dict, decision_id: int = None) -> Optional[Dict]:
        """
        The synchronous execution logic. Takes a pre-built context dict.
        """
        # 1. Construct the Prompt
        prompt = self._construct_prompt(symbol, context)
        
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
                    # If this was a backfill run (decision_id provided), update the DB here too
                    # The normal flow does this in _handle_completion, but standalone scripts call this directly.
                    # We return the result, so the caller can handle it, but saving to file happens later?
                    # Actually _handle_completion calls this, then does the DB update.
                    # Our script will need to handle the DB update or we pass decision_id here and do it?
                    # The original code returns the result and _process_individual_task calls _handle_completion.
                    # So we should just return result.
                    
                    return self._parse_output(poll_data, schema_type='individual')
                elif status in ['failed', 'FAILED']:
                    logger.error(f"[Deep Research] Task Failed for {symbol}: {poll_data}")
                    return None
                    
            logger.error(f"[Deep Research] Task Timeout for {symbol}")
            return None
            
        except Exception as e:
            logger.error(f"[Deep Research] Execution Exception: {e}")
            return None

    def _construct_prompt(self, symbol: str, context: dict) -> str:
        """
        Deep Research prompt — acts as a SENIOR REVIEWER
        of the council's decision, not a redo of the analysis.
        """
        pm_decision = context.get("pm_decision", {})
        bull_case = context.get("bull_case", "Not available")
        bear_case = context.get("bear_case", "Not available")
        tech_data = context.get("technical_data", {})
        drop_percent = context.get("drop_percent", 0)
        raw_news = context.get("raw_news", [])
        transcript_summary = context.get("transcript_summary", "")
        transcript_date = context.get("transcript_date", "Unknown")
        data_depth = context.get("data_depth", {})

        # Format PM decision compactly
        pm_summary = json.dumps(pm_decision, indent=2)
        tech_str = json.dumps(tech_data, indent=2) if tech_data else "No technical data."

        # Format news (paywalled — deep research can't access these via Google Search)
        news_str = ""
        if raw_news:
            for n in raw_news[:20]:
                date = n.get('datetime_str', 'N/A')
                source = n.get('source', 'Unknown')
                source_type = n.get('source_type', 'WIRE')
                headline = n.get('headline', 'No Headline')
                summary = n.get('summary', '')
                content = n.get('content', '')
                news_str += f"- {date} [{source_type}] [{source}]: {headline}\n"
                if content:
                    news_str += f"  {content[:1500]}\n\n"
                elif summary:
                    news_str += f"  {summary[:500]}\n\n"

        # Evidence quality note
        news_count = data_depth.get("news", {}).get("total_count", 0) if isinstance(data_depth, dict) else 0
        evidence_note = f"Council analyzed {news_count} news articles. {len(raw_news)} articles provided below (paywalled sources — not available via Google Search)."

        transcript_section = ""
        if transcript_summary and transcript_summary != "No transcript summary available from council." and transcript_summary != "No transcript summary available from backfill.":
            transcript_section = f"""
EARNINGS TRANSCRIPT SUMMARY (Date: {transcript_date}):
(Condensed by the News Agent from the full earnings call — key points preserved)
{transcript_summary}
"""

        return f"""
You are a **Senior Investment Reviewer** at a hedge fund. An internal AI council
has already analyzed stock {symbol} which dropped {drop_percent:.2f}% today
and recommends it as a potential "buy the dip" opportunity.

Your job is NOT to redo the analysis. Your job is to:
1. **CHALLENGE** the council's recommendation — find what they might have missed
2. **VERIFY** their key claims using fresh Google Search data
3. **REFINE** the trading levels (entry, stop-loss, take-profit) if the council got them wrong
4. **CONFIRM or OVERRIDE** the final verdict

You are the last line of defense before real money is deployed.

═══════════════════════════════════════════════════════
COUNCIL DECISION (This is what you are reviewing):
═══════════════════════════════════════════════════════
{pm_summary}

═══════════════════════════════════════════════════════
BULL CASE (Constructed by Council's Bull Researcher):
═══════════════════════════════════════════════════════
{bull_case[:4000]}

═══════════════════════════════════════════════════════
BEAR CASE (Constructed by Council's Bear Researcher):
═══════════════════════════════════════════════════════
{bear_case[:4000]}

═══════════════════════════════════════════════════════
TECHNICAL DATA (Raw Indicators):
═══════════════════════════════════════════════════════
{tech_str}

{transcript_section}

═══════════════════════════════════════════════════════
NEWS ARTICLES (Paywalled Sources — NOT available via Google Search):
═══════════════════════════════════════════════════════
These articles are from Benzinga/Polygon, Alpha Vantage, Finnhub, and other
premium sources. You CANNOT access these via Google Search. Use this data
as primary evidence and verify/supplement with your own Google Search.

SOURCE PRIORITY (each article is tagged with a source_type):
1. OFFICIAL (press releases, SEC filings) — ground truth
2. WIRE (Benzinga, Reuters, Finnhub) — factual reporting
3. ANALYST (Seeking Alpha, Motley Fool) — informed opinion, check for bias
4. MARKET_CONTEXT — broad signals, not company-specific
When an ANALYST article contradicts a WIRE report, trust the WIRE source for facts.

{news_str if news_str else "No paywalled news articles available."}

═══════════════════════════════════════════════════════
DATA QUALITY NOTE:
═══════════════════════════════════════════════════════
{evidence_note}

═══════════════════════════════════════════════════════
YOUR TASK:
═══════════════════════════════════════════════════════

STEP 1: VERIFY KEY CLAIMS
Use Google Search to independently verify the top 3 claims from the council:
- Is the drop reason accurate? Search for the actual news.
- Is the earnings data correct? Check the actual numbers.
- Are there NEW developments since the council ran (breaking news, analyst notes, insider trades)?

STEP 2: CHALLENGE THE THESIS
Play devil's advocate against the council's BUY recommendation:
- What's the worst-case scenario they didn't consider?
- Is there a liquidity risk, delisting risk, or regulatory action pending?
- Did they misjudge what's "priced in"?

STEP 3: VALIDATE TRADING LEVELS
Review the council's entry zone, stop-loss, and take-profit:
- Is the stop-loss realistic? (Too tight = will get stopped out on noise. Too wide = too much risk.)
- Is the take-profit achievable? (Pre-drop price may not be realistic if the fundamental story changed.)
- Would YOU adjust any of these levels based on your research?

STEP 4: SWOT ANALYSIS
Based on your independent research, construct a SWOT:
- Strengths: What competitive advantages protect this company?
- Weaknesses: What structural problems exist?
- Opportunities: What catalysts could drive recovery?
- Threats: What risks could prevent recovery?

STEP 5: FINAL VERDICT
After your review, decide:
- **CONFIRMED**: Council's recommendation stands. You agree with the setup.
- **UPGRADED**: You found additional positive evidence the council missed. Even better than they thought.
- **ADJUSTED**: The thesis is okay but trading levels need correction. Provide corrected levels.
- **OVERRIDDEN**: You found critical issues the council missed. Do NOT buy this stock.

> **Philosophical Reminder (Elm Partners Paradox):**
> Even traders with tomorrow's news often lose because they misjudge what's priced in.
> If the news driving this drop is obvious to everyone, the recovery may already be priced in.
> Look for the REACTION to the news, not just the news itself.
> Humility: If you can't verify the "why," the risk is higher than the council thinks.

OUTPUT FORMAT:
Your output must be valid JSON. All price fields must be numbers. All percentage fields must be numbers.
{{
  "review_verdict": "CONFIRMED" | "UPGRADED" | "ADJUSTED" | "OVERRIDDEN",
  "action": "BUY" | "BUY_LIMIT" | "WATCH" | "AVOID",
  "conviction": "HIGH" | "MODERATE" | "LOW",
  "drop_type": "EARNINGS_MISS" | "ANALYST_DOWNGRADE" | "SECTOR_ROTATION" | "MACRO_SELLOFF" | "COMPANY_SPECIFIC" | "TECHNICAL_BREAKDOWN" | "UNKNOWN",
  "risk_level": "Low" | "Medium" | "High" | "Extreme",
  "catalyst_type": "Structural" | "Temporary" | "Noise",
  "entry_price_low": <number>,
  "entry_price_high": <number>,
  "stop_loss": <number>,
  "take_profit_1": <number>,
  "take_profit_2": <number or null>,
  "upside_percent": <number>,
  "downside_risk_percent": <number>,
  "risk_reward_ratio": <number>,
  "pre_drop_price": <number>,
  "entry_trigger": "Specific condition for entry",
  "reassess_in_days": <number>,
  "global_market_analysis": "Brief analysis of global market conditions",
  "local_market_analysis": "Brief analysis of local/sector conditions",
  "swot_analysis": {{
    "strengths": ["point 1", "point 2"],
    "weaknesses": ["point 1", "point 2"],
    "opportunities": ["point 1", "point 2"],
    "threats": ["point 1", "point 2"]
  }},
  "verification_results": [
    "Claim 1: [VERIFIED/DISPUTED] — explanation",
    "Claim 2: [VERIFIED/DISPUTED] — explanation",
    "Claim 3: [VERIFIED/DISPUTED] — explanation"
  ],
  "council_blindspots": ["Issue 1 the council missed", "Issue 2"],
  "knife_catch_warning": true | false,
  "reason": "One sentence: your final assessment as the senior reviewer."
}}
"""

    def _repair_json_using_flash(self, raw_text: str, schema_type: str = 'individual') -> Optional[Dict]:
        """
        Uses Gemini Flash to extract JSON from a malformed report.
        """
        try:
            logger.info(f"[Deep Research] Attempting to repair output ({schema_type}) using Gemini Flash...")
            
            # Clean up the raw text if it looks like a Python dict string
            clean_text = raw_text
            
            schema_def = ""
            if schema_type == 'batch':
                schema_def = """
{
  "winner_symbol": "TICKER",
  "rationale": "Detailed explanation...",
  "projected_timeline": "1-12 Months",
  "ranking": ["TICKER_1", "TICKER_2", "TICKER_3", "TICKER_4"]
}
"""
            else:
                # Individual Stock Schema — Senior Reviewer output
                schema_def = """
{
  "review_verdict": "CONFIRMED | UPGRADED | ADJUSTED | OVERRIDDEN",
  "action": "BUY | BUY_LIMIT | WATCH | AVOID",
  "conviction": "HIGH | MODERATE | LOW",
  "drop_type": "EARNINGS_MISS | ANALYST_DOWNGRADE | SECTOR_ROTATION | MACRO_SELLOFF | COMPANY_SPECIFIC | TECHNICAL_BREAKDOWN | UNKNOWN",
  "risk_level": "Low | Medium | High | Extreme",
  "catalyst_type": "Structural | Temporary | Noise",
  "entry_price_low": 0.0,
  "entry_price_high": 0.0,
  "stop_loss": 0.0,
  "take_profit_1": 0.0,
  "take_profit_2": null,
  "upside_percent": 0.0,
  "downside_risk_percent": 0.0,
  "risk_reward_ratio": 0.0,
  "pre_drop_price": 0.0,
  "entry_trigger": "string",
  "reassess_in_days": 5,
  "global_market_analysis": "Brief analysis string",
  "local_market_analysis": "Brief analysis string",
  "swot_analysis": {
    "strengths": ["list", "of", "strings"],
    "weaknesses": ["list", "of", "strings"],
    "opportunities": ["list", "of", "strings"],
    "threats": ["list", "of", "strings"]
  },
  "verification_results": ["list", "of", "strings"],
  "council_blindspots": ["list", "of", "strings"],
  "knife_catch_warning": true,
  "reason": "string"
}
"""

            prompt = f"""
You are a data extraction assistant. I have a stock analysis report that is not in the required JSON format.
Please extract the relevant information and format it EXACTLY as this JSON object.
Do not include markdown formatting or code blocks around the JSON. Just return the raw JSON string.

SCHEMA:
{schema_def}

REPORT CONTENT:
{clean_text}
"""
            url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent"
            headers = {
                "Content-Type": "application/json",
                "x-goog-api-key": self.api_key
            }
            payload = {
                "contents": [{"parts": [{"text": prompt}]}],
                "generationConfig": {"response_mime_type": "application/json"}
            }
            
            # Timeout of 30s is enough for Flash
            response = requests.post(url, headers=headers, json=payload, timeout=30)
            if response.status_code != 200:
                logger.error(f"[Deep Research] Repair API Error: {response.text}")
                return None
                
            data = response.json()
            if 'candidates' in data and data['candidates']:
                repair_text = data['candidates'][0]['content']['parts'][0]['text']
                return json.loads(repair_text)
            
            return None
            
        except Exception as e:
            logger.error(f"[Deep Research] Repair failed: {e}")
            return None

    def _parse_output(self, poll_data, schema_type: str = 'individual') -> Optional[Dict]:
        try:
            outputs = poll_data.get('outputs', [])
            if not outputs: 
                logger.warning("[Deep Research] No outputs found in poll data.")
                return None
            
            logger.info(f"[Deep Research] Parsing outputs. Count: {len(outputs)}")
            
            # Iterate through all outputs to find the best candidate
            best_text_candidate = ""
            for output in reversed(outputs):
                text = output.get('text', str(output)) if isinstance(output, dict) else str(output)
                
                # Update candidate (take the last one seen, which is the first in reversed list)
                if not best_text_candidate:
                    best_text_candidate = text

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
            final_text = str(outputs[-1]) if outputs else "No Output"
            
            # Use the cleaner text candidate if available, else raw
            text_to_repair = best_text_candidate if best_text_candidate else final_text
            
            # --- ATTEMPT REPAIR ---
            repaired_json = self._repair_json_using_flash(text_to_repair, schema_type=schema_type)
            if repaired_json:
                logger.info("[Deep Research] Successfully repaired JSON output.")
                repaired_json['raw_report_full'] = final_text # Keep raw text
                return repaired_json
            
            logger.warning(f"[Deep Research] JSON Parse & Repair failed. Using Raw Fallback. Length: {len(final_text)}")
            
            # Construct a fallback JSON matching the new schema
            return {
                "review_verdict": "ERROR_PARSING",
                "action": "AVOID",
                "conviction": "LOW",
                "drop_type": "UNKNOWN",
                "risk_level": "Unknown",
                "catalyst_type": "Parse Error",
                "entry_price_low": None,
                "entry_price_high": None,
                "stop_loss": None,
                "take_profit_1": None,
                "take_profit_2": None,
                "upside_percent": None,
                "downside_risk_percent": None,
                "risk_reward_ratio": None,
                "pre_drop_price": None,
                "entry_trigger": None,
                "reassess_in_days": None,
                "global_market_analysis": "See Raw Report",
                "local_market_analysis": "See Raw Report",
                "swot_analysis": {
                    "strengths": [], "weaknesses": [], "opportunities": [], "threats": []
                },
                "verification_results": [
                    "JSON Parsing Failed.",
                    "Raw Output Below:",
                    final_text[:3000]
                ],
                "council_blindspots": [],
                "knife_catch_warning": True,
                "reason": "Deep research output could not be parsed.",
                "raw_report_full": final_text
            }
            
        except Exception as e:
            logger.error(f"[Deep Research] Error parsing: {e}")
            return None

    def _load_council_report(self, symbol: str, date_str: str) -> str:
        """
        Attempts to load the council report for the given symbol and date.
        Returns the report content or empty string if not found.
        """
        try:
            import glob
            # Pattern: data/council_reports/{symbol}_{date}_council1.json
            report_dir = "data/council_reports"
            expected_file = os.path.join(report_dir, f"{symbol}_{date_str}_council1.json")
            
            if os.path.exists(expected_file):
                 with open(expected_file, 'r') as f:
                     data = json.load(f)
                     return json.dumps(data, indent=2)
            
            return ""
            
        except Exception as e:
            logger.warning(f"[Deep Research] Failed to load council report for {symbol}: {e}")
            return ""

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
            
            # Construct Prompt (Fresh Research + Supplementary Context)
            candidates_list_str = ", ".join(symbols)
            
            # Load Council Reports
            context_data = ""
            date_str = datetime.now().strftime("%Y-%m-%d") # Default to today
            # Use candidate timestamp if available
            if candidates:
                ts = candidates[0].get('timestamp')
                if ts: date_str = ts.split(' ')[0]

            for cand in candidates:
                sym = cand['symbol']
                report_str = self._load_council_report(sym, date_str)
                if report_str:
                    summary = self._summarize_report_context(report_str)
                    context_data += f"\n--- SUPPLEMENTARY REPORT FOR {sym} ---\n{summary}\n"
                    
                    # Console Output for User Verification
                    print(f"\n[{sym}] SUMMARIZED CONTEXT (Token Optimization):")
                    print("-" * 40)
                    print(summary)
                    print("-" * 40)


            prompt = f"""
You are the **Lead Portfolio Manager**. You are tasked with researching and comparing the following stock candidates to find the single best buying opportunity for today.

CANDIDATES: {candidates_list_str}

TASK:
1. **Deep Research:** Use Google Search to find the latest news, earnings reports, and market sentiment for each candidate.
2. **Review Context:** Use the provided SUPPLEMENTARY REPORTS (below) as a secondary source of information to guide your research, but prioritize YOUR fresh research if there are newer developments.
3. **Compare:** Analyze which stock offers the best risk/reward ratio RIGHT NOW.
4. **Select Winner:** Pick the ONE stock that is the "Stock of the Day".

CRITERIA:
- **Catalyst:** Is there a valid reason for the drop/price action?
- **Recovery Potential:** Which has the best chance of bouncing back?
- **Safety:** Avoid bankruptcy risks or falling knives.

CONTEXT (Use as Supplementary Info):
{context_data}

OUTPUT:
A JSON object:
{{
  "winner_symbol": "TICKER",
  "rationale": "Detailed explanation why this stock wins over the others.",
  "projected_timeline": "1-12 Months",
  "ranking": ["TICKER_1", "TICKER_2", "TICKER_3", "TICKER_4"]
}}
"""
            
            print(f"DEBUG: SUBMISSION PROMPT: (Deep Research for {candidates_list_str})")
            
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
                    outputs = poll_data.get('outputs', [])
                    if not outputs:
                        logger.debug(f"[Deep Research] No outputs in poll data. Keys: {list(poll_data.keys())}")
                    
                    
                    # Parse Output using existing helper
                    parsed_result = self._parse_output(poll_data, schema_type='batch')
                    if parsed_result:
                         # We expect the agent to output the JSON we asked for.
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
                    return None
            
            if not result_text:
                logger.error("[Deep Research] Batch Comparison Timeout or No Result.")
                print("[Deep Research] Batch Comparison Timeout.")
                return None

            logger.info(f"[Deep Research] Comparison Result: {result_text}")
            
            # Save Comparison Result (Data Driven Persistence)
            date_str = datetime.now().strftime("%Y-%m-%d")
            # Sort symbols to ensure deterministic filename
            symbols_str = "_".join(sorted(symbols))
            filename_base = f"batch_comparison_{date_str}_{symbols_str}"
            
            output_dir = "data/comparisons"
            os.makedirs(output_dir, exist_ok=True)
            
            json_filepath = os.path.join(output_dir, f"{filename_base}.json")
            with open(json_filepath, "w") as f:
                 f.write(result_text)
            logger.info(f"[Deep Research] Saved comparison JSON to {json_filepath}")
            
            # Save PDF
            try:
                self._save_batch_pdf(symbols, result_text, os.path.join(output_dir, f"{filename_base}.pdf"))
            except Exception as e:
                logger.error(f"[Deep Research] Failed to save Batch PDF: {e}")
            
            try:
                # Try to parse if we haven't already (though parsed_result might be available)
                if not parsed_result:
                    parsed_result = json.loads(result_text)

                res_json = parsed_result # Use the parsed object
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
                
                # Mark Winner in DB
                if winner and winner != 'UNKNOWN' and winner != 'N/A':
                    try:
                        from app.database import mark_batch_winner
                        # Attempt to get date from candidates to be precise
                        target_date = None
                        if candidates:
                             ts = candidates[0].get('timestamp', '') # Expect 'YYYY-MM-DD HH:MM:SS'
                             if ts:
                                 target_date = ts.split(' ')[0]
                        
                        if mark_batch_winner(winner, target_date):
                             logger.info(f"[Deep Research] Marked {winner} as Batch Winner (Date: {target_date})")
                    except Exception as e:
                        logger.error(f"[Deep Research] Failed to mark batch winner: {e}")
                
                # Update DB Status to COMPLETED
                if batch_id:
                     if os.path.exists(json_filepath) and os.path.getsize(json_filepath) > 0:
                         from app.database import update_batch_status
                         update_batch_status(batch_id, 'COMPLETED')
                         logger.info(f"[Deep Research] Batch {batch_id} marked as COMPLETED.")
                     else:
                         from app.database import update_batch_status
                         update_batch_status(batch_id, 'FAILED')
                         logger.error(f"[Deep Research] Batch {batch_id} FAILED: Output file missing or empty.")
               
                return parsed_result

            except:
                print(f"\n[Deep Research] Comparison Complete. (Could not parse JSON output)")
                print(result_text)
                # Still mark as completed if text was returned? Or failed?
                if batch_id:
                     if os.path.exists(json_filepath) and os.path.getsize(json_filepath) > 0:
                         from app.database import update_batch_status
                         update_batch_status(batch_id, 'COMPLETED')
                     else:
                         from app.database import update_batch_status
                         update_batch_status(batch_id, 'FAILED')
                return None

        except Exception as e:
            logger.error(f"[Deep Research] Comparison Agent Failed: {e}")
            print(f"[Deep Research] Comparison Agent Error: {e}")
            if batch_id:
                 from app.database import update_batch_status
                 update_batch_status(batch_id, 'FAILED')




    def _save_batch_pdf(self, symbols, result_text, filepath):
        try:
            from reportlab.lib.pagesizes import letter
            from reportlab.lib import colors
            from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer
            from reportlab.lib.styles import getSampleStyleSheet
            
            doc = SimpleDocTemplate(filepath, pagesize=letter)
            styles = getSampleStyleSheet()
            
            story = []
            story.append(Paragraph(f"Batch Comparison: {', '.join(symbols)}", styles['Title']))
            story.append(Spacer(1, 12))
            
            import json
            try:
                data = json.loads(result_text)
                
                winner = data.get('winner_symbol', 'UNKNOWN')
                ranking = data.get('ranking', [])
                rationale = data.get('rationale', 'No rationale provided.')
                timeline = data.get('projected_timeline', 'N/A')
                
                story.append(Paragraph(f"<b>WINNER:</b> <font color='green'>{winner}</font>", styles['Heading2']))
                story.append(Spacer(1, 12))
                
                story.append(Paragraph(f"<b>Ranking:</b> {', '.join(ranking)}", styles['BodyText']))
                story.append(Spacer(1, 12))
                
                story.append(Paragraph("<b>Rationale:</b>", styles['Heading3']))
                story.append(Paragraph(rationale, styles['BodyText']))
                story.append(Spacer(1, 12))
                
                story.append(Paragraph(f"<b>Projected Timeline:</b> {timeline}", styles['BodyText']))
                
            except json.JSONDecodeError:
                story.append(Paragraph("Raw Result (Parse Error):", styles['Heading2']))
                story.append(Paragraph(result_text, styles['BodyText']))
                
            doc.build(story)
            logger.info(f"[Deep Research] Saved Batch PDF to {filepath}")
            
        except Exception as e:
            logger.error(f"[Deep Research] Error generating Batch PDF: {e}")

    def _summarize_report_context(self, report_json_str: str) -> str:
        """
        Extracts only high-value signals from a full council report to save tokens.
        Drops raw data, evidence dumps, and full transcripts.
        """
        try:
            data = json.loads(report_json_str)
            summary = []
            
            # 1. High-Level Verdicts
            # In some reports, 'final_verdict' might be at root. In others, it's not present yet if this is Pre-Deep Research.
            if 'final_verdict' in data:
                summary.append(f"FINAL VERDICT: {data.get('final_verdict')}")
            
            # 2. Bull/Bear Debate
            # If explicit key exists
            if 'bull_bear_debate' in data:
                 bb = data['bull_bear_debate']
                 if isinstance(bb, str):
                     summary.append(f"BULL/BEAR DEBATE: {bb[:1000]}...") 
                 elif isinstance(bb, dict):
                     summary.append(f"BULL CASE: {bb.get('bull_case', '')[:800]}")
                     summary.append(f"BEAR CASE: {bb.get('bear_case', '')[:800]}")
            
            # 3. Agent Sub-Reports
            # The keys in the JSON are simply 'technical', 'fundamental', etc.
            # We need to map them or check both.
            agent_keys = {
                'technical': 'TECHNICAL',
                'technical_analysis': 'TECHNICAL',
                'fundamental': 'FUNDAMENTAL',
                'fundamental_analysis': 'FUNDAMENTAL',
                'sentiment': 'SENTIMENT',
                'sentiment_analysis': 'SENTIMENT',
                'quantitative': 'QUANT',
                'quantitative_analysis': 'QUANT',
                'valuation': 'VALUATION',
                'valuation_analysis': 'VALUATION'
            }
            
            for key, label in agent_keys.items():
                if key in data:
                    content = data[key]
                    # The content is often a huge Markdown string (as seen in TRU report)
                    if isinstance(content, str):
                        # Try to extract the Verdict line
                        first_line = content.split('\n')[0]
                        # Look for "Verdict:" pattern
                        import re
                        match = re.search(r'\*\*Verdict:?\s*(.*?)\*\*', content)
                        if match:
                             verdict_text = match.group(1)
                             summary.append(f"{label} VERDICT: {verdict_text}")
                        else:
                             # Fallback: First 200 chars
                             summary.append(f"{label} SUMMARY: {content[:200]}...")
                             
                    elif isinstance(content, dict):
                         # If structured info
                         verdict = content.get('verdict') or content.get('conclusion')
                         if verdict:
                             summary.append(f"{label} VERDICT: {verdict}")
                             
            # 4. News / Seeking Alpha (EXTREME CONDENSATION)
            # The TRU report shows 'news' as a markdown string.
            if 'news' in data:
                news_blob = data['news']
                if isinstance(news_blob, str):
                    # Just grab the "Reason for Drop" header if possible
                    match = re.search(r'# Reason for Drop\s*(.*?)(?=#|\Z)', news_blob, re.DOTALL)
                    if match:
                        drop_reason = match.group(1).strip()[:500]
                        summary.append(f"NEWS (DROP REASON): {drop_reason}")
            
            # 5. Deep Research (Previous)
            if 'deep_research_output' in data:
                 dr = data['deep_research_output']
                 summary.append(f"DEEP RESEARCH (PREV): Verdict={dr.get('verdict', 'N/A')}")

            result = "\n".join(summary)
            if not result:
                return "No structured summary available."
            return result
            
        except Exception as e:
            logger.warning(f"[Deep Research] Error summarizing report context: {e}")
            return "Summary unavailable due to parse error."

deep_research_service = DeepResearchService()
