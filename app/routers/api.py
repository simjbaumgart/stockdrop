from fastapi import APIRouter
from app.services.stock_service import stock_service
import time

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

@router.get("/deep-research/status")
def get_deep_research_status():
    """
    Returns the real-time health and queue status of the background Deep Research agent.
    """
    from app.services.deep_research_service import deep_research_service as dr_service
        
    duration = 0
    if dr_service.current_task_start_time:
        duration = int(time.time() - dr_service.current_task_start_time)
        
    return {
        "status": "running" if dr_service.is_running else "stopped",
        "active_tasks": dr_service.active_tasks_count,
        "current_job": dr_service.current_task_name,
        "duration_seconds": duration,
        "queue": {
            "individual": dr_service.individual_queue.qsize(),
            "batch": dr_service.batch_queue.qsize()
        }
    }
