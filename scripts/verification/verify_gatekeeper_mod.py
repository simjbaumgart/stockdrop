import sys
import os
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '../../')))

from app.services.gatekeeper_service import gatekeeper_service
from unittest.mock import MagicMock
import pandas as pd
import numpy as np

def test_gatekeeper_mod():
    print("--- Testing Gatekeeper Modifications ---")
    
    # 1. Test Market Regime (should be silent or cached)
    print("Testing Market Regime (expect no output if cached/suppressed)...")
    regime = gatekeeper_service.check_market_regime()
    print(f"Regime: {regime['regime']}")

    # 2. Test Technical Filters
    print("\nTesting Technical Filters (2.25 SD Logic)...")
    
    # Mock yfinance history
    dates = pd.date_range(end=pd.Timestamp.now(), periods=100)
    
    # Generate noisy data around 100
    np.random.seed(42)
    noise = np.random.normal(0, 1.0, 99) # Std dev of 1.0
    close_prices_base = 100.0 + noise
    
    # Scenario 1: Huge Drop (Should Pass)
    # Drop to 90 (from ~100). With std dev ~1, this is ~10 sigma!
    close_prices_1 = np.append(close_prices_base, 90.0)
    
    data_1 = pd.DataFrame({
        'Close': close_prices_1,
        'Volume': [10000] * 100
    }, index=dates)
    
    # Mock Ticker
    mock_ticker = MagicMock()
    mock_ticker.history.return_value = data_1
    
    # Patch yf.Ticker
    import yfinance as yf
    original_ticker = yf.Ticker
    yf.Ticker = MagicMock(return_value=mock_ticker)
    
    try:
        # Run check
        is_valid, reasons = gatekeeper_service.check_technical_filters("TEST_DROP")
        print(f"Drop Scenario (Price 90, Mean ~100, SD ~1): Valid? {is_valid}")
        print(f"Reasons: {reasons}")
        
        if is_valid:
             print("SUCCESS: Drop detected correctly.")
        else:
             print("FAILURE: Drop not detected.")

        # Scenario 2: Mild Drop (Should Fail)
        # Drop to 98.5. With std dev ~1, this is ~1.5 sigma. 
        # 2.25 SD threshold means it should NOT trigger.
        close_prices_2 = np.append(close_prices_base, 98.5)
        data_2 = pd.DataFrame({
            'Close': close_prices_2,
            'Volume': [10000] * 100
        }, index=dates)
        mock_ticker.history.return_value = data_2
        
        is_valid_2, reasons_2 = gatekeeper_service.check_technical_filters("TEST_MILD_DROP")
        print(f"\nMild Drop Scenario (Price 98, Mean ~100, SD ~1): Valid? {is_valid_2}")
        print(f"Reasons: {reasons_2}")
        
        if not is_valid_2:
             print("SUCCESS: Mild drop rejected.")
        else:
             print("FAILURE: Mild drop accepted incorrectly.")

    finally:
        # Restore
        yf.Ticker = original_ticker

if __name__ == "__main__":
    test_gatekeeper_mod()
