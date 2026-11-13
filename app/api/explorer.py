"""The API explorer: a documented, browsable API at /api/docs (#147).

FastAPI generates the OpenAPI schema; this shapes it into something worth
exploring, and serves it only to people who are signed in:

- every route gets a tag (its area), a summary (its docstring's first line),
  and the way it is authorised: an API token with the scope it needs (read or
  write by method, as `_scoped` decides), the browser's session, a worker
  token, or nothing for the sign-in routes;
- the main routes describe what they return (app/api/schemas.py);
- the schema says which release it describes;
- a non-admin gets the schema without the admin and worker routes, so the
  explorer does not offer what they cannot call.

The page is Swagger UI, bundled into the front end at build time
(frontend/vite.config.ts copies it to /explorer/), so nothing is loaded from a
CDN. A token given to "Authorize" stays in the page's memory, and trying a
route that changes something is off until "Allow changes" is ticked.
"""

from __future__ import annotations

import copy
import html
from collections.abc import Callable
from importlib.metadata import PackageNotFoundError, version
from typing import Any

from fastapi import FastAPI, Request
from fastapi.openapi.utils import get_openapi
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.routing import APIRoute
from pydantic import TypeAdapter

from .schemas import RESPONSES

TAGS = [
    ("Library", "Your reels: the grid, one reel, search, places, collections, tags."),
    ("Recipes & shopping", "Recipes read out of reels, your corrections, the shopping list."),
    ("Import & export", "Instagram exports in, your library out."),
    ("Account", "You: your profile, sign-in, API tokens, your AI settings."),
    ("Admin", "Accounts, invites, worker tokens, the pipeline, logs. Admins only."),
    ("Worker protocol", "What remote workers call, with a worker token."),
]

# Path prefixes to tags, the first that matches.
_AREAS = [
    ("/api/worker/", "Worker protocol"),
    ("/api/admin/", "Admin"),
    ("/api/users", "Admin"),
    ("/api/invites", "Admin"),
    ("/api/worker-tokens", "Admin"),
    ("/api/tokens", "Admin"),
    ("/api/pipeline", "Admin"),
    ("/api/logs", "Admin"),
    ("/api/recipes", "Recipes & shopping"),
    ("/api/shopping", "Recipes & shopping"),
    ("/api/import", "Import & export"),
    ("/api/export", "Import & export"),
    ("/api/me", "Account"),
    ("/api/auth", "Account"),
]

# Which dependency a route uses says how it is authorised.
_SCOPE = {"library_user": "library", "admin": "admin", "export_owner": "library", "ai_user": "ai"}
_ADMIN = {"admin", "ai_admin"}
_WORKER = {"worker_auth"}
_SESSION = {"by_session"}
_ANYONE = {"current_user"}


# For the routes whose docstring is missing: their summary, by function. The
# AI provider routes serve both your settings and the server's.
SUMMARIES = {
    "show": "{whose} AI provider settings",
    "set_stage": "Set a stage's provider and model, for {whose_lower} API",
    "set_key": "Save a provider key, encrypted, for {whose_lower} API",
    "remove_key": "Remove a provider key from {whose_lower} API",
    "set_budget": "Set the monthly budget of {whose_lower} API",
    "set_language": "Set the language transcription listens for, on {whose_lower} API",
    "backlog": "The reels {whose_lower} API would take if switched on",
    "switch": "Switch {whose_lower} API on or off",
    "test": "Try a provider and model with a small request",
    "models": "The models a provider offers",
    "month": "What {whose_lower} API used this month",
    "ai_remove_user_key": "Remove a user's provider key",
    "ai_usage": "Hosted AI usage, every API",
    "login": "Sign in with a username and password",
    "logout": "Sign out this browser",
    "invite_state": "Whether an invite link is still good",
    "me": "Who you are, and your settings",
    "sign_out_everywhere": "Sign out every browser you are signed in on",
    "my_tokens": "Your API tokens",
    "users": "Every account",
    "change_user": "Disable or enable an account, or make it an admin",
    "open_invites": "Invites not yet used",
    "revoke_invite": "Withdraw an invite",
    "stats": "Counts over your library: reels, pipeline states, collections",
    "collection_names": "Your Instagram collections, with how many reels each",
    "items": "Your reels, filtered, searched and sorted, a page at a time",
    "item_detail": "Everything about one reel in your library",
    "drop_from_list": "Take a recipe off your shopping list",
    "clear_list": "Empty your shopping list",
    "revoke_anyones_token": "Revoke anyone's API token",
    "upload_export": "Import an Instagram export (a ZIP) into your library",
}


