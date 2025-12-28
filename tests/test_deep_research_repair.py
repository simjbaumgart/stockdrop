
import sys
import os
import json
import logging

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
    print(f"Error loading .env: {e}")

# Add app to path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from app.services.deep_research_service import DeepResearchService

# Configure logging
logging.basicConfig(level=logging.INFO)

def test_repair():
    print("Testing Deep Research Repair Mechanism...")
    
    # Mock raw text (simulating the failure case found in 0REH)
    # This matches the structure "{'text': 'MARKDOWN CONTENT', 'type': 'text'}"
    # I'll include a snippet of the markdown found in the file.
    
    raw_markdown = """
# Event-Driven Equity Analysis: Frontline Plc (0REH)

## Executive Summary: The "Red Sea" Premium Under Siege
**Date:** December 19, 2025
**Verdict:** WAIT_FOR_STABILIZATION

The sharp decline in Frontline Plc (0REH) represents a classic **event-driven repricing**.

## 1. Catalyst Identification
The primary catalyst is the **successful transit of a Maersk vessel**.

## 6. SWOT Analysis Summary
| **Strengths** | **Weaknesses** |
| :--- | :--- |
| - Young fleet | - High debt |

## 7. Final Verdict & Recommendation
**Verdict:** **WAIT_FOR_STABILIZATION**
**Risk Level:** Extreme
**Knife Catch Warning:** True
"""

    # Simulate the raw output format from the interaction API
    # output = {'text': raw_markdown, 'type': 'text'}
    # The _parse_output logic would pass 'raw_markdown' to the repair function if parsing failed.
    # Note: validation of _parse_output logic:
    # It extracts `text` from `output`. So it passes `raw_markdown` to `_repair_json_using_flash`.
    
    service = DeepResearchService()
    
    if not service.api_key:
        print("Skipping test: No API Key found.")
        return

    print("Sending Markdown to Flash for Repair...")
    repaired_json = service._repair_json_using_flash(raw_markdown)
    
    if repaired_json:
        print("\nSUCCESS! Repaired JSON:")
        print(json.dumps(repaired_json, indent=2))
        
        # Verify fields
        assert repaired_json.get('verdict') == "WAIT_FOR_STABILIZATION"
        assert "Young fleet" in str(repaired_json.get('swot_analysis'))
        print("\nVerification Passed.")
    else:
        print("\nFAILED: Could not repair JSON.")

if __name__ == "__main__":
    test_repair()
