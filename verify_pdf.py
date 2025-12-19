from app.services.deep_research_service import DeepResearchService
import os

def test_pdf_generation():
    service = DeepResearchService()
    
    mock_result = {
        "verdict": "STRONG_BUY",
        "risk_level": "Medium",
        "catalyst_type": "Temporary Overreaction",
        "global_market_analysis": "Markets are fearful but fundamentals are strong.",
        "local_market_analysis": "Sector is recovering from a cyclical low.",
        "reasoning_bullet_points": [
            "Revenue grew 20% YoY despite macro headwinds.",
            "New product launch expects 50% margin accretion.",
            "Stock is trading at historical support levels."
        ],
        "swot_analysis": {
            "strengths": ["Strong Balance Sheet", "Market Leader"],
            "weaknesses": ["High exposure to Europe"],
            "opportunities": ["AI Integration", "M&A"],
            "threats": ["Regulatory changes"]
        }
    }
    
    print("Generating PDF...")
    service._save_result_to_file("TEST_STOCK", mock_result)
    print("Check data/deep_research_reports for the PDF.")

if __name__ == "__main__":
    test_pdf_generation()
