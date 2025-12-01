from fastapi import APIRouter, Request
from fastapi.templating import Jinja2Templates

router = APIRouter()
templates = Jinja2Templates(directory="templates")

@router.get("/")
def dashboard(request: Request):
    return templates.TemplateResponse("dashboard.html", {"request": request})

@router.get("/stock/{symbol}")
def stock_details(request: Request, symbol: str):
    return templates.TemplateResponse("stock_details.html", {"request": request, "symbol": symbol})
