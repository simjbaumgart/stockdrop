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
