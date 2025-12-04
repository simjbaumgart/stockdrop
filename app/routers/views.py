from fastapi import APIRouter, Request
from fastapi.templating import Jinja2Templates
from app.database import get_decision_points, get_decision_point
router = APIRouter()
templates = Jinja2Templates(directory="templates")

@router.get("/")
def dashboard(request: Request):
    decision_points = get_decision_points()
    return templates.TemplateResponse("dashboard.html", {"request": request, "decision_points": decision_points})

@router.get("/stock/{symbol}")
def stock_details(request: Request, symbol: str):
    return templates.TemplateResponse("stock_details.html", {"request": request, "symbol": symbol})

@router.get("/decisions")
def decisions(request: Request):
    decision_points = get_decision_points()
    return templates.TemplateResponse("decisions.html", {"request": request, "decision_points": decision_points})

@router.get("/decision/{decision_id}")
def decision_detail(request: Request, decision_id: int):
    decision = get_decision_point(decision_id)
    if not decision:
        return templates.TemplateResponse("404.html", {"request": request}, status_code=404)
    return templates.TemplateResponse("decision_detail.html", {"request": request, "decision": decision})
