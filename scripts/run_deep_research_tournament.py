
import sys
import os
import json
import sqlite3
import time
from datetime import datetime
from typing import List, Dict, Any
from dotenv import load_dotenv

# Load env vars first
load_dotenv()

# Ensure app imports work
sys.path.append(os.getcwd())

from app.services.deep_research_service import deep_research_service

def get_todays_candidates() -> List[Dict]:
    """
    Fetches today's candidates with Score >= 70.
    """
    try:
        conn = sqlite3.connect("subscribers.db")
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        today_str = datetime.now().strftime("%Y-%m-%d")
        
        # Query: Score >= 70, from Today
        query = """
            SELECT * FROM decision_points 
            WHERE date(timestamp) = ? 
            AND ai_score >= 70
        """
        cursor.execute(query, (today_str,))
        rows = cursor.fetchall()
        conn.close()
        
        candidates = [dict(row) for row in rows]
        print(f"[Tournament] Found {len(candidates)} candidates for today ({today_str}).")
        
        # Filter duplicates (keep highest score)
        unique = {}
        for c in candidates:
            sym = c['symbol']
            if sym not in unique or c['ai_score'] > unique[sym]['ai_score']:
                unique[sym] = c
                
        final_list = list(unique.values())
        return sorted(final_list, key=lambda x: x['ai_score'], reverse=True)
        
    except Exception as e:
        print(f"[Tournament] Error fetching candidates: {e}")
        return []

def run_tournament(candidates: List[Dict], round_num: int = 1) -> Dict:
    """
    Recursive tournament execution.
    Chunk size = 4.
    """
    if not candidates:
        return None
        
    print(f"\n{'='*40}")
    print(f"🏟️  TOURNAMENT ROUND {round_num}")
    print(f"Candidates: {len(candidates)} -> {[c['symbol'] for c in candidates]}")
    print(f"{'='*40}\n")
    
    # Base Case: Single Winner
    if len(candidates) == 1:
        return candidates[0]
        
    winners = []
    
    # Chunking
    chunk_size = 4
    for i in range(0, len(candidates), chunk_size):
        chunk = candidates[i:i + chunk_size]
        
        # Handle leftover chunk (if < 4, merge with previous if possible or run as is)
        # User Instruction: "if the last chunk does not have 4, take the last stocks in one."
        # This implies we just run the chunk as is, even if smaller (e.g. 2 or 3).
        # But if it's 1 (e.g. 5 candidates -> 4 + 1), we can't really compare 1 against itself.
        # If chunk is size 1 and it's the last one, we could auto-promote it?
        # Or should we have added it to the previous chunk?
        # User said: "In chunks of 4... if the last chunk does not have 4, take the last stocks in one."
        # This suggests just processing the last group as a group.
        
        if len(chunk) == 1:
            print(f"  > Auto-promoting {chunk[0]['symbol']} (Only 1 in chunk)")
            winners.append(chunk[0])
            continue
            
        print(f"  > Running Batch {i//chunk_size + 1}: {[c['symbol'] for c in chunk]}")
        
        # Run Comparison
        try:
            # We must pass a batch_id logically, but here we can just pass None 
            # or create a dummy one if we want DB logging.
            # Let's pass None for now to keep it simple, or create one.
            # The service creates a batch ID if we don't pass one? No, it expects one or None.
            # If None, it won't update DB status.
            
            result = deep_research_service.execute_batch_comparison(chunk, batch_id=None)
            
            if result and 'winner_symbol' in result:
                winner_sym = result['winner_symbol']
                # Find candidate object
                winner_cand = next((c for c in chunk if c['symbol'] == winner_sym), None)
                if winner_cand:
                    print(f"  ✅ Winner: {winner_sym}")
                    winners.append(winner_cand)
                else:
                    print(f"  ⚠️ Winner {winner_sym} not found in chunk! Promoting first candidate as fallback.")
                    winners.append(chunk[0])
            else:
                 print("  ❌ Batch Failed. Promoting highest score as fallback.")
                 chunk.sort(key=lambda x: x['ai_score'], reverse=True)
                 winners.append(chunk[0])

            # Buffer Delay (Consecutive runs)
            # User requirement: "Run it consequitive... 1min buffer between every run."
            # Only sleep if there are more chunks or rounds (but we don't know rounds deep)
            # Just sleep here to be safe, unless it's the very last action.
            print("  ⏳ Waiting 60s for rate limit...")
            time.sleep(60)

        except Exception as e:
            print(f"  ❌ Error in batch execution: {e}")
            # Fallback
            winners.append(chunk[0])

    # Recursive Call
    return run_tournament(winners, round_num + 1)

def main():
    print("Starting Deep Research Tournament...")
    
    candidates = get_todays_candidates()
    
    if not candidates:
        print("No candidates found for today with Score >= 70.")
        return

    print(f"Loaded {len(candidates)} candidates.")
    
    overall_winner = run_tournament(candidates)
    
    if overall_winner:
        print(f"\n{'='*60}")
        print(f"🏆 STOCK OF THE DAY: {overall_winner['symbol']}")
        print(f"{'='*60}")
        print(f"Score: {overall_winner['ai_score']}")
        print(f"Recommendation: {overall_winner.get('recommendation', 'N/A')}")
        print(f"{'='*60}\n")
    else:
        print("Tournament finished without a clear winner.")

if __name__ == "__main__":
    main()
