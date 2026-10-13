# Copyright 2026 Odoo Community Association (OCA)
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

import base64
import hashlib
from urllib.parse import parse_qs, urlparse

from odoo.exceptions import AccessError, UserError
from odoo.tests.common import new_test_user

from odoo.addons.queue_job.exception import RetryableJobError

from .common import FakeResponse, MercadoLibreCommon, Transport


class TestMercadoLibreOAuth(MercadoLibreCommon):
    def test_exchange_code_stores_seller(self):
        self.backend.code_verifier = "verifier-1"
        transport = Transport(
            [
                FakeResponse(200, self.token_payload),
                FakeResponse(200, self.user_payload),
            ]
        )
        backend = self.backend.with_context(mercadolibre_transport=transport)
        backend._job_exchange_code("auth-code")

        self.assertEqual(backend.access_token, "APP_USR-123")
        self.assertEqual(backend.refresh_token, "TG-REFRESH-1")
        self.assertEqual(backend.seller_id, "123456789")
        self.assertEqual(backend.nickname, "SELLER_NICK")
        self.assertEqual(backend.versions, "user_product")
        self.assertEqual(backend.state, "connected")
        self.assertFalse(backend.code_verifier)
        self.assertEqual(transport.calls[0]["data"]["code"], "auth-code")
        self.assertEqual(transport.calls[0]["data"]["code_verifier"], "verifier-1")
        self.assertEqual(
            transport.calls[1]["headers"]["Authorization"],
            "Bearer APP_USR-123",
        )

    def test_exchange_without_user_product_tag_uses_legacy_items(self):
        user_payload = dict(self.user_payload, tags=["normal"])
        transport = Transport(
            [
                FakeResponse(200, self.token_payload),
                FakeResponse(200, user_payload),
            ]
        )
        backend = self.backend.with_context(mercadolibre_transport=transport)
        backend._job_exchange_code("auth-code")
        self.assertEqual(backend.versions, "legacy_item")

    def test_refresh_rotates_single_use_token(self):
        self.backend.write(
            {
                "refresh_token": "r0",
                "access_token": "a0",
                "state": "connected",
            }
        )
        transport = Transport(
            [
                FakeResponse(
                    200,
                    {
                        "access_token": "a1",
                        "refresh_token": "r1",
                        "expires_in": 21600,
                    },
                ),
                FakeResponse(
                    200,
                    {
                        "access_token": "a2",
                        "refresh_token": "r2",
                        "expires_in": 21600,
                    },
                ),
            ]
        )
        queries = []
        cursor = self.env.cr
        original = cursor.execute

        def wrapped(query, *args, **kwargs):
            queries.append(str(query))
            return original(query, *args, **kwargs)

        cursor.execute = wrapped
        try:
            backend = self.backend.with_context(mercadolibre_transport=transport)
            backend._job_refresh_token()
            backend._job_refresh_token()
        finally:
            cursor.execute = original

        self.assertEqual(transport.calls[0]["data"]["refresh_token"], "r0")
        self.assertEqual(transport.calls[1]["data"]["refresh_token"], "r1")
        self.assertEqual(self.backend.refresh_token, "r2")
        self.assertEqual(self.backend.access_token, "a2")
        self.assertEqual(len(transport.calls), 2)
        self.assertTrue(any("FOR UPDATE" in query for query in queries))

    def test_invalid_grant_sets_error_without_retry(self):
        self.backend.write(
            {
                "refresh_token": "r0",
                "access_token": "a0",
                "state": "connected",
            }
        )
        transport = Transport(
            [
                FakeResponse(
                    400,
                    {
                        "error": "invalid_grant",
                        "message": "invalid refresh token",
                    },
                )
            ]
        )
        before = self.env["queue.job"].search_count([])
        backend = self.backend.with_context(mercadolibre_transport=transport)
        try:
            backend._job_refresh_token()
        except RetryableJobError:
            self.fail("invalid_grant must not be retried")
        self.assertEqual(backend.state, "error")
        self.assertIn("invalid_grant", backend.auth_error)
        self.assertEqual(self.env["queue.job"].search_count([]), before)

    def test_button_connect_stores_pkce_challenge(self):
        action = self.backend.button_connect()
        parsed = urlparse(action["url"])
        query = parse_qs(parsed.query)
        digest = hashlib.sha256(self.backend.code_verifier.encode("ascii")).digest()
        challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
        self.assertEqual(parsed.netloc, "auth.mercadolibre.com")
        self.assertEqual(query["code_challenge"], [challenge])
        self.assertEqual(query["code_challenge_method"], ["S256"])
        self.assertEqual(query["state"], [str(self.backend.id)])
        self.assertTrue(self.backend.code_verifier)

    def test_disconnect_clears_tokens(self):
        self.backend.write(
            {
                "seller_id": "123",
                "nickname": "SELLER_NICK",
                "access_token": "a0",
                "refresh_token": "r0",
                "state": "connected",
                "auth_error": "previous",
            }
        )
        self.backend.button_disconnect()
        self.assertEqual(self.backend.state, "draft")
        self.assertFalse(self.backend.seller_id)
        self.assertFalse(self.backend.access_token)
        self.assertFalse(self.backend.refresh_token)
        self.assertFalse(self.backend.auth_error)

    def test_site_is_frozen_after_the_seller_is_known(self):
        self.backend.seller_id = "123"
        with self.assertRaises(UserError):
            self.backend.write({"site_id": "MLB"})

    def test_user_cannot_read_client_secret(self):
        user = new_test_user(
            self.env,
            login="ml_user",
            groups="base.group_user,connector_mercadolibre.group_mercadolibre_user",
        )
        with self.assertRaises(AccessError):
            self.backend.with_user(user).read(["client_secret"])
