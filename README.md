# StockDrop 📉

A real-time stock tracking dashboard with automated email alerts for significant market drops.

## Features

- **Live Dashboard**: Track major indices (S&P 500, CSI 300, STOXX 600) and top movers.
- **Large Cap Alerts**: Automatically detects when a large-cap stock (> $500M) drops more than 6% in a day.
- **Email Notifications**: Sends an immediate email alert with price details.
- **User Subscriptions**: Users can subscribe to alerts directly from the dashboard.
- **Research Reports**: (Optional) AI-generated analysis of the drop.

## Setup

1.  **Clone the repository**:
    ```bash
    git clone https://github.com/simjbaumgart/stockdrop.git
    cd stockdrop
    ```

2.  **Install dependencies**:
    ```bash
    python3 -m venv venv
    source venv/bin/activate
    pip install -r requirements.txt
    ```

3.  **Configure Environment**:
    Create a `.env` file in the root directory:
    ```env
    SMTP_SERVER=smtp.gmail.com
    SMTP_PORT=587
    SENDER_EMAIL=your_email@gmail.com
    SENDER_PASSWORD=your_app_password
    RECIPIENT_EMAIL=your_email@example.com
    ```

4.  **Run the App**:
    ```bash
    uvicorn main:app --reload
    ```
    Visit `http://localhost:8000` in your browser.

## Deployment (Render)

1.  Create a new **Web Service** on Render connected to this repo.
2.  **Build Command**: `pip install -r requirements.txt`
3.  **Start Command**: `uvicorn main:app --host 0.0.0.0 --port $PORT`
4.  Add the Environment Variables from your `.env` file in the Render dashboard.
