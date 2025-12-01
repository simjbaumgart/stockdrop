import smtplib
import os
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from app.database import get_all_subscribers

class EmailService:
    def __init__(self):
        self.smtp_server = os.getenv("SMTP_SERVER", "smtp.gmail.com")
        self.smtp_port = int(os.getenv("SMTP_PORT", "587"))
        self.sender_email = os.getenv("SENDER_EMAIL")
        self.sender_password = os.getenv("SENDER_PASSWORD")
        # self.recipient_email = os.getenv("RECIPIENT_EMAIL") # No longer single recipient

    def send_notification(self, symbol: str, percentage: float, price: float, research_report: str = None):
        subscribers = get_all_subscribers()
        
        # Fallback to env var if DB is empty (for testing/legacy)
        env_recipient = os.getenv("RECIPIENT_EMAIL")
        if not subscribers and env_recipient:
            subscribers = [env_recipient]

        if not self.sender_email or not self.sender_password or not subscribers:
            print(f"Mock Email Sent: Alert! {symbol} has dropped {percentage:.2f}% to ${price:.2f}")
            if research_report:
                print(f"Research Report included: {research_report[:50]}...")
            return

        subject = f"Stock Alert: {symbol} dropped {percentage:.2f}%"
        body = f"Alert! {symbol} has dropped {percentage:.2f}% today.\nCurrent Price: ${price:.2f}\n\n"
        
        if research_report:
            body += f"--- Analyst Research Report ---\n{research_report}\n"

        msg = MIMEMultipart()
        msg['From'] = self.sender_email
        # msg['To'] will be set in the loop
        msg['Subject'] = subject
        msg.attach(MIMEText(body, 'plain'))

        try:
            server = smtplib.SMTP(self.smtp_server, self.smtp_port)
            server.starttls()
            server.login(self.sender_email, self.sender_password)
            
            # Send to each subscriber
            # Note: For production, use BCC or a mailing service (SendGrid, AWS SES)
            # to avoid sending N separate emails sequentially which is slow.
            # But for this MVP, sequential is fine.
            for recipient in subscribers:
                msg['To'] = recipient
                text = msg.as_string()
                server.sendmail(self.sender_email, recipient, text)
                print(f"Email sent to {recipient} for {symbol}")
                
            server.quit()
        except Exception as e:
            print(f"Failed to send email: {e}")

email_service = EmailService()
