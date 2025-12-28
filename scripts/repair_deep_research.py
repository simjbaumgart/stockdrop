import os
import sys
import json
import logging
import sqlite3
from glob import glob

# Add project root to path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from app.services.deep_research_service import DeepResearchService
from app.database import update_deep_research_data, DB_NAME

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Load .env manually
try:
    env_path = os.path.join(os.path.dirname(__file__), '..', '.env')
    if os.path.exists(env_path):
        with open(env_path) as f:
            for line in f:
                if line.strip() and not line.startswith('#'):
                    parts = line.strip().split('=', 1)
                    if len(parts) == 2:
                        os.environ[parts[0]] = parts[1].strip('"').strip("'")
except Exception as e:
    logger.error(f"Error loading .env: {e}")

def get_decision_id_for_symbol(symbol):
    """
    Finds the decision_id for a symbol that has an ERROR_PARSING status.
    """
    try:
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        # Find the most recent decision for this symbol giving an error
        cursor.execute('''
            SELECT id FROM decision_points 
            WHERE symbol = ? AND deep_research_verdict IN ('ERROR_PARSING', 'UNKNOWN (Parse Error)')
            ORDER BY id DESC LIMIT 1
        ''', (symbol,))
        row = cursor.fetchone()
        conn.close()
        return row[0] if row else None
    except Exception as e:
        logger.error(f"Error finding decision_id for {symbol}: {e}")
        return None

def repair_reports():
    service = DeepResearchService()
    if not service.api_key:
        logger.error("No GEMINI_API_KEY found. Cannot run repair.")
        return

    data_dir = os.path.join(os.path.dirname(__file__), '..', 'data', 'deep_research_reports')
    if not os.path.exists(data_dir):
        logger.error(f"Directory not found: {data_dir}")
        return

    json_files = glob(os.path.join(data_dir, "*.json"))
    logger.info(f"Found {len(json_files)} reports to check.")

    repaired_count = 0

    for filepath in json_files:
        try:
            with open(filepath, 'r') as f:
                data = json.load(f)

            symbol = None
            # Extract symbol from filename (deep_research_SYMBOL_TIMESTAMP.json)
            filename = os.path.basename(filepath)
            parts = filename.split('_')
            if len(parts) >= 3:
                symbol = parts[2]
            
            if data.get('verdict') in ["ERROR_PARSING", "UNKNOWN (Parse Error)"]:
                logger.info(f"check: Found corrupted report for {symbol} ({filename})")
                
                raw_text = data.get('raw_report_full')
                if not raw_text:
                    logger.warning(f"  No 'raw_report_full' found in {filename}. Skipping.")
                    continue

                logger.info(f"  Attempting repair for {symbol}...")
                repaired_data = service._repair_json_using_flash(raw_text)

                if repaired_data:
                    logger.info(f"  Success! Repaired JSON for {symbol}.")
                    
                    # 1. Update File
                    # Preserve original metadata if needed, but usually repair is raw only
                    # We might want to keep the raw_report_full
                    repaired_data['raw_report_full'] = raw_text
                    
                    with open(filepath, 'w') as f:
                        json.dump(repaired_data, f, indent=2)
                    logger.info(f"  Updated JSON file: {filepath}")

                    # 2. Update PDF
                    filename_base = filename.replace(".json", "")
                    service._save_result_to_pdf(symbol, repaired_data, filename_base)
                    
                    # 3. Update Database
                    decision_id = get_decision_id_for_symbol(symbol)
                    if decision_id:
                        logger.info(f"  Updating Database for decision_id {decision_id}...")
                        
                        score_map = {
                            "STRONG_BUY": 90,
                            "SPECULATIVE_BUY": 75,
                            "WAIT_FOR_STABILIZATION": 50,
                            "HARD_AVOID": 10
                        }
                        score = score_map.get(repaired_data.get('verdict'), 0)
                        
                        success = update_deep_research_data(
                            decision_id=decision_id,
                            verdict=repaired_data.get('verdict'),
                            risk=repaired_data.get('risk_level', 'Unknown'),
                            catalyst=repaired_data.get('catalyst_type', 'Unknown'),
                            knife_catch=str(repaired_data.get('knife_catch_warning', 'False')),
                            score=score,
                            swot=json.dumps(repaired_data.get('swot_analysis', {})),
                            global_analysis=repaired_data.get('global_market_analysis', ''),
                            local_analysis=repaired_data.get('local_market_analysis', '')
                        )
                        if success:
                            logger.info("  Database updated.")
                        else:
                            logger.error("  Failed to update database.")
                    else:
                         logger.warning(f"  Could not find decision_id for {symbol} in DB (or valid error status).")
                    
                    repaired_count += 1
                else:
                    logger.error(f"  Repair Failed for {symbol}.")

        except Exception as e:
            logger.error(f"Error processing {filepath}: {e}")

    logger.info(f" Repair Complete. Total repaired: {repaired_count}")

if __name__ == "__main__":
    repair_reports()
