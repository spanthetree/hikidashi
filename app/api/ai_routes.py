"""The hosted AI settings, as routes (#125): a user's own API under
/api/me/providers, the server's under /api/admin/settings/providers.

Both call app/ai/service.py and nothing else, as `hikidashi ai` does. A GET
returns key presence and the last four characters, never a key, and nothing
here ever sends one back.
"""

# No `from __future__ import annotations`: the routes are made in a function,
# and FastAPI reads their parameters' types from the annotations, which must
# be the closure's values, not strings looked up in this module.
import sqlite3
from collections.abc import Callable
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from app.accounts import User
from app.ai import service, usage
from app.ai.service import AiError


class StageIn(BaseModel):
    provider: str | None = Field(None, max_length=40)
    model: str | None = Field(None, max_length=200)
    base_url: str | None = Field(None, max_length=500)


class KeyIn(BaseModel):
    key: str = Field(..., min_length=1, max_length=1000)


class BudgetIn(BaseModel):
    usd: float | None = None


class LanguageIn(BaseModel):
    code: str | None = Field(None, max_length=3)


class UseIn(BaseModel):
    use: str = Field(..., max_length=10)


class SwitchIn(BaseModel):
    on: bool
    scope: str | None = Field(None, max_length=3)
    backlog: bool = True


class TestIn(BaseModel):
    stage: str | None = Field(None, max_length=10)


def install(  # noqa: C901 -- one closure per route
    app: FastAPI,
    db: Callable[[], sqlite3.Connection],
    who: Callable[..., User],
    scoped: Callable[[Request, str], None],
    data_dir: Callable[[], Path],
) -> None:
    @app.exception_handler(AiError)
    def ai_error(request: Request, exc: AiError) -> JSONResponse:
        return JSONResponse({"detail": str(exc)}, status_code=exc.status)

    def ai_user(request: Request, user: Annotated[User, Depends(who)]) -> User:
        scoped(request, "ai")
        return user

    def ai_admin(request: Request, user: Annotated[User, Depends(who)]) -> User:
        if not user.is_admin:
            raise HTTPException(403, "Only an admin can see this.")
        scoped(request, "ai")
        scoped(request, "admin")
        return user

    Mine = Annotated[User, Depends(ai_user)]
    Adm = Annotated[User, Depends(ai_admin)]

    def routes(prefix: str, dep: Any, owner_of: Callable[[User], int | None]) -> APIRouter:  # noqa: C901
        r = APIRouter(prefix=prefix)

        def page(user: User) -> dict[str, Any]:
            conn = db()
            owner = owner_of(user)
            out = service.show(conn, owner, data_dir())
            api = service.load(conn, owner, data_dir())
            out["spent"] = round(usage.spent(conn, api.name), 4)
            if owner is not None:
                out["applies"] = service.applies(conn, owner, data_dir())
            out["history"] = service.history(conn, owner, 20)
            return out

        @r.get("")
        def show(user: dep) -> dict[str, Any]:
            return page(user)

        @r.put("/stages/{stage}")
        def set_stage(stage: str, body: StageIn, user: dep) -> dict[str, Any]:
            service.set_stage(
                db(), user, owner_of(user), stage, data_dir(),
                provider=body.provider, model=body.model, base_url=body.base_url,
            )  # fmt: skip
            return page(user)

        @r.put("/keys/{provider}")
        def set_key(provider: str, body: KeyIn, user: dep) -> dict[str, Any]:
            service.set_key(db(), user, owner_of(user), provider, body.key, data_dir())
            return page(user)

        @r.delete("/keys/{provider}")
        def remove_key(provider: str, user: dep) -> dict[str, Any]:
            service.remove_key(db(), user, owner_of(user), provider, data_dir())
            return page(user)

        @r.put("/budget")
        def set_budget(body: BudgetIn, user: dep) -> dict[str, Any]:
            service.set_budget(db(), user, owner_of(user), body.usd, data_dir())
            return page(user)

        @r.put("/language")
        def set_language(body: LanguageIn, user: dep) -> dict[str, Any]:
            service.set_language(db(), user, owner_of(user), body.code, data_dir())
            return page(user)

        @r.get("/backlog")
        def backlog(user: dep, scope: str | None = Query(None, max_length=3)) -> dict[str, Any]:
            return service.backlog(db(), owner_of(user), data_dir(), actor=user.id, scope=scope)

        @r.post("/switch")
        def switch(body: SwitchIn, user: dep) -> dict[str, Any]:
            conn = db()
            if body.on:
                service.turn_on(
                    conn, user, owner_of(user), data_dir(), scope=body.scope, backlog=body.backlog
                )
            else:
                service.off_by(conn, user, owner_of(user), data_dir())
            return page(user)

        @r.post("/test")
        def test(body: TestIn, user: dep) -> dict[str, Any]:
            return service.test(db(), owner_of(user), data_dir(), body.stage)

        @r.get("/models")
        def models(
            user: dep,
            provider: str = Query(..., max_length=40),
            base_url: str | None = Query(None, max_length=500),
        ) -> list[str]:
            return service.models(db(), owner_of(user), provider, data_dir(), base_url=base_url)

        @r.get("/usage")
        def month(user: dep, month: str | None = Query(None, max_length=7)) -> dict[str, Any]:
            api = service.load(db(), owner_of(user), data_dir())
            try:
                return usage.report(db(), api=api.name, month=month)
            except ValueError as exc:
                raise HTTPException(400, str(exc)) from exc

        return r

    def me(user: User) -> int | None:
        return user.id

    def server(user: User) -> int | None:
        return None

    mine = routes("/api/me/providers", Mine, me)

    @mine.put("/use")
    def set_use(body: UseIn, user: Mine) -> dict[str, Any]:
        service.set_use(db(), user, user.id, body.use, data_dir())
        return service.show(db(), user.id, data_dir())

    app.include_router(mine)
    app.include_router(routes("/api/admin/settings/providers", Adm, server))

    @app.get("/api/admin/ai/users")
    def ai_users(user: Adm) -> list[dict[str, Any]]:
        """Per user: whether they have their own key, their switch, their
        choice, and this month's spend. Never a key."""
        return service.users(db(), data_dir())

    @app.delete("/api/admin/ai/users/{user_id}/keys/{provider}")
    def ai_remove_user_key(user_id: int, provider: str, user: Adm) -> list[dict[str, Any]]:
        service.remove_key(db(), user, user_id, provider, data_dir())
        return service.users(db(), data_dir())

    @app.get("/api/admin/ai/usage")
    def ai_usage(user: Adm, month: str | None = Query(None, max_length=7)) -> dict[str, Any]:
        try:
            return usage.report(db(), month=month)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
