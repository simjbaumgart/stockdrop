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
            
            logger.info(f"[Deep Research Sync] Found {len(json_files)} batch result files.")
            
            for filepath in json_files:
                try:
                    filename = os.path.basename(filepath)
                    # format: batch_comparison_{date}_{prob_symbols}.json
                    # But symbols might contain underscores, so parsing logic needs care.
                    # Actually, we rely on the CONTENT for the winner.
                    # DB link is harder if we don't know the batch_id directly.
                    # But we can find the batch by (Date + Symbols) using SQL.
                    # Let's read content first.
                    
                    with open(filepath, 'r') as f:
                         data = json.load(f)
                         
                    winner = data.get('winner_symbol')
                    # We need the date from filename or content? 
                    # filename: batch_comparison_YYYY-MM-DD_...
                    parts = filename.replace("batch_comparison_", "").split("_")
                    date_str = parts[0] # YYYY-MM-DD
                    
                    if winner:
                        # Mark Winner
                        mark_batch_winner(winner, date_str)
                        logger.info(f"[Deep Research Sync] Synced winner {winner} from file.")
                        
                        # Find and Mark Batch as COMPLETED
                        # We need to find the batch_id that matches this file.
                        # Symbols are in the filename (rest of parts), but order matters.
                        # Actually, we can search by status 'STARTED' and date? 
                        # Or just find any batch with this date/symbols.
                        pass # For now, winner update is key.
                        
                except Exception as e:
                    logger.error(f"[Deep Research Sync] Error processing file {filepath}: {e}")
                    
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

    def queue_research_task(self, symbol, raw_news, technical_data, drop_percent, decision_id, transcript_text="", transcript_date=None, transcript_warning=None, market_sentiment_report=None, competitive_report=None):
        """
        Queues an individual deep research task (HIGH PRIORITY).
        """
        payload = {
            'symbol': symbol,
            'raw_news': raw_news,
            'technical_data': technical_data,
            'drop_percent': drop_percent,
            'decision_id': decision_id,
            'transcript_text': transcript_text,
            'transcript_date': transcript_date,
            'transcript_warning': transcript_warning,
            'market_sentiment_report': market_sentiment_report,
            'competitive_report': competitive_report
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
                symbol, 
                payload['raw_news'], 
                payload['technical_data'], 
                payload['drop_percent'], 
                payload['transcript_text'], 
                payload['transcript_date'], 
                payload['transcript_warning'],
                payload.get('market_sentiment_report'),
                payload.get('competitive_report'),
                payload.get('summary_report'), # Support for Backfill
                payload.get('decision_id')     # Pass decision_id for DB update
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
                
                # --- AUTO-TRIGGER BATCH COMPARISON ---
                # Logic: Once 4 evaluations are completed, trigger a batch comparison.
                self._check_and_trigger_batch()
            else:
                logger.error(f"[Deep Research] Failed to update DB for {symbol}")

            # Save to file
            self._save_result_to_file(symbol, result)
            
        except Exception as e:
            logger.error(f"[Deep Research] Error updating DB: {e}")

    def _check_and_trigger_batch(self):
        """
        Checks if there are at least 4 stocks with completed deep research that haven't been batched yet.
        If so, creates a batch of 4 and queues a comparison.
        """
        try:
            from app.database import get_unbatched_candidates, log_batch_run
            
            # 1. Get Unbatched Candidates (Limit 4, Sorted by Score Desc)
            # We need to implement get_unbatched_candidates in database.py or do raw query here.
            # Let's do raw query here for simplicity or assume helper exists.
            # We will use raw sqlite3 here to be self-contained or import if we add it to database.py
            # Let's use raw query to avoid touching database.py too much if we can.
            
            # Actually, better to keep DB logic in database.py? 
            # Let's write the query here.
            
            conn = sqlite3.connect("subscribers.db")
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            
            # Query: Completed Deep Research, No Batch ID assigned
            # Pick top 4 by AI Score (Quality first)
            query = """
                SELECT * FROM decision_points 
                WHERE deep_research_verdict IS NOT NULL 
                AND deep_research_verdict != '' 
                AND deep_research_verdict != '-'
                AND (batch_id IS NULL OR batch_id = '')
                ORDER BY ai_score DESC
                LIMIT 4
            """
            cursor.execute(query)
            rows = cursor.fetchall()
            conn.close()
            
            candidates = [dict(row) for row in rows]
            
            if len(candidates) >= 4:
                logger.info(f"[Deep Research] Found {len(candidates)} unbatched candidates. Triggering Batch Comparison...")
                
                symbols = [c['symbol'] for c in candidates]
                date_str = datetime.now().strftime("%Y-%m-%d")
                
                # 2. Log Batch Run & Get ID
                # We need to update these rows with the new batch_id.
                # log_batch_run usually creates a new batch entry. 
                # We should also update the decision_points rows to link them.
                
                # Creating a new batch entry
                # We reuse log_batch_run but we need to update the decision_points too.
                # Let's do it manually here to ensure atomicity or proper linking.
                
                conn = sqlite3.connect("subscribers.db")
                cursor = conn.cursor()
                
                # Create Batch Record (in batch_comparisons table)
                cursor.execute("INSERT INTO batch_comparisons (candidates, status, timestamp) VALUES (?, ?, ?)", 
                              (json.dumps(symbols), 'PENDING', datetime.now()))
                batch_id = cursor.lastrowid
                
                # Update Decision Points with this batch_id
                placeholders = ','.join(['?'] * len(symbols))
                update_query = f"UPDATE decision_points SET batch_id = ? WHERE symbol IN ({placeholders}) AND date(timestamp) = date('now')" 
                # Note: The candidate rows might be from different days if backlog? User said "backfill the ones from the same day".
                # But queue priority logic implies we process backlog. 
                # If we want to strictly batch same-day, we can filter by date in the select query.
                # User request: "If this is empty, then the batch comparison report is run. This takes 4 stocks... and names the best... logic is that once 4 evaluations... a batch comparison is run."
                # Doesn't explicitly say "same day" for the batch trigger, but likely implied. 
                # However, usually we batch what we have. 
                # Let's stick to the specific candidates we selected (by ID is safer).
                
                candidate_ids = [c['id'] for c in candidates]
                ids_placeholders = ','.join(['?'] * len(candidate_ids))
                
                cursor.execute(f"UPDATE decision_points SET batch_id = ? WHERE id IN ({ids_placeholders})", (batch_id, *candidate_ids))
                
                conn.commit()
                conn.close()
                
                logger.info(f"[Deep Research] Created Batch {batch_id} with candidates: {symbols}")
                
                 # 3. Queue Batch Task
                self.queue_batch_comparison_task(candidates, batch_id)
            else:
                logger.info(f"[Deep Research] Checked for batch trigger. Found {len(candidates)}/4 candidates. Waiting for more.")
                
        except Exception as e:
            logger.error(f"[Deep Research] Error checking/triggering batch: {e}")

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

    def execute_deep_research(self, symbol, raw_news, technical_data, drop_percent, transcript_text, transcript_date=None, transcript_warning=None, market_sentiment_report=None, competitive_report=None, summary_report=None, decision_id=None) -> Optional[Dict]:
        """
        The synchronous execution logic.
        """
        # 1. Construct the Prompt
        prompt = self._construct_prompt(symbol, raw_news, technical_data, drop_percent, transcript_text, transcript_date, transcript_warning, market_sentiment_report, competitive_report, summary_report)
        
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

    def _construct_prompt(self, symbol, raw_news, technical_data, drop_percent, transcript_text="", transcript_date=None, transcript_warning=None, market_sentiment_report=None, competitive_report=None, summary_report=None) -> str:
        # Format News List
        news_str = ""
        if raw_news:
            for n in raw_news[:15]: 
                date = n.get('datetime_str', 'N/A')
                source = n.get('source', 'Unknown')
                headline = n.get('headline', 'No Headline')
                news_str += f"- {date} [{source}]: {headline}\n"

        # Format Technical Data
        tech_str = json.dumps(technical_data, indent=2) if technical_data else "No specific technical data available."
        
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

        # Context Section (News/Tech OR Summary Report)
        context_section = ""
        if summary_report:
            context_section = f"""
You receive a consolidated preliminary analysis report from other agents (News, Technical, Sentiment):
{summary_report}
"""
        else:
             context_section = f"""
You receive a recent news summary of the stock:
{news_str}

You receive technical data on the stock:
{tech_str}

You receive a Market Sentiment Report (from a dedicated agent):
{market_sentiment_report or "No Sentiment Report Available."}

You receive a Competitive Landscape Report (from a dedicated agent):
{competitive_report or "No Competitive Report Available."}
"""

        return f"""
You are a Senior Market Analyst specializing in event-driven equities. Your goal is to determine if this drop is a temporary overreaction (Buy) or the start of a structural decline (Trap).

As you see in the report stock {symbol} dropped {drop_percent:.2f}% today.

{context_section}

{transcript_section}

> **Philosophical Context (The "Tomorrow's News" Paradox):**
> Remember the lesson of the Elm Partners study: Even traders with tomorrow's news often fail because they misjudge what is *already priced in*.
> - **Markets Anticipate:** A strong earnings report might cause a drop if the market expected *perfect* earnings.
> - **Size Matters:** Overconfidence kills. Do not recommend "STRONG_BUY" unless the edge is asymmetric and clear.
> - **Skepticism:** If the news is obvious (e.g., "Profits up"), assume the market knows. Look for the *reaction* to the news, not just the news itself.
> - **Humility:** Acknowledge unknowns. If the "Why" is murky, the risk is higher.

> **Directives:**
> 1. **Identify the Catalyst:** Using the provided analysis, identify the single most probable cause for the current price action. Explicitly state if there is NO clear news (a "Silent Mover").
> 2. **Global & Local Market Context:** Analyze the broader market conditions. Is the selling pressure specific to this stock, its sector, or the entire market? Consider global macro factors and local market sentiment.
> 3. **SWOT Analysis:** Perform a concise Strength, Weakness, Opportunity, and Threat analysis based on the provided data.
> 4. **Verify the Magnitude:** Apply the "Tomorrow's News" skepticism. Does the severity of the news (e.g., "Earnings down 50%") truly justify the drop, or was it priced in? If news is minor but reaction is massive, flag as "Speculative Sell".
> 5. **Technical Cross-Check:** Reference the TradingView RSI and MACD (if available in the report). Is the technical reaction proportionate to the fundamental news?
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
                # Individual Stock Schema
                schema_def = """
{
  "verdict": "[STRONG_BUY | SPECULATIVE_BUY | WAIT_FOR_STABILIZATION | HARD_AVOID]",
  "risk_level": "[Low/Medium/Extreme]",
  "catalyst_type": "[Structural/Temporary/Noise]",
  "global_market_analysis": "Brief analysis string",
  "local_market_analysis": "Brief analysis string",
  "swot_analysis": {
    "strengths": ["list", "of", "strings"],
    "weaknesses": ["list", "of", "strings"],
    "opportunities": ["list", "of", "strings"],
    "threats": ["list", "of", "strings"]
  },
  "reasoning_bullet_points": [ "list", "of", "strings" ],
  "knife_catch_warning": "True/False"
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
                report = self._load_council_report(sym, date_str)
                if report:
                    context_data += f"\n--- SUPPLEMENTARY REPORT FOR {sym} ---\n{report}\n"

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
                    if outputs:
                        print(f"DEBUG: Poll Data Outputs Found: {len(outputs)} items")
                    else:
                        print(f"DEBUG: NO OUTPUTS in Poll Data. Keys available: {list(poll_data.keys())}")
                        print(f"DEBUG: Full Poll Data: {json.dumps(poll_data, indent=2)}")
                    
                    
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

deep_research_service = DeepResearchService()
