from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from dotenv import load_dotenv
import os

load_dotenv()

from app.routers import views, api, subscriptions
import asyncio
from app.services.stock_service import stock_service
from app.database import init_db

app = FastAPI(title="Stock Tracker")

app.mount("/static", StaticFiles(directory="static"), name="static")

app.include_router(views.router)
app.include_router(api.router, prefix="/api")
app.include_router(subscriptions.router, prefix="/api")

@app.on_event("startup")
async def startup_event():
    init_db()
    asyncio.create_task(run_periodic_check())

async def run_periodic_check():
    while True:
        try:
            # Run the check in a thread pool to avoid blocking the event loop
            # since check_large_cap_drops is synchronous (uses yfinance)
            await asyncio.to_thread(stock_service.check_large_cap_drops)
        except Exception as e:
            print(f"Error in periodic check: {e}")
        await asyncio.sleep(300) # Check every 5 minutes

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