def summary_of(route: APIRoute) -> str:
    doc = " ".join((route.endpoint.__doc__ or "").strip().split("\n\n")[0].split())
    if doc:
        return doc.split(". ")[0].rstrip(".")[:120]
    server = route.path.startswith("/api/admin/settings/")
    return SUMMARIES.get(route.name, route.name.replace("_", " ")).format(
        whose="The server's" if server else "Your",
        whose_lower="the server's" if server else "your",
    )


def tag_of(path: str) -> str:
    if "/recipe" in path and path.startswith("/api/items/"):
        return "Recipes & shopping"
    return next((tag for prefix, tag in _AREAS if path.startswith(prefix)), "Library")


def _calls(dependant: Any) -> set[str]:
    out: set[str] = set()
    for d in dependant.dependencies:
        if d.call is not None:
            out.add(getattr(d.call, "__name__", ""))
        out |= _calls(d)
    return out


def app_version() -> str:
    try:
        return version("hikidashi")
    except PackageNotFoundError:
        return "dev"


def build(app: FastAPI) -> dict[str, Any]:
    """The whole schema, every route tagged, summarised and authorised."""
    schema = get_openapi(
        title="Hikidashi",
        version=app_version(),
        description=(
            "Your library, for scripts. Make an API token on your Account page "
            "(or `hikidashi token make`), then send it as `Authorization: Bearer "
            "hk_api_…`. In the browser, the explorer can use your session instead."
        ),
        routes=app.routes,
        tags=[{"name": n, "description": d} for n, d in TAGS],
    )
    schema["components"] = schema.get("components", {})
    schema["components"]["securitySchemes"] = {
        "apiToken": {"type": "http", "scheme": "bearer", "description": "An API token, hk_api_…"},
        "session": {"type": "apiKey", "in": "cookie", "name": "hk_session"},
        "workerToken": {"type": "http", "scheme": "bearer", "description": "A worker token"},
    }
    models = schema["components"].setdefault("schemas", {})
    for route in app.routes:
        if not isinstance(route, APIRoute) or not route.include_in_schema:
            continue
        calls = _calls(route.dependant)
        for method in route.methods:
            op = schema["paths"].get(route.path, {}).get(method.lower())
            if op is None:
                continue
            op["tags"] = [tag_of(route.path)]
            op["summary"] = summary_of(route)
            write = method not in ("GET", "HEAD")
            area = next((_SCOPE[c] for c in calls if c in _SCOPE), None)
            if calls & _WORKER:
                op["security"] = [{"workerToken": []}]
                op["x-hikidashi-access"] = "worker"
            elif calls & _SESSION:
                op["security"] = [{"session": []}]
                op["x-hikidashi-access"] = "session"
            elif area or calls & (_ANYONE | _ADMIN):
                scope = (
                    f"{'write' if write else 'read'}:{area or 'admin'}"
                    if (area or calls & _ADMIN)
                    else None
                )
                op["security"] = [{"apiToken": []}, {"session": []}]
                if scope:
                    op["x-hikidashi-scope"] = scope
                    op["description"] = (
                        f"Requires `{scope}`.\n\n{op.get('description', '')}".strip()
                    )
                op["x-hikidashi-access"] = "admin" if calls & _ADMIN else "user"
                op.setdefault("responses", {})["401"] = {"description": "Not signed in"}
                op["responses"]["403"] = {"description": "The token lacks the scope"}
            else:
                op["security"] = []
                op["x-hikidashi-access"] = "public"
            model = RESPONSES.get((method, route.path))
            if model is not None:
                adapter = TypeAdapter(model)
                js = adapter.json_schema(ref_template="#/components/schemas/{model}")
                models.update(js.pop("$defs", {}))
                op["responses"]["200"] = {
                    "description": "OK",
                    "content": {"application/json": {"schema": js}},
                }
    return schema


