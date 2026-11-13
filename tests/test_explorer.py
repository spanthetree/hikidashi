"""The API explorer (#147): a schema worth exploring, shown to the right people."""

import json
import os
import re
import unittest
from pathlib import Path
from unittest import mock

from tests import test_api
from tests.test_api import ALICE, BOB, HAS_WEB, ApiCase

SNAPSHOT = Path(__file__).parent / "openapi.snapshot.json"


def setUpModule():
    test_api.setUpModule()


@unittest.skipUnless(HAS_WEB, "the web dependencies are not installed")
class TestExplorer(ApiCase):
    def setUp(self):
        super().setUp()
        # The explorer asks for the user itself, not through FastAPI's
        # dependencies, so the tests' overrides do not reach it: stand in here.
        self.user = ALICE
        who = mock.patch.object(self.main, "current_user", lambda request: self.user)
        who.start()
        self.addCleanup(who.stop)

    def schema(self):
        r = self.client.get("/api/openapi.json")
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()

    def ops(self, schema):
        return [(p, m, op) for p, ops in schema["paths"].items() for m, op in ops.items()]

    def test_every_route_is_tagged_summarised_and_authorised(self):
        schema = self.schema()
        self.assertTrue(schema["openapi"].startswith("3.1"))
        self.assertNotIn(schema["info"]["version"], ("", "0.1.0"))
        names = {t["name"] for t in schema["tags"]}
        for path, method, op in self.ops(schema):
            where = f"{method.upper()} {path}"
            self.assertEqual(len(op["tags"]), 1, where)
            self.assertIn(op["tags"][0], names, where)
            self.assertTrue(op["summary"], where)
            self.assertIn("security", op, where)
            if "x-hikidashi-scope" in op:
                want = "read:" if method == "get" else "write:"
                self.assertTrue(op["x-hikidashi-scope"].startswith(want), where)

    def test_scopes_follow_the_route(self):
        paths = self.schema()["paths"]
        self.assertEqual(paths["/api/items"]["get"]["x-hikidashi-scope"], "read:library")
        self.assertEqual(
            paths["/api/items/{shortcode}/tag"]["put"]["x-hikidashi-scope"], "write:library"
        )
        self.assertEqual(paths["/api/worker/claim"]["post"]["security"], [{"workerToken": []}])
        self.assertEqual(paths["/api/auth/login"]["post"]["security"], [])

    def test_a_non_admin_sees_no_admin_or_worker_routes(self):
        self.user = BOB
        paths = self.schema()["paths"]
        self.assertIn("/api/items", paths)
        for p in (
            "/api/users",
            "/api/worker/claim",
            "/api/pipeline",
            "/api/logs",
            "/api/worker-tokens",
        ):
            self.assertNotIn(p, paths)
        self.assertNotIn("workerToken", self.schema()["components"]["securitySchemes"])
        self.user = ALICE
        self.assertIn("/api/users", self.schema()["paths"])

    def test_signed_out_gets_nothing(self):
        def nobody(request):
            raise self.main.HTTPException(401)

        with mock.patch.object(self.main, "current_user", nobody):
            self.assertEqual(self.client.get("/api/openapi.json").status_code, 401)
            r = self.client.get("/api/docs", follow_redirects=False)
            self.assertEqual((r.status_code, r.headers["location"]), (303, "/login?next=/api/docs"))

    def test_the_page_loads_nothing_from_elsewhere(self):
        page = self.client.get("/api/docs").text
        urls = re.findall(r"""(?:src|href)=["']([^"']+)""", page)
        self.assertIn("/explorer/swagger-ui-bundle.js", urls)
        self.assertFalse([u for u in urls if re.match(r"(?:https?:)?//", u)], urls)
        self.assertIn("persistAuthorization: false", page)
        self.assertNotIn("localStorage", page)

    def test_the_models_describe_real_responses(self):
        from pydantic import TypeAdapter

        from app.api.schemas import RESPONSES

        calls = {
            ("GET", "/api/items"): "/api/items?q=brake",
            ("GET", "/api/items/{shortcode}"): "/api/items/ALICE1",
            ("GET", "/api/items/{shortcode}/similar"): "/api/items/ALICE1/similar",
            ("GET", "/api/rediscover"): "/api/rediscover?md=01-01",
            ("GET", "/api/tags"): "/api/tags",
            ("GET", "/api/collections"): "/api/collections",
            ("GET", "/api/me"): "/api/me",
            ("GET", "/api/items/{shortcode}/recipe/translation"): (
                "/api/items/ALICE1/recipe/translation?lang=en"
            ),
        }
        for key, url in calls.items():
            r = self.client.get(url)
            self.assertEqual(r.status_code, 200, url)
            TypeAdapter(RESPONSES[key]).validate_python(r.json())

    def test_snapshot(self):
        """What the API offers, so a change shows up in review.
        UPDATE_SNAPSHOT=1 writes it again."""
        now = sorted(
            f"{m.upper()} {p} [{op['tags'][0]}] "
            f"{op.get('x-hikidashi-scope') or op['x-hikidashi-access']}"
            for p, m, op in self.ops(self.schema())
        )
        if os.environ.get("UPDATE_SNAPSHOT") or not SNAPSHOT.exists():
            SNAPSHOT.write_text(json.dumps(now, indent=1) + "\n", encoding="utf-8")
        self.assertEqual(now, json.loads(SNAPSHOT.read_text(encoding="utf-8")))
