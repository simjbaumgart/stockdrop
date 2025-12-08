# StockDrop

**StockDrop** is an intelligent market monitoring system designed to identify "buy the dip" opportunities in large-cap global stocks. It automates the entire investment research process—from discovery to deep-dive analysis—using a multi-agent AI architecture.

## Core Workflow

### 1. Global Market Scanning
*   **What it does:** Every 2 hours (and on startup), the system scans major global markets (US, UK, Europe, China, India, Australia).
*   **Criteria:** It looks for "Large Cap" companies (>$5B USD) that have dropped significantly (more than **-5%**) in the last 24 hours.
*   **Technology:** Uses `TradingView` screener for real-time data.

### 2. The "Council of Agents" (AI Analysis)
Once a stock is identified, it is passed to a team of 4 specialized AI agents (powered by Google Gemini) that debate the investment case:

*   **🕵️ The Analyst (Senior Financial Analyst):** A skeptic who provides a neutral, data-driven assessment. Scrapes news and financial data to explain *why* the drop happened (e.g., earnings miss, regulatory fine, sector rotation).
*   **🐂 The Bull (Value Investor):** Takes the Analyst's data and constructs the strongest possible argument for *buying* the stock, focusing on overreactions and long-term catalysts.
*   **🐻 The Bear (Forensic Accountant):** Constructs the strongest argument for *avoiding* the stock, looking for structural problems, accounting red flags, and value traps.
*   **⚖️ The Synthesizer (Chief Investment Officer):** Reads all three reports, weighs the risks vs. rewards with a focus on capital preservation, and issues a final **Score (0-10)** along with an Executive Summary.
    *   **0:** Do not invest
    *   **5:** Neutral/Hold
    *   **10:** High Conviction Buy

### 3. Reporting & Storage
*   **Database:** All decisions are logged in a local database (`decision_points`) to track performance over time.
*   **CSV Records:** Daily summaries are saved to `data/decisions/` and raw scan results to `data/found_stocks/`.
*   **PDF Deep Dives:** A comprehensive PDF report is generated for every analyzed stock, containing the full output from all 4 agents. These are saved in the `reports/` folder.

### 4. Notifications
*   The system can send email alerts containing the decision and the attached PDF report immediately after analysis.

## Goal
To filter out market noise and provide high-quality, reasoned second opinions on volatile stocks, preventing emotional trading decisions.

## Inspiration & References

This project draws inspiration from:
*   **Tauric Research**: For agentic research methodologies.
*   **BA2TradePlatform**: For platform architecture concepts.
*   **Academic Research**: Concepts aligned with recent advancements in multi-agent financial analysis (e.g., [arXiv:2412.20138](https://arxiv.org/pdf/2412.20138)).

## Disclaimer

**THIS SOFTWARE IS FOR EDUCATIONAL AND RESEARCH PURPOSES ONLY.**

It is NOT financial advice, and should not be used as such. The "decisions" made by the AI agents are simulations based on historical and real-time data analysis. Trading stocks, especially attempting to "buy the dip," involves significant risk of capital loss.

## 🚀 Getting Started

Follow these instructions to set up the project on your local machine.

### Prerequisites

- **Python 3.9+** installed.
- **Git** installed.

### Installation

1.  **Clone the repository:**
    ```bash
    git clone <repository_url>
    cd Stock-Tracker
    ```

2.  **Create and activate a virtual environment:**
    ```bash
    # MacOS/Linux
    python3 -m venv venv
    source venv/bin/activate
    
    # Windows
    # python -m venv venv
    # .\venv\Scripts\activate
    ```

3.  **Install dependencies:**
    ```bash
    pip install -r requirements.txt
    ```

### Configuration

1.  Create a `.env` file in the root directory.
2.  Add the following environment variables (get API keys from their respective providers):

    ```env
    # --- Required ---
    GEMINI_API_KEY=your_google_gemini_api_key
    
    # --- Optional (for full functionality) ---
    ALPACA_API_KEY=your_alpaca_api_key
    ALPACA_SECRET_KEY=your_alpaca_secret_key
    FINNHUB_API_KEY=your_finnhub_api_key
    ALPHA_VANTAGE_API_KEY=your_alpha_vantage_api_key
    
    # --- Email Alerts (Optional) ---
    SENDER_EMAIL=your_email@gmail.com
    SENDER_PASSWORD=your_app_password
    RECIPIENT_EMAIL=target_email@example.com
    SMTP_SERVER=smtp.gmail.com
    SMTP_PORT=587
    ```

### Running the Application

1.  **Start the server:**
    ```bash
    uvicorn main:app --reload
    ```
    
2.  **Access the Dashboard:**
    Open your browser and navigate to `http://localhost:8000`.

3.  **Enable Email Notifications (Optional):**
    To run with email alerts enabled:
    ```bash
    python main.py --enable-email
    ```
