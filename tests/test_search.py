"""HTTP contract and SQL filtering for nearby search; no production DB access."""
import unittest
import json
from urllib.parse import urlencode
from unittest.mock import AsyncMock, Mock

from fastapi import FastAPI
from sqlalchemy.dialects import postgresql

from app.api.search_router import router
from app.database import get_db
from app.models import Object, ObjectType


class NearbyTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.db = AsyncMock()
        self.db.execute.return_value = Mock()
        self.db.execute.return_value.all.return_value = []
        app = FastAPI()
        app.include_router(router)
        app.dependency_overrides[get_db] = lambda: self.db
        self.app = app

    async def get(self, params):
        messages = []

        async def receive():
            return {"type": "http.request", "body": b"", "more_body": False}

        async def send(message):
            messages.append(message)

        await self.app({"type": "http", "asgi": {"version": "3.0"},
            "http_version": "1.1", "method": "GET", "scheme": "http",
            "path": "/search/nearby", "raw_path": b"/search/nearby",
            "query_string": urlencode(params).encode(), "headers": [],
            "client": ("127.0.0.1", 1), "server": ("test", 80),
            "root_path": ""}, receive, send)
        status = next(m["status"] for m in messages if m["type"] == "http.response.start")
        body = b"".join(m.get("body", b"") for m in messages if m["type"] == "http.response.body")
        return status, json.loads(body)

    async def test_multiple_types_filter_before_limit(self):
        status, body = await self.get([
            ('lat', 47), ('lon', 8), ('types', 'distribution_box'),
            ('types', 'disconnect_point'), ('limit', 1)])
        self.assertEqual(status, 200)
        stmt = self.db.execute.call_args.args[0]
        sql = str(stmt.compile(dialect=postgresql.dialect(),
                               compile_kwargs={'literal_binds': True}))
        self.assertIn("objects.type IN ('distribution_box', 'disconnect_point')", sql)
        self.assertLess(sql.index('objects.type IN'), sql.index('LIMIT'))

    async def test_unfiltered_includes_disconnect_point(self):
        obj = Object(id='point1', name='Point1', type=ObjectType.disconnect_point)
        self.db.execute.return_value.all.return_value = [(obj, 8, 47, 10)]
        status, body = await self.get([('lat', 47), ('lon', 8)])
        self.assertEqual(status, 200)
        self.assertEqual(body[0]['type'], 'disconnect_point')
        sql = str(self.db.execute.call_args.args[0])
        self.assertNotIn('objects.type IN', sql)

    async def test_invalid_type_rejected_before_database_access(self):
        status, body = await self.get([('lat', 47), ('lon', 8), ('types', 'station')])
        self.assertEqual(status, 422)
        self.db.execute.assert_not_called()
