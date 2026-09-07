# /// script
# requires-python = ">=3.11"
# dependencies = ["fastapi==0.116.1", "uvicorn==0.35.0", "jinja2==3.1.6"]
# ///
"""Read-only dashboard: shared shell, page templates and evidence fragments."""

from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fixtures import DATA, statistics
from jinja2 import Environment, FileSystemLoader, StrictUndefined, select_autoescape

ROOT = Path(__file__).resolve().parent
PAGES = ("home", "experience", "usage")
STATES = ("normal", "empty", "missing", "unknown", "uncomparable", "negative", "unavailable")
env = Environment(
    loader=FileSystemLoader(ROOT / "templates"), autoescape=select_autoescape(), undefined=StrictUndefined
)
app = FastAPI(openapi_url=None, docs_url=None, redoc_url=None)
app.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")


def context(request: Request, page: str) -> dict[str, Any]:
    def choice(key: str, values: tuple[str, ...]) -> str:
        value = request.query_params.get(key, values[0])
        return value if value in values else values[0]

    scope = choice("scope", ("payments", "research"))
    state = choice("state", STATES)
    period = choice("period", ("7d", "today", "30d"))
    ablation = choice("ablation", ("none", "evidence", "chart", "navigation"))

    def link(destination: str = page, fragment: str = "", **params: str) -> str:
        query = {"scope": scope, "state": state, "period": period, "ablation": ablation, **params}
        return f"/{destination}?{urlencode(query)}" + (f"#{fragment}" if fragment else "")

    return {
        "t": DATA["labels"],
        "data": DATA[scope],
        "page": page,
        "scope": scope,
        "scenario": state,
        "period": period,
        "ablation": ablation,
        "link": link,
        "stats": statistics(period, state, scope),
    }


def response(request: Request, ctx: dict[str, Any], fragment: str, full: str, status: int = 200) -> HTMLResponse:
    partial = (
        request.headers.get("HX-Request") == "true" and request.headers.get("HX-History-Restore-Request") != "true"
    )
    return HTMLResponse(
        env.get_template(fragment if partial else full).render(**ctx),
        status_code=status,
        headers={
            "Cache-Control": "no-store",
            "Vary": "HX-Request, HX-History-Restore-Request",
            "X-Dashboard-HTML": "1",
        },
    )


@app.get("/")
async def index() -> RedirectResponse:
    return RedirectResponse("/home")


@app.get("/evidence/{source_id}")
async def evidence(request: Request, source_id: str) -> HTMLResponse:
    ctx = context(request, "experience")
    source = next((item for item in ctx["data"]["sources"] if item["source_id"] == source_id), None)
    if ctx["scenario"] == "empty":
        source = None
    ctx["source"] = source
    status = 404 if source is None else 503 if ctx["scenario"] == "missing" else 200
    return response(request, ctx, "evidence.html", "source.html", status)


@app.get("/{page}")
async def screen(request: Request, page: str) -> HTMLResponse:
    ctx = context(request, page if page in PAGES else "home")
    ctx["not_found"] = page not in PAGES
    return response(request, ctx, "workspace.html", "base.html", 404 if ctx["not_found"] else 200)


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8765)
