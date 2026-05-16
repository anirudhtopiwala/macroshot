"""Shared utilities for OAuth route modules."""

from html import escape

from fastapi.responses import HTMLResponse


def redirect_html(url: str) -> HTMLResponse:
    """Return a small HTML page that redirects to the given URL."""
    safe_url = escape(url, quote=True)
    html = (
        f'<!DOCTYPE html>\n'
        f'<html><head><meta http-equiv="refresh" content="0;url={safe_url}"></head>\n'
        f'<body><p>Redirecting... <a href="{safe_url}">Click here</a> if not redirected.</p></body>\n'
        f'</html>'
    )
    return HTMLResponse(content=html)
