"""Serves the static web admin panel at ``/admin``.

The panel is three files — ``index.html``, ``app.css`` and ``app.js`` in
``app/admin_web`` — with no build step, so there is nothing to compile on the
server and nothing third-party to trust.

Explicit routes rather than ``StaticFiles`` for two reasons:

* **Only these three files are reachable.** A mounted directory serves
  whatever lands in it, including an editor's backup or a stray ``.env``
  copied there by mistake.
* **The headers are set per file.** The API's own policy is
  ``default-src 'none'`` (it serves JSON only), which would stop the panel
  from loading its own script. These responses set a policy that allows
  exactly the panel's same-origin files and nothing else;
  ``SecurityHeadersMiddleware`` uses ``setdefault`` and so leaves it alone.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import Response

WEB_ROOT = Path(__file__).resolve().parent.parent / "admin_web"

# No inline script or style is allowed, so an injected <script> or style
# attribute does nothing even if an escaping bug ever let one into the page.
# connect-src 'self' means a stolen token cannot be posted anywhere else from
# inside the page; frame-ancestors stops clickjacking the confirm buttons.
ADMIN_CSP = (
    "default-src 'self'; script-src 'self'; style-src 'self'; "
    "img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; "
    "base-uri 'none'; form-action 'self'"
)

_FILES = {
    "index.html": "text/html; charset=utf-8",
    "app.css": "text/css; charset=utf-8",
    "app.js": "text/javascript; charset=utf-8",
}

router = APIRouter(include_in_schema=False)


def _serve(name: str) -> Response:
    body = (WEB_ROOT / name).read_bytes()
    return Response(
        content=body,
        media_type=_FILES[name],
        headers={
            "Content-Security-Policy": ADMIN_CSP,
            "X-Frame-Options": "DENY",
            "Referrer-Policy": "no-referrer",
            # The page is the entry point: never let a shared or back-button
            # cache show a panel from a previous session. Assets revalidate so
            # an update reaches the operator's phone on the next load.
            "Cache-Control": "no-store" if name == "index.html" else "no-cache",
        },
    )


@router.api_route("/admin", methods=["GET", "HEAD"])
@router.api_route("/admin/", methods=["GET", "HEAD"])
async def admin_index() -> Response:
    return _serve("index.html")


@router.api_route("/admin/app.css", methods=["GET", "HEAD"])
async def admin_css() -> Response:
    return _serve("app.css")


@router.api_route("/admin/app.js", methods=["GET", "HEAD"])
async def admin_js() -> Response:
    return _serve("app.js")
