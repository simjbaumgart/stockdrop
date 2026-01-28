# Token Optimization Demo Results

Generated on: 2026-01-27 16:09:56

## 1. Batch Context Summarization
Demonstrating reduction of full Council Reports into concise summaries for Batch processing.

### Stock: TRU
- **Original Report Size:** 145,959 bytes
- **Summarized Context Size:** 586 bytes
- **Token Reduction:** **99.60%**

#### Summary Content (Preview):
```text
TECHNICAL VERDICT: Value Play disguised as a Technical Breakdown.
NEWS (DROP REASON): The stock's -5.42% decline is primarily driven by **regulatory intervention and legal headwinds**:
1.  **FHFA Criticism (Primary Catalyst - Jan 6, 2026):** Federal Housing Finance Agency (FHFA) Director Bill Pulte explicitly criticized the pricing practices of credit reporting agencies (specifically naming Equifax and TransUnion). This raises fears of potential price caps or stricter regulations in the mortgage credit reporting space, which is a high-margin vertical.
2.  **Class Action Lawsuit (
```

### Stock: APP
- **Original Report Size:** 116,052 bytes
- **Summarized Context Size:** 743 bytes
- **Token Reduction:** **99.36%**

#### Summary Content (Preview):
```text
TECHNICAL SUMMARY: This Technical Playbook analyzes **APP (AppLovin)** following its recent **-5.83%** daily drop and its broader **-27.24%** one-month correction.

### 1. Technical Signal: Bearish Acceleration Toward L...
NEWS (DROP REASON): The primary catalyst for the stock's -5.83% decline is a **scathing short-seller report released by CapitalWatch on January 20-21, 2026**. 
*   **Allegations:** The report alleges that AppLovin has become a "safe haven" for illicit funds, citing ties to a multibillion-dollar money-laundering network involving Chinese Ponzi schemes and Cambodian fraud rings.
*   **Compliance Risks:** CapitalWatch claims that primary shareholder Hao Tang and associated networks bypassed global Anti-Money Launderin
```

## 2. Data Cleaning (Seeking Alpha)
Demonstrating removal of HTML and noise from fetched data.

### Stock: TRU (Simulated Raw Input)
#### Raw Input:
```html
<div id="article-body">
                <h1>TRU Earnings Report</h1>
                <p class="summary-bullet">This is a summary point for TRU.</p>
                <div class="ad-container">BUY NOW - LIMITED TIME OFFER</div>
                
                <p>The company TRU reported strong earnings today. Revenue exceeded analyst expectations by 15%.</p>
                
                <img src="chart.jpg" alt="Chart" />
                
                <h2>Future Outlook</h2>
                <p class="paywall-full-content">Investors are optimistic about the future guidance. Management expects double-digit growth next quarter.</p>
                
                <p>However, risks remain regarding supply chain constraints.</p>
                <script>console.log('tracker');</script>
            </div>
```
#### Cleaned Output:
```text
TRU Earnings Report 

 This is a summary point for TRU. 

 The company TRU reported strong earnings today. Revenue exceeded analyst expectations by 15%. 

 Future Outlook 

 Investors are optimistic about the future guidance. Management expects double-digit growth next quarter. 

 However, risks remain regarding supply chain constraints.
```
- **Reduction:** 841 -> 339 bytes

### Stock: APP (Simulated Raw Input)
#### Raw Input:
```html
<div id="article-body">
                <h1>APP Earnings Report</h1>
                <p class="summary-bullet">This is a summary point for APP.</p>
                <div class="ad-container">BUY NOW - LIMITED TIME OFFER</div>
                
                <p>The company APP reported strong earnings today. Revenue exceeded analyst expectations by 15%.</p>
                
                <img src="chart.jpg" alt="Chart" />
                
                <h2>Future Outlook</h2>
                <p class="paywall-full-content">Investors are optimistic about the future guidance. Management expects double-digit growth next quarter.</p>
                
                <p>However, risks remain regarding supply chain constraints.</p>
                <script>console.log('tracker');</script>
            </div>
```
#### Cleaned Output:
```text
APP Earnings Report 

 This is a summary point for APP. 

 The company APP reported strong earnings today. Revenue exceeded analyst expectations by 15%. 

 Future Outlook 

 Investors are optimistic about the future guidance. Management expects double-digit growth next quarter. 

 However, risks remain regarding supply chain constraints.
```
- **Reduction:** 841 -> 339 bytes


## 3. Real Data Verification (Seeking Alpha)
Processing real file: `experiment_data/analysis_4854256.html`

### Real File Results:
- **Original Size:** 19899 bytes
- **Cleaned Size:** 9107 bytes
- **Reduction:** 54.23%

#### Cleaned Content Snippet (First 2000 chars):
```text
Why I Am Buying Alphabet Over Microsoft 

 Kenneth Cheung/iStock Unreleased via Getty Images 

 Setting The Stage 
 As of today (17 th December, 2025), Microsoft Corporation ( MSFT ) and Alphabet Inc. ( GOOG ) are two of the most dominant forces in the technology arena. Both boast market caps above $3 trillion and are key players in AI and cloud computing. MSFT has been for a while viewed by several analysts as the premium AI compounder due to its early OpenAI partnerships and seamless integration of Copilot across its ecosystem. However, Alphabet has staged a remarkable comeback this year, with its stock surging more than 60% compared to MSFT’s ~14%. This surge saw GOOG overtake MSFT in market cap, a testament to its solid rise. 
 To this end, some investors might wonder which of these two stocks offers a better risk-adjusted upside from their current levels. I will turn to an Ordinary Least Squares [OLS] regression to model relationships between key financial drivers (revenue growth, operating margins, and CapEx efficiency), being the independent variables, against the stock returns as the dependent variable. My analysis seeks to provide evidence-based insights into relative valuation and forward potential. Given this background, I am bullish on Alphabet given its tolerance to high capital expenditure amid its accelerated cloud growth. 
 Recent Financial Highlights 
 In June this year, MSFT ended its 2025 financial year, delivering revenue of $281.7 billion, marking a YoY growth of 15%. This was primarily driven by Cloud business growth with the total Microsoft cloud revenue growing by 25% YoY. On the other hand, Alphabet’s trailing revenue stands at $385.5 billion, a YoY growth of 13.4%, which is close to MSFT’s trailing revenue growth. This is also being fueled by its Google Cloud, which has accelerated growth to 34% YoY growth in Q3 2025. 
 Given this financial snapshot, it’s clear that Alphabet is edging MSFT in cloud growth, perhaps due to Azure’s capacity co
...
```

#### Cleaned Content Snippet (Last 1000 chars):
```text
an AI bubble burst . While GOOG’s value lies in its consistent strong growth, should the bubble burst, leading to low growth, the stock’s value could tumble, leading to losses. Historically, when GOOG experiences low revenue growth, its stock performance is poor. For example, in 2022, when the stock experienced a single-digit revenue growth for the first time in over the last five years, its stock return dipped by 38.7%, the worst annual return performance in more than five years. 
 Conclusion 

 I recommend buying GOOG over MSFT amid the ongoing heavy capital expenditure in the AI race. The OLS model shows that the market punishes MSFT for aggressive capital expenditure while attaching a high value to GOOG’s revenue growth. While the biggest value driver for MSFT is margin growth, the ongoing capital outlay threatens its short term margin growth sustainability, but Alphabet shows higher investor tolerance. With these ongoing dynamics, I think Alphabet is an outright buy at a discount.
```
