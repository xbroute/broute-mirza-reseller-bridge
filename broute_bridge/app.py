from __future__ import annotations

from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException, Request

from . import __version__
from .config import Settings
from .crypto import SecretBox
from .db import Database, Profile
from .service import AgentService


def create_app(
    settings: Settings | None = None,
    *,
    db: Database | None = None,
    service: AgentService | None = None,
) -> FastAPI:
    settings = settings or Settings.from_env()
    if db is None:
        SecretBox.ensure_key_file(settings.master_key_path)
        db = Database(settings.db_path, SecretBox.from_file(settings.master_key_path))
    service = service or AgentService(
        db,
        timeout=settings.request_timeout,
        verify_tls=settings.verify_reseller_tls,
        replay_window_seconds=settings.replay_window_seconds,
    )

    app = FastAPI(title="Broute Mirza Reseller Bridge", version=__version__)
    app.state.db = db
    app.state.service = service

    def profile_auth(authorization: str | None = Header(default=None)) -> Profile:
        if not authorization or not authorization.lower().startswith("bearer "):
            raise HTTPException(status_code=401, detail="Missing Bearer token")
        token = authorization.split(" ", 1)[1].strip()
        profile = db.authenticate(token)
        if not profile:
            raise HTTPException(status_code=401, detail="Invalid Bridge token")
        return profile

    @app.get("/healthz")
    def healthz() -> dict[str, Any]:
        return {"ok": True, "version": __version__}

    @app.get("/readyz")
    def readyz() -> dict[str, Any]:
        db.list_profiles()
        return {"ok": True}

    @app.get("/")
    def agent_get(
        request: Request,
        profile: Profile = Depends(profile_auth),
    ) -> dict[str, Any]:
        params = dict(request.query_params)
        action = params.pop("actions", "")
        return service.dispatch(profile, action, params)

    @app.post("/")
    @app.put("/")
    @app.delete("/")
    async def agent_mutation(
        request: Request,
        profile: Profile = Depends(profile_auth),
    ) -> dict[str, Any]:
        try:
            body = await request.json()
        except Exception as exc:
            raise HTTPException(status_code=400, detail="Body must be JSON") from exc
        if not isinstance(body, dict):
            raise HTTPException(status_code=400, detail="Body must be a JSON object")
        action = body.get("actions", "")
        return service.dispatch(profile, str(action), body)

    @app.get("/admin/doctor")
    def doctor(profile: Profile = Depends(profile_auth)) -> dict[str, Any]:
        client = service.client(profile)
        me = client.me()
        inbounds = client.inbounds()
        return {
            "ok": True,
            "profile": profile.name,
            "reseller_me": me,
            "inbounds_reachable": isinstance(inbounds, dict),
        }

    return app