def visible(schema: dict[str, Any], *, admin: bool) -> dict[str, Any]:
    """The schema as this person may see it: without the admin and worker
    routes unless they are an admin."""
    if admin:
        return schema
    out = copy.deepcopy(schema)
    for path in list(out["paths"]):
        ops = {
            m: op
            for m, op in out["paths"][path].items()
            if op.get("x-hikidashi-access") not in ("admin", "worker")
            and op["tags"][0] not in ("Admin", "Worker protocol")
        }
        if ops:
            out["paths"][path] = ops
        else:
            del out["paths"][path]
    out["tags"] = [t for t in out.get("tags", []) if t["name"] not in ("Admin", "Worker protocol")]
    out["components"]["securitySchemes"].pop("workerToken", None)
    return out


PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Hikidashi API</title>
<link rel="icon" href="/favicon.ico">
<link rel="stylesheet" href="/explorer/swagger-ui.css">
<style>
  body { margin: 0; background: #fffdf8; }
  .hk-bar { display: flex; gap: 16px; align-items: center; flex-wrap: wrap; padding: 12px 20px;
    font: 14px system-ui, sans-serif; background: #f3efe6; border-bottom: 1px solid #e5dccb; }
  .hk-bar a { color: #c4361f; }
  .hk-bar label { display: flex; gap: 6px; align-items: center; }
  .hk-bar .hk-note { color: #6b6457; }
  @media (prefers-color-scheme: dark) {
    body { background: #16140f; filter: invert(0.9) hue-rotate(180deg); }
    .hk-bar { filter: none; }
  }
</style>
</head>
<body>
<div class="hk-bar">
  <a href="/">&larr; Library</a>
  <a href="/account#tokens">Make an API token</a>
  <label><input type="checkbox" id="hk-writes"> Allow changes</label>
  <span class="hk-note" id="hk-auth">__AUTH__</span>
</div>
<div id="ui"></div>
<script src="/explorer/swagger-ui-bundle.js"></script>
<script>
  // GET only, until changes are allowed: Try it out acts on your real library.
  function start(writes) {
    window.ui = SwaggerUIBundle({
      url: "/api/openapi.json",
      dom_id: "#ui",
      deepLinking: true,
      persistAuthorization: false,
      tryItOutEnabled: false,
      supportedSubmitMethods: writes ? ["get", "put", "post", "delete", "patch"] : ["get"],
    });
  }
  document.getElementById("hk-writes").addEventListener("change", (e) => {
    if (e.target.checked && !confirm("Try it out will change your real library. Allow changes?")) {
      e.target.checked = false;
      return;
    }
    start(e.target.checked);
  });
  start(false);
</script>
</body>
</html>
"""


def install(app: FastAPI, who: Callable[[Request], Any]) -> None:
    """Serve the explorer and its schema, to signed-in people only. `who`
    returns the signed-in user for a request, or raises."""
    cache: dict[str, Any] = {}

    def full() -> dict[str, Any]:
        if "schema" not in cache:
            cache["schema"] = build(app)
        return cache["schema"]  # type: ignore[no-any-return]

    def user_of(request: Request) -> Any:
        try:
            return who(request)
        except Exception:
            return None

    @app.get("/api/openapi.json", include_in_schema=False)
    def openapi(request: Request) -> Response:
        user = user_of(request)
        if user is None:
            return JSONResponse({"detail": "Sign in to see the API."}, status_code=401)
        return JSONResponse(visible(full(), admin=bool(user.is_admin)))

    @app.get("/api/docs", include_in_schema=False)
    def docs(request: Request) -> Response:
        user = user_of(request)
        if user is None:
            return RedirectResponse("/login?next=/api/docs", status_code=303)
        auth = (
            f"Signed in as {html.escape(user.username)}: Try it out uses your session, "
            "or the token you give Authorize, kept in this page only."
        )
        return HTMLResponse(PAGE.replace("__AUTH__", auth))
