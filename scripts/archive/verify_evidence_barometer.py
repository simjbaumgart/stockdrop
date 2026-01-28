import sys
import os
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from app.services.evidence_service import evidence_service
import json

def test_evidence_barometer():
    print("Testing Evidence Barometer...")
    
    # Mock Data
    raw_data = {
        "ticker": "AAPL",
        "news_items": [
            {"provider": "Benzinga", "headline": "News 1", "content": "Short content", "datetime_str": "2024-01-01"},
            {"source": "Reuters", "headline": "News 2", "content": "Longer content " * 10, "datetime_str": "2024-01-02"},
            {"provider": "Benzinga", "headline": "News 3", "content": "Another one", "datetime_str": "2024-01-03"}
        ],
        "transcript_text": "This is a transcript text > 100 chars. " * 5,
        "transcript_date": "2023-12-01"
    }
    
    agent_reports = {
        "technical": "Tech report content",
        "bull": "Bull report content",
        "bear": ""
    }
    
    # Execute
    result = evidence_service.collect_barometer(raw_data, agent_reports)
    
    # Verify
    print("\nResult:")
    print(json.dumps(result, indent=2))
    
    # Assertions
    assert result['news']['total_count'] == 3
    assert result['news']['providers']['Benzinga'] == 2
    assert result['news']['providers']['Reuters'] == 1
    assert result['fundamentals']['transcript_available'] == True
    assert result['agents']['technical_length'] > 0
    assert result['agents']['bear_length'] == 0
    
    print("\n✅ Verification Passed!")

if __name__ == "__main__":
    test_evidence_barometer()
