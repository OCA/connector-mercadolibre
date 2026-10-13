# Copyright 2026 Odoo Community Association (OCA)
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo.addons.queue_job.exception import RetryableJobError

from .common import FakeResponse, MercadoLibreCommon, Transport


class TestMercadoLibreAdapter(MercadoLibreCommon):
    def test_unauthorized_refreshes_once_and_retries(self):
        self.backend.write(
            {
                "access_token": "old-access",
                "refresh_token": "old-refresh",
                "state": "connected",
            }
        )
        transport = Transport(
            [
                FakeResponse(
                    401,
                    {"message": "invalid_token", "error": "unauthorized"},
                ),
                FakeResponse(
                    200,
                    {
                        "access_token": "new-access",
                        "refresh_token": "new-refresh",
                        "expires_in": 21600,
                    },
                ),
                FakeResponse(200, {"id": "MLA1"}),
            ]
        )
        backend = self.backend.with_context(mercadolibre_transport=transport)
        with backend.work_on("mercadolibre.backend") as work:
            payload = work.component(usage="backend.adapter").read("/items/MLA1")
        self.assertEqual(payload["id"], "MLA1")
        self.assertEqual(len(transport.calls), 3)
        self.assertEqual(
            transport.calls[0]["headers"]["Authorization"],
            "Bearer old-access",
        )
        self.assertEqual(
            transport.calls[1]["data"]["grant_type"],
            "refresh_token",
        )
        self.assertEqual(transport.calls[1]["data"]["refresh_token"], "old-refresh")
        self.assertNotIn("Authorization", transport.calls[1]["headers"])
        self.assertEqual(
            transport.calls[2]["headers"]["Authorization"],
            "Bearer new-access",
        )
        self.assertEqual(self.backend.access_token, "new-access")

    def test_too_many_requests_is_retryable(self):
        self.backend.access_token = "old-access"
        transport = Transport(
            [
                FakeResponse(
                    429,
                    {"message": "too many requests"},
                    headers={"Retry-After": "15"},
                )
            ]
        )
        backend = self.backend.with_context(mercadolibre_transport=transport)
        with backend.work_on("mercadolibre.backend") as work:
            adapter = work.component(usage="backend.adapter")
            with self.assertRaises(RetryableJobError) as caught:
                adapter.read("/items/MLA1")
        self.assertEqual(caught.exception.seconds, 15)
        self.assertNotIn("old-access", str(caught.exception))
        self.assertEqual(len(transport.calls), 1)

    def test_search_sends_scan_cursor(self):
        self.backend.access_token = "old-access"
        transport = Transport([FakeResponse(200, {"results": [], "scroll_id": "s2"})])
        backend = self.backend.with_context(mercadolibre_transport=transport)
        with backend.work_on("mercadolibre.backend") as work:
            work.component(usage="backend.adapter").search(
                "/orders/search",
                params={"seller": "123"},
                search_type="scan",
                scroll_id="s1",
                limit=50,
            )
        query = transport.calls[0]["params"]
        self.assertEqual(query["seller"], "123")
        self.assertEqual(query["search_type"], "scan")
        self.assertEqual(query["scroll_id"], "s1")
        self.assertEqual(query["limit"], 50)
