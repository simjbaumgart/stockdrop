from fastapi import APIRouter, Request, Depends, HTTPException
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from typing import Optional
from app.services.performance_service import performance_service

router = APIRouter()
templates = Jinja2Templates(directory="templates")

class PerformanceRequest(BaseModel):
    symbol: str
    buy_date: str
    investment_amount: Optional[float] = 1000.0

@router.get("/performance")
def performance_dashboard(request: Request):
    return templates.TemplateResponse("performance.html", {"request": request})

@router.post("/api/performance/analyze")
async def analyze_performance(request: PerformanceRequest):
    result = performance_service.analyze_historical_trade(
        symbol=request.symbol,
        buy_date=request.buy_date,
        investment_amount=request.investment_amount
    )
    
    if "error" in result:
        raise HTTPException(status_code=400, detail=result["error"])
        
    return result
