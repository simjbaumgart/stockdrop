import smtplib
import os
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.application import MIMEApplication
from fpdf import FPDF
from datetime import datetime
from app.database import get_all_subscribers

class EmailService:
    def __init__(self):
        self.smtp_server = os.getenv("SMTP_SERVER", "smtp.gmail.com")
        self.smtp_port = int(os.getenv("SMTP_PORT", "587"))
        self.sender_email = os.getenv("SENDER_EMAIL")
        self.sender_password = os.getenv("SENDER_PASSWORD")
        self.recipient_email = os.getenv("RECIPIENT_EMAIL") # Uncommented and used in new logic
        self.enabled = False

    def send_notification(self, symbol: str, percentage: float, price: float, report_data: dict, market_context: dict = None):
        # The original subscriber logic is replaced by the new snippet's recipient handling.
        # If you intended to keep the subscriber logic, this section would need to be merged carefully.
        # For now, following the provided snippet's logic.
        
        # Always generate PDF report first
        try:
            pdf_path = self._generate_pdf_report(symbol, report_data)
            print(f"Generated PDF report: {pdf_path}")
        except Exception as e:
            print(f"Error generating PDF: {e}")
            pdf_path = None

        if not self.enabled:
            print(f"Email notifications disabled. Skipping alert for {symbol}.")
            return

        if not self.sender_email or not self.sender_password or not self.recipient_email:
            print(f"Mock Email Sent: Alert! {symbol} has dropped {percentage:.2f}% to ${price:.2f}")
            return

        subject = f"Stock Alert: {symbol} dropped {percentage:.2f}%"
        
        # Format Market Context
        context_str = ""
        if market_context:
            context_str = "\n\nMarket Context:\n"
            for k, v in market_context.items():
                context_str += f"- {k}: {v:.2f}%\n"

        # Format Body
        recommendation = report_data.get("recommendation", "N/A")
        summary = report_data.get("executive_summary", "No summary provided.")
        
        body = (
            f"STOCK ALERT: {symbol}\n"
            f"Drop: {percentage:.2f}%\n"
            f"Current Price: ${price:.2f}\n"
            f"Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
            f"{context_str}\n"
            f"----------------------------------------\n"
            f"ANALYST DECISION: {recommendation}\n"
            f"----------------------------------------\n\n"
            f"EXECUTIVE SUMMARY:\n{summary}\n\n"
            f"----------------------------------------\n"
            f"Data provided by Yahoo Finance & Gemini 3\n"
            f"See attached PDF for full deep-dive report."
        )

        msg = MIMEMultipart()
        msg['From'] = self.sender_email
        # msg['To'] will be set in the loop
        msg['Subject'] = subject
        msg.attach(MIMEText(body, 'plain'))
        # Attach PDF if generated
        if pdf_path and os.path.exists(pdf_path):
            try:
                with open(pdf_path, "rb") as f:
                    part = MIMEApplication(f.read(), Name=os.path.basename(pdf_path))
                part['Content-Disposition'] = f'attachment; filename="{os.path.basename(pdf_path)}"'
                msg.attach(part)
            except Exception as e:
                print(f"Error attaching PDF: {e}")

        # Send to list of recipients (or single for now)
        recipients = [self.recipient_email]
        # TODO: Fetch from DB
        
        try:
            with smtplib.SMTP(self.smtp_server, self.smtp_port) as server:
                server.starttls()
                server.login(self.sender_email, self.sender_password)
                for recipient in recipients:
                    msg['To'] = recipient
                    server.send_message(msg)
            print(f"Email sent to {recipients} for {symbol}")
        except Exception as e:
            print(f"Failed to send email: {e}")

    def send_daily_summary(self, movers: list):
        if not self.enabled:
            print("Email notifications disabled. Skipping daily summary.")
            return

        subscribers = get_all_subscribers()
        
        # Fallback to env var if DB is empty (for testing/legacy)
        env_recipient = os.getenv("RECIPIENT_EMAIL")
        if not subscribers and env_recipient:
            subscribers = [env_recipient]

        if not self.sender_email or not self.sender_password or not subscribers:
            print(f"Mock Daily Summary Sent: {len(movers)} movers found.")
            return

        subject = f"Daily Market Summary: {len(movers)} Major Movers"
        
        # Build HTML body
        body = "<h2>Daily Market Summary</h2>"
        body += "<p>Here are the major stocks that moved more than 5% today:</p>"
        
        if movers:
            body += "<table border='1' cellpadding='5' style='border-collapse: collapse;'>"
            body += "<tr><th>Symbol</th><th>Price</th><th>Change %</th><th>Sector</th></tr>"
            for mover in movers:
                color = "green" if mover["change_percent"] > 0 else "red"
                body += f"<tr>"
                body += f"<td><b>{mover['symbol']}</b></td>"
                body += f"<td>${mover['price']:.2f}</td>"
                body += f"<td style='color: {color};'>{mover['change_percent']:.2f}%</td>"
                body += f"<td>{mover['sector']}</td>"
                body += "</tr>"
            body += "</table>"
        else:
            body += "<p>No major movements (>5%) detected today.</p>"

        msg = MIMEMultipart()
        msg['From'] = self.sender_email
        msg['Subject'] = subject
        msg.attach(MIMEText(body, 'html'))

        try:
            server = smtplib.SMTP(self.smtp_server, self.smtp_port)
            server.starttls()
            server.login(self.sender_email, self.sender_password)
            
            for recipient in subscribers:
                msg['To'] = recipient
                text = msg.as_string()
                server.sendmail(self.sender_email, recipient, text)
                print(f"Daily summary sent to {recipient}")
                
            server.quit()
        except Exception as e:
            print(f"Failed to send daily summary: {e}")

    def _generate_pdf_report(self, symbol: str, report_data: dict) -> str:
        pdf = FPDF()
        pdf.add_page()
        
        # Title
        pdf.set_font("Arial", 'B', 16)
        pdf.cell(0, 10, f"Deep Dive Research Report: {symbol}", 0, 1, 'C')
        pdf.ln(10)
        
        # Recommendation
        pdf.set_font("Arial", 'B', 12)
        pdf.cell(0, 10, f"Recommendation: {report_data.get('recommendation', 'N/A')}", 0, 1)
        pdf.ln(5)
        
        # Detailed Report
        pdf.set_font("Arial", '', 11)
        detailed_text = report_data.get("detailed_report", "No details provided.")
        
        # FPDF multi_cell handles text wrapping
        # Encode to latin-1 to avoid unicode errors in standard FPDF, or replace chars
        # For simplicity, we'll just replace common issues
        safe_text = detailed_text.encode('latin-1', 'replace').decode('latin-1')
        pdf.multi_cell(0, 10, safe_text)
        
        # --- Append Intermediate Reports ---
        
        # Technician's Report
        if "technician_report" in report_data:
            pdf.add_page()
            pdf.set_font("Arial", 'B', 14)
            pdf.set_text_color(0, 0, 150) # Dark Blue
            pdf.cell(0, 10, "Technician's Report (Momentum & Levels)", 0, 1)
            pdf.set_text_color(0, 0, 0) # Reset
            pdf.ln(5)
            pdf.set_font("Arial", '', 10)
            text = report_data["technician_report"].encode('latin-1', 'replace').decode('latin-1')
            pdf.multi_cell(0, 6, text)

        # Macro Context
        if "macro_report" in report_data:
            pdf.add_page()
            pdf.set_font("Arial", 'B', 14)
            pdf.set_text_color(100, 100, 0) # Dark Yellow/Gold
            pdf.cell(0, 10, "Macro Context (Sector & Factors)", 0, 1)
            pdf.set_text_color(0, 0, 0) # Reset
            pdf.ln(5)
            pdf.set_font("Arial", '', 10)
            text = report_data["macro_report"].encode('latin-1', 'replace').decode('latin-1')
            pdf.multi_cell(0, 6, text)

        # Bear's Pre-Mortem
        if "bear_report" in report_data:
            pdf.add_page()
            pdf.set_font("Arial", 'B', 14)
            pdf.set_text_color(150, 0, 0) # Dark Red
            pdf.cell(0, 10, "The Bear's Pre-Mortem (Downside Risks)", 0, 1)
            pdf.set_text_color(0, 0, 0) # Reset
            pdf.ln(5)
            pdf.set_font("Arial", '', 10)
            text = report_data["bear_report"].encode('latin-1', 'replace').decode('latin-1')
            pdf.multi_cell(0, 6, text)
        
        # Save to reports folder
        reports_dir = "data/reports"
        if not os.path.exists(reports_dir):
            os.makedirs(reports_dir)
            
        filename = f"report_{symbol}_{datetime.now().strftime('%Y%m%d')}.pdf"
        filepath = os.path.join(reports_dir, filename)
        pdf.output(filepath)
        return filepath

email_service = EmailService()
