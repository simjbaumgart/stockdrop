# StockDrop 📉🚀

**StockDrop** Is an autonomous AI hedge fund analyst aimed at solving one specific problem: **Identifying "Buy the Dip" opportunities without the emotional baggage.**

It continuously scans global markets for significant price drops in large-cap companies and deploys a **Council of AI Agents** to debate the fundamental, technical, and macro rationale before issuing a trade recommendation.

---

## 🧠 The "AI Council" Architecture

Unlike simple "screeners" or single-prompt GPT wrappers, StockDrop uses a multi-stage, multi-agent architecture to simulate a real investment committee.

### Phase 1: The Sensors (Data Collection)
Once a stock is identified (dropping >5% in 24h), a team of specialized agents gathers intelligence:
*   **🕵️ News Agent:** Scans thousands of headlines (Benzinga, Reuters, Seeking Alpha) and reads Earnings Transcripts to answer: *Why is the stock down? Is it a structural issue or temporary panic?*
*   **📈 Technical Agent:** Analyzes price action, support levels, RSI, and trend integrity.
*   **🌍 Macro/Economics Agent:** Triggered automatically if the company has high exposure to the US economy, fetching real-time data from the **Federal Reserve (FRED)** (Interest Rates, CPI, GDP) to assess headwinds.
*   **⚔️ Competitive Landscape Agent:** Identifies peers and checks if the drop is company-specific or sector-wide.
*   **📰 Seeking Alpha Agent:** Digs into specialized investor analysis to find contrarian viewpoints.
*   **🧠 Market Sentiment Agent:** Uses Google Search Grounding to gauge the real-time "pulse" of the internet and social sentiment.

### Phase 2: The Debate (Thesis Construction)
Two distinct AI personas review the evidence from Phase 1 independent of each other:
*   **🐂 The Bull (Value Investor):** Constructs the strongest possible argument for **buying**, focusing on overreactions, value discrepancies, and misunderstood catalysts.
*   **🐻 The Bear (Forensic Accountant):** Constructs the strongest argument for **avoiding** the trade, highlighting risks, broken growth stories, and "value traps".

### Phase 3: The Verdict (Portfolio Manager)
*   **⚖️ The Portfolio Manager:** Acting as the final decision-maker, this agent reads both arguments and the raw evidence. It is instructed to be **risk-averse** and verify claims using its own internet search tools. It outputs a final **0-100 Score** and a decision:
    *   `STRONG BUY` / `BUY` / `HOLD` / `SELL` / `STRONG SELL`

### Phase 4: The Deep Research Validator (Secondary Gatekeeper)
Because true Deep Research is extremely token-heavy and time-consuming, the system does not deep-research every single dropping stock. Instead, it acts as a secondary gatekeeper. 

*   **The Filter:** Only stocks that receive an initial high score (`BUY` or `STRONG BUY`) from Phase 3's Portfolio Manager are forwarded to the **Gemini Deep Research Agent**.
*   **The Final Verification:** The Deep Research agent independently scours the internet, analyzes sprawling financial documents, and issues an ultimate **DR Verdict** to validate or overrule the initial Buy rating:
    *   `STRONG_BUY` / `SPECULATIVE_BUY`: The trade is deeply validated. (Historically our highest-performing cohort).
    *   `WAIT_FOR_STABILIZATION`: The agent likes the fundamentals, but detects a falling-knife scenario and advises patience.
    *   `AVOID` / `HARD_AVOID`: The Deep Research agent overrules the initial AI Council, identifying a value trap or fundamental flaw they missed entirely.

---

## 🌍 Global Market Coverage
StockDrop doesn't just watch the S&P 500. It monitors:
*   🇺🇸 **USA** (NYSE, NASDAQ)
*   🇪🇺 **Europe** (STOXX 600 components)
*   🇨🇳 **China** (HKSE, Shanghai - Large Caps)
*   🇮🇳 **India** (Nifty 50 components)

