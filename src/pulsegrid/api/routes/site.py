"""Public landing page."""

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

from pulsegrid.web import landing_page as load_landing_page

router = APIRouter(include_in_schema=False)


@router.get("/", response_class=HTMLResponse)
async def landing_page() -> HTMLResponse:
    """Serve the marketing landing page (no authentication)."""
    return HTMLResponse(load_landing_page())
