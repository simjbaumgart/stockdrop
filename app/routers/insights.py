"""Insights routes: aggregated performance dashboard."""
from __future__ import annotations

import logging

from fastapi import APIRouter, Query, Request
from fastapi.templating import Jinja2Templates

from app.services.analytics.summary import summary_json

logger = logging.getLogger(__name__)
router = APIRouter()
templates = Jinja2Templates(directory="templates")


@router.get("/insights")
def insights_page(request: Request):
    return templates.TemplateResponse("insights.html", {"request": request})


@router.get("/api/insights/summary")
def insights_summary(refresh: bool = Query(False), start_date: str = Query("2026-02-01")):
    return summary_json(refresh=refresh, start_date=start_date)
