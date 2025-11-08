# app/routers/pages.py
from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from ..deps import templates

router = APIRouter(tags=["pages"])

@router.get("/", response_class=HTMLResponse, response_model=None)
def home(request: Request):
    # mirror Flask behavior: redirect if logged in
    if request.session.get("user_id"):
        return RedirectResponse(url="/welcome", status_code=303)
    return templates.TemplateResponse("landing.html", {"request": request})
