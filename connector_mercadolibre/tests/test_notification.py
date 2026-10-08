# Copyright 2026 Odoo Community Association (OCA)
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from .common import MercadoLibreCommon


class TestMercadoLibreNotification(MercadoLibreCommon):
    def test_orders_notification_is_stored_once(self):
        self.backend.seller_id = str(self.notification_payload["user_id"])
        first = self.env["mercadolibre.notification"].create_from_payload(
            self.backend, self.notification_payload
        )
        second = self.env["mercadolibre.notification"].create_from_payload(
            self.backend, self.notification_payload
        )
        jobs = self.env["queue.job"].search(
            [
                ("model_name", "=", "mercadolibre.notification"),
                ("method_name", "=", "_job_dispatch"),
            ]
        )
        self.assertEqual(first, second)
        self.assertEqual(first.state, "enqueued")
        self.assertEqual(first.resource, "/orders/2195160686")
        self.assertEqual(first.actions, "order")
        self.assertEqual(
            self.env["mercadolibre.notification"].search_count([]),
            1,
        )
        self.assertEqual(len(jobs), 1)
        self.assertEqual(
            jobs.identity_key,
            f"notify:{self.backend.id}:orders_v2:/orders/2195160686",
        )

    def test_other_seller_is_stored_skipped(self):
        self.backend.seller_id = "1"
        payload = dict(self.notification_payload, user_id=2)
        notification = self.env["mercadolibre.notification"].create_from_payload(
            self.backend, payload
        )
        jobs = self.env["queue.job"].search([("method_name", "=", "_job_dispatch")])
        self.assertEqual(notification.state, "skipped")
        self.assertFalse(jobs)

    def test_unknown_topic_is_skipped(self):
        self.backend.seller_id = str(self.notification_payload["user_id"])
        payload = dict(self.notification_payload, topic="questions")
        notification = (
            self.env["mercadolibre.notification"]
            .with_context(queue_job__no_delay=True)
            .create_from_payload(self.backend, payload)
        )
        self.assertEqual(notification.state, "skipped")
        self.assertFalse(notification.error_message)

    def test_receive_ignores_unknown_backend_and_invalid_payload(self):
        notifications = self.env["mercadolibre.notification"]
        self.assertFalse(notifications.receive(999999, self.notification_payload))
        self.assertFalse(notifications.receive(self.backend.id, None))
        self.assertFalse(
            notifications.receive(self.backend.id, ["not", "an", "object"])
        )
        self.assertFalse(notifications.search([]))

    def test_requeue_enqueues_again(self):
        self.backend.seller_id = str(self.notification_payload["user_id"])
        payload = dict(self.notification_payload, topic="questions")
        notification = (
            self.env["mercadolibre.notification"]
            .with_context(queue_job__no_delay=True)
            .create_from_payload(self.backend, payload)
        )
        self.assertEqual(notification.state, "skipped")
        notification = self.env["mercadolibre.notification"].browse(notification.id)
        notification.button_requeue()
        self.assertEqual(notification.state, "enqueued")
        jobs = self.env["queue.job"].search([("method_name", "=", "_job_dispatch")])
        self.assertEqual(len(jobs), 1)
