import os
import sys
import json
import logging

# Add project root to path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from app.services.deep_research_service import DeepResearchService

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

def repair_specific_batch_file():
    service = DeepResearchService()
    if not service.api_key:
        logger.error("No GEMINI_API_KEY found. Cannot run repair.")
        return

    filepath = "data/comparisons/batch_comparison_2025-12-22_0RHE_DCC_DDS_ELPC.json"
    abs_path = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', filepath))
    
    if not os.path.exists(abs_path):
        logger.error(f"File not found: {abs_path}")
        # Try to find it in current dir just in case
        if os.path.exists(filepath):
             abs_path = os.path.abspath(filepath)
        else:
             return

    logger.info(f"Targeting file: {abs_path}")

    try:
        # Read the corrupted file
        # The file content provided by user shows:
        # [Deep Research] Comparison Complete. (Could not parse JSON output)
        # { ... }
        # Error: ...
        
        # We need to read it as text, not JSON, because it is likely corrupted or just raw text saved.
        with open(abs_path, 'r') as f:
            content = f.read()

        logger.info(f"Read {len(content)} bytes.")

        # Extract the JSON-like part or just pass the whole thing to the repair agent
        # The content has a "raw_report_full" field with the text inside? 
        # Or is the file itself a valid JSON but with a generic request error?
        # User provided:
        # """
        # [Deep Research] Comparison Complete. (Could not parse JSON output)
        # {
        #   "verdict": "STRONG_BUY",
        #   ...
        # }
        # """
        # And then `raw_report_full`: "{'text': ...}"
        
        # If the file IS parsable JSON but has "verdict": "ERROR_PARSING" or similar, we can reuse logic.
        # But if the file content literally starts with "[Deep Research]...", it's not valid JSON.
        
        # Step 1: Try to parse as JSON
        data = None
        try:
            data = json.loads(content)
        except json.JSONDecodeError:
            logger.info("File content is NOT valid JSON. Treating as raw text.")
        
        text_to_repair = ""
        if data:
            # It is valid JSON. Check if it's an error report.
            # The 'raw_report_full' field is usually where the gold is.
            text_to_repair = data.get('raw_report_full', '')
            if not text_to_repair:
                # If no raw report, maybe the whole JSON is the data but with wrong keys?
                text_to_repair = json.dumps(data)
        else:
            text_to_repair = content

        if not text_to_repair:
             logger.error("No text to repair found.")
             return

        logger.info("Attempting repair with schema_type='batch'...")
        repaired_data = service._repair_json_using_flash(text_to_repair, schema_type='batch')

        if repaired_data:
            logger.info("Repair Successful!")
            
            # Save properly
            with open(abs_path, 'w') as f:
                json.dump(repaired_data, f, indent=2)
            logger.info(f"Overwrote {abs_path} with repaired JSON.")
            
            # Generate PDF
            # We need symbols. filename: batch_comparison_2025-12-22_0RHE_DCC_DDS_ELPC.json
            filename = os.path.basename(abs_path)
            # Remove prefix and extension
            base = filename.replace("batch_comparison_", "").replace(".json", "")
            # Base is 2025-12-22_0RHE_DCC_DDS_ELPC
            parts = base.split('_')
            # Date is parts[0]
            symbols = parts[1:] #['0RHE', 'DCC', 'DDS', 'ELPC']
            
            pdf_path = abs_path.replace(".json", ".pdf")
            logger.info(f"Regenerating PDF at {pdf_path}...")
            service._save_batch_pdf(symbols, json.dumps(repaired_data), pdf_path)
            
            # Mark Winner in DB (if applicable)
            winner = repaired_data.get('winner_symbol')
            if winner:
                from app.database import mark_batch_winner
                date_str = parts[0]
                mark_batch_winner(winner, date_str)
                logger.info(f"Marked winner {winner} in DB.")
                
        else:
            logger.error("Repair Failed.")

    except Exception as e:
        logger.error(f"Error in repair script: {e}")

if __name__ == "__main__":
    repair_specific_batch_file()
