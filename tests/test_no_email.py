from app.services.email_service import email_service
from unittest.mock import patch, MagicMock

def test_no_email_flag():
    print("Testing no-email flag logic...")
    
    # 1. Disable email service
    email_service.enabled = False
    
    # 2. Mock SMTP
    with patch('smtplib.SMTP') as mock_smtp:
        # 3. Call send_notification
        email_service.send_notification(
            symbol="TEST", 
            percentage=-10.0, 
            price=100.0, 
            report_data={"recommendation": "BUY", "executive_summary": "Test"},
            market_context={}
        )
        
        # 4. Verify no SMTP connection was made
        if mock_smtp.called:
            print("FAIL: SMTP was called despite enabled=False")
        else:
            print("PASS: SMTP was NOT called when enabled=False")
            
    # 5. Re-enable and verify it would call (optional, but good sanity check)
    email_service.enabled = True
    # We need to mock env vars or ensure it doesn't actually send if creds are missing
    # But for this test, we just want to prove the flag works.
    
if __name__ == "__main__":
    test_no_email_flag()
