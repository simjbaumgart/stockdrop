from fastapi import APIRouter
from app.services.stock_service import stock_service

router = APIRouter()

@router.get("/indices")
def get_indices():
    return stock_service.get_indices()

@router.get("/movers")
def get_top_movers():
    return stock_service.get_top_movers()

@router.get("/large-cap-movers")
def get_large_cap_movers():
    return stock_service.get_large_cap_movers()

@router.get("/stock/{symbol}")
def get_stock_details(symbol: str):
    return stock_service.get_stock_details(symbol)

@router.get("/stock/{symbol}/options")
def get_options_dates(symbol: str):
    return stock_service.get_options_dates(symbol)

@router.get("/stock/{symbol}/options/{date}")
def get_option_chain(symbol: str, date: str):
    return stock_service.get_option_chain(symbol, date)

@router.get("/research-reports")
def get_research_reports():
    return stock_service.research_reports