## 🔌 Data Integrations
We fuse reputable financial data with cutting-edge AI:
*   **Markets:** TradingView & Alpaca (Real-time Prices & Screener)
*   **Alternative Data:** DefeatBeta (Transcripts & Niche News)
*   **Economy:** Federal Reserve Economic Data (FRED)
*   **Analysis:** Seeking Alpha & Benzinga Pro
*   **AI Models:** Google Gemini 3.5 Flash (Reasoning Core) & Gemini Deep Research
*   **Search**: Google Search Grounding (Real-time fact-checking)

---

## 🚀 Getting Started

### Prerequisites
*   Python 3.10+
*   Google Cloud API Key (for Gemini)

### Installation

1.  **Clone the repository:**
    ```bash
    git clone https://github.com/your-repo/Stock-Tracker.git
    cd Stock-Tracker
    ```

2.  **Set up Virtual Environment:**
    ```bash
    python3 -m venv venv
    source venv/bin/activate
    ```

3.  **Install Dependencies:**
    ```bash
    pip install -r requirements.txt
    ```

4.  **Configuration (.env):**
    Create a `.env` file and add your keys:
    ```env
    GEMINI_API_KEY=your_key_here
    ALPACA_API_KEY=optional
    ALPACA_SECRET_KEY=optional
    ```

### Running the App
Start the autonomous loop (scans every 2 hours):
```bash
python main.py
```
Or run the web dashboard to view reports:
```bash
uvicorn main:app --reload
```
View the dashboard at `http://localhost:8000`.

---

## 📊 Performance Analysis & Strategy Insights

We continuously evaluate the **AI Council's Recommendations** combined with the **Deep Research Verdicts** by measuring both the theoretical Peak ROI and the Win Rate (>10% peak ROI baseline). 

### 1. Overall System Performance (2026 Readout)
The table below showcases the performance of the system's various confidence cohorts against the S&P 500 baseline across identical timelines.

| Recommendation | DR Verdict | N | Avg Date | Avg Max ROI | SP500 Max ROI | Win Rate (>10%) |
|---|---|---|---|---|---|---|
| **STRONG BUY** | **STRONG_BUY** | 1 | Feb 03 | **49.43%** | 1.09% | **100.0%** |
| **BUY** | **SPECULATIVE_BUY** | 19 | Jan 30 | **34.31%** | 1.13% | **73.7%** |
| **AVOID** | **AVOID** | 4 | Mar 10 | **20.82%** | 1.43% | **25.0%** |
| **BUY** | **WAIT_FOR_STABILIZATION** | 49 | Jan 29 | **18.98%** | 0.99% | **61.2%** |
| **SELL** | **None** | 37 | Jan 30 | **16.65%** | 1.00% | **48.6%** |

*(Note: Data is continuously updated. See `/reports` for deeper dives).*

### 2. The Cost of Being Cheap: BUY LIMIT vs Market Buy
We ran a deep temporal simulation on the 262 instances where the agent set a **Limit Price**. The results were stark: waiting for the dip costs us the massive runners.
- **Trigger Rate:** 86.3% filled (saving ~0.50% on average).
- **Opportunity Cost:** The 13.7% of trades that "ran away" without dropping to our limit price rocketed for an **Avg Peak ROI of +35.06%**.

### Visualizing the Data
![ROI Distribution](docs/images/post_jan15_roi_distribution.png)
*(Violin plot showing standard Peak ROI potential across all recommendation cohorts)*

![BUY LIMIT Deep Dive](docs/images/buy_limit_roi_comparison.png)
*(Bar chart showing how missed limit orders hold the vast majority of Alpha)*

---

## ⚠️ Disclaimer
**THIS SOFTWARE IS FOR EDUCATIONAL AND RESEARCH PURPOSES ONLY.**
It is NOT financial advice. The "decisions" made by the AI agents are simulations. Trading stocks involves significant risk of capital loss. Do not trade based on these outputs.
