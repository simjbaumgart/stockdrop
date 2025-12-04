from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from dotenv import load_dotenv
import os

load_dotenv()

from app.routers import views, api, subscriptions
import asyncio
from datetime import datetime
from app.services.stock_service import stock_service
from app.services.storage_service import storage_service
from app.services.email_service import email_service
from app.database import init_db

from app.services.performance_service import performance_service

app = FastAPI(title="StockDrop")

app.mount("/static", StaticFiles(directory="static"), name="static")

app.include_router(views.router)
app.include_router(api.router, prefix="/api")
app.include_router(subscriptions.router, prefix="/api")

@app.on_event("startup")
async def startup_event():
    init_db()
    asyncio.create_task(run_periodic_check())
    asyncio.create_task(run_storage_upload())
    asyncio.create_task(run_daily_summary())
    asyncio.create_task(run_performance_tracking())

async def run_periodic_check():
    while True:
        try:
            # Run the check in a thread pool to avoid blocking the event loop
            # since check_large_cap_drops is synchronous (uses yfinance)
            await asyncio.to_thread(stock_service.check_large_cap_drops)
        except Exception as e:
            print(f"Error in periodic check: {e}")
        await asyncio.sleep(7200) # Check every 2 hours

async def run_storage_upload():
    while True:
        try:
            print("Running Storage upload...")
            # Fetch current data
            indices_data = stock_service.get_indices()
            # Upload to GCS
            await asyncio.to_thread(storage_service.upload_data, indices_data)
            # Save locally
            await asyncio.to_thread(storage_service.save_locally, indices_data)
        except Exception as e:
            print(f"Error in Storage upload: {e}")
        await asyncio.sleep(43200) # Run every 12 hours

async def run_daily_summary():
    """
    Checks once an hour if it's time to send the daily summary (e.g., after 22:00 local time).
    """
    last_sent_date = None
    
    while True:
        try:
            now = datetime.now()
            today_str = now.strftime("%Y-%m-%d")
            
            # Send summary after 22:00 (10 PM) if not sent today
            if now.hour >= 22 and last_sent_date != today_str:
                print("Generating Daily Summary...")
                movers = await asyncio.to_thread(stock_service.get_daily_movers, 5.0)
                await asyncio.to_thread(email_service.send_daily_summary, movers)
                last_sent_date = today_str
                print("Daily Summary completed.")
                
        except Exception as e:
            print(f"Error in daily summary task: {e}")
            
        await asyncio.sleep(3600) # Check every hour

async def run_performance_tracking():
    """
    Records performance metrics once a day (e.g., after market close).
    """
    last_run_date = None
    
    while True:
        try:
            now = datetime.now()
            today_str = now.strftime("%Y-%m-%d")
            
            # Run after 23:00 (11 PM) to ensure markets are closed
            if now.hour >= 23 and last_run_date != today_str:
                print("Running Daily Performance Tracking...")
                count = await asyncio.to_thread(performance_service.record_daily_performance)
                print(f"Daily Performance Tracking completed. Recorded {count} snapshots.")
                last_run_date = today_str
                
        except Exception as e:
            print(f"Error in performance tracking task: {e}")
            
        await asyncio.sleep(3600) # Check every hour

if __name__ == "__main__":
    import uvicorn
    import sys
    
    if "--enable-email" in sys.argv:
        email_service.enabled = True
        print("Email notifications ENABLED.")
    else:
        print("Email notifications disabled (default).")
        
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
