from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.api import digests, profile, subscriptions
from app.config import settings
from app.db.models import Base
from app.db.session import engine
from app.services.scheduler import build_scheduler

logging.basicConfig(level=logging.INFO)

templates = Jinja2Templates(directory="app/templates")

scheduler = build_scheduler()


@asynccontextmanager
async def lifespan(app: FastAPI):
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    scheduler.start()
    yield
    scheduler.shutdown(wait=False)


app = FastAPI(title=settings.app_name, lifespan=lifespan)

app.include_router(profile.router)
app.include_router(subscriptions.router)
app.include_router(digests.router)

app.mount("/static", StaticFiles(directory="app/static"), name="static")


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse(request, "index.html")