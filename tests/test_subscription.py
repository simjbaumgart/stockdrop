import sys
import os
import sqlite3
from unittest.mock import patch

# Add project root to path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from app.database import init_db, add_subscriber, get_all_subscribers, DB_NAME
from app.services.email_service import email_service

def test_subscription_flow():
    print("Testing subscription flow...")
    
    # 1. Setup fresh DB
    if os.path.exists(DB_NAME):
        os.remove(DB_NAME)
    init_db()
    print("Database initialized.")

    # 2. Add subscribers
    email1 = "user1@example.com"
    email2 = "user2@example.com"
    
    assert add_subscriber(email1) == True
    assert add_subscriber(email2) == True
    assert add_subscriber(email1) == False # Duplicate
    print("Subscribers added.")

    # 3. Verify retrieval
    subscribers = get_all_subscribers()
    assert len(subscribers) == 2
    assert email1 in subscribers
    assert email2 in subscribers
    print("Subscribers retrieved correctly.")

    # 4. Verify EmailService uses subscribers
    # Mock SMTP to avoid real sending, but verify logic
    with patch('smtplib.SMTP') as mock_smtp:
        # Mock login/sendmail
        instance = mock_smtp.return_value
        
        # Set dummy credentials to bypass check
        email_service.sender_email = "dummy@example.com"
        email_service.sender_password = "dummy"
        
        email_service.send_notification("TEST", -10.0, 100.0)
        
        # Verify sendmail called for each subscriber
        assert instance.sendmail.call_count == 2
        
        # Check recipients
        calls = instance.sendmail.call_args_list
        recipients = [call[0][1] for call in calls]
        assert email1 in recipients
        assert email2 in recipients
        print("EmailService attempted to send to all subscribers.")

    # Cleanup
    if os.path.exists(DB_NAME):
        os.remove(DB_NAME)
    print("SUCCESS: Subscription flow verified.")

if __name__ == "__main__":
    test_subscription_flow()
