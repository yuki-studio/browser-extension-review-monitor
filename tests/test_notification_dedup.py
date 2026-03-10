import unittest
from email.message import EmailMessage
import os

import monitor


class NotificationDedupTests(unittest.TestCase):
    def setUp(self) -> None:
        self.conn = monitor.db_connect(":memory:")
        monitor.init_db(self.conn)
        task_id, created = monitor.add_task(
            self.conn,
            store="chrome",
            plugin_name="n",
            detail_url=None,
            item_id="abc",
            version="1.0.0.3",
            submitted_at=monitor.iso(monitor.now_utc()),
            owner=None,
            operation_id=None,
            check_frequency_seconds=300,
            timeout_hours=72,
        )
        self.assertTrue(created)
        row = self.conn.execute(
            """
            SELECT
              id, store, item_id, product_id, version, submitted_at, status,
              plugin_name, detail_url,
              next_check_at, last_checked_at, check_frequency_seconds,
              timeout_hours, timeout_started_at, owner, operation_id
            FROM tasks
            WHERE id = ?
            """,
            (task_id,),
        ).fetchone()
        self.task = monitor.Task(**dict(row))

    def tearDown(self) -> None:
        self.conn.close()

    def test_transition_notify_only_once(self) -> None:
        # First time this transition appears, it should notify.
        self.assertTrue(
            monitor.should_notify(
                self.conn,
                self.task,
                old_status="PublishedPublic",
                status="PendingReview",
                effective_version="1003",
            )
        )
        monitor.record_notify(
            self.conn,
            self.task,
            old_status="PublishedPublic",
            status="PendingReview",
            effective_version="1003",
            channel="feishu",
        )
        # Same transition + same version should never be sent again.
        self.assertFalse(
            monitor.should_notify(
                self.conn,
                self.task,
                old_status="PublishedPublic",
                status="PendingReview",
                effective_version="1.0.0.3",
            )
        )

    def test_different_transition_or_version_can_notify(self) -> None:
        monitor.record_notify(
            self.conn,
            self.task,
            old_status="PublishedPublic",
            status="PendingReview",
            effective_version="1003",
            channel="feishu",
        )
        self.assertTrue(
            monitor.should_notify(
                self.conn,
                self.task,
                old_status="PendingReview",
                status="PublishedPublic",
                effective_version="1003",
            )
        )
        self.assertTrue(
            monitor.should_notify(
                self.conn,
                self.task,
                old_status="PublishedPublic",
                status="PendingReview",
                effective_version="1004",
            )
        )

    def test_legacy_notify_key_is_respected(self) -> None:
        legacy_key = monitor.build_legacy_notify_key(
            self.task, "PublishedPublic", "PendingReview", "1003"
        )
        self.conn.execute(
            "INSERT INTO notification_keys(k, sent_at) VALUES (?, ?)",
            (legacy_key, monitor.iso(monitor.now_utc())),
        )
        self.conn.commit()
        self.assertFalse(
            monitor.should_notify(
                self.conn,
                self.task,
                old_status="PublishedPublic",
                status="PendingReview",
                effective_version="1003",
            )
        )


class EdgeMailMatchTests(unittest.TestCase):
    def setUp(self) -> None:
        self._old_aliases = os.environ.get("EDGE_MAIL_NAME_ALIASES")

    def tearDown(self) -> None:
        if self._old_aliases is None:
            os.environ.pop("EDGE_MAIL_NAME_ALIASES", None)
        else:
            os.environ["EDGE_MAIL_NAME_ALIASES"] = self._old_aliases

    def test_match_by_plugin_name_without_item_id(self) -> None:
        task = monitor.Task(
            id=1,
            store="edge",
            plugin_name="StreamFab Netflix Downloader for Browser",
            detail_url=None,
            item_id="eboeciamjbjeobdhnikpddmpnijicmhg",
            product_id=None,
            version="1003",
            submitted_at=monitor.iso(monitor.now_utc()),
            status="Monitoring",
            next_check_at=None,
            last_checked_at=None,
            check_frequency_seconds=300,
            timeout_hours=72,
            timeout_started_at=None,
            owner=None,
            operation_id=None,
        )
        text = "Your product, StreamFab Netflix Downloader, has been successfully published"
        self.assertTrue(monitor.edge_mail_matches_task(text, task))

    def test_match_by_alias_when_mail_uses_short_name(self) -> None:
        os.environ["EDGE_MAIL_NAME_ALIASES"] = "eboeciamjbjeobdhnikpddmpnijicmhg:streamfab netflix|netflix downloader"
        task = monitor.Task(
            id=1,
            store="edge",
            plugin_name="StreamFab Netflix Downloader for Browser",
            detail_url=None,
            item_id="eboeciamjbjeobdhnikpddmpnijicmhg",
            product_id=None,
            version="1003",
            submitted_at=monitor.iso(monitor.now_utc()),
            status="Monitoring",
            next_check_at=None,
            last_checked_at=None,
            check_frequency_seconds=300,
            timeout_hours=72,
            timeout_started_at=None,
            owner=None,
            operation_id=None,
        )
        text = "Your product, StreamFab Netflix, has been successfully published"
        self.assertTrue(monitor.edge_mail_matches_task(text, task))

    def test_decode_html_mail_part(self) -> None:
        msg = EmailMessage()
        msg["Subject"] = "test"
        msg.set_content("<html><body><p>Your product has been successfully published</p></body></html>", subtype="html")
        body = monitor.decode_mail_part(msg)
        self.assertIn("successfully published", body.lower())

    def test_monitorfailed_can_recover_to_monitoring(self) -> None:
        conn = monitor.db_connect(":memory:")
        monitor.init_db(conn)
        task_id, _ = monitor.add_task(
            conn,
            store="edge",
            plugin_name="StreamFab Netflix Downloader for Browser",
            detail_url=None,
            item_id="eboeciamjbjeobdhnikpddmpnijicmhg",
            version="1003",
            submitted_at=monitor.iso(monitor.now_utc()),
            owner=None,
            operation_id=None,
            check_frequency_seconds=300,
            timeout_hours=72,
        )
        conn.execute("UPDATE tasks SET status='MonitorFailed' WHERE id = ?", (task_id,))
        conn.commit()
        row = conn.execute(
            """
            SELECT
              id, store, item_id, product_id, version, submitted_at, status,
              plugin_name, detail_url,
              next_check_at, last_checked_at, check_frequency_seconds,
              timeout_hours, timeout_started_at, owner, operation_id
            FROM tasks
            WHERE id = ?
            """,
            (task_id,),
        ).fetchone()
        task = monitor.Task(**dict(row))

        original_fetch = monitor.fetch_task_status
        try:
            monitor.fetch_task_status = lambda _task: ("Monitoring", "ok", "edge_email", None)
            monitor.handle_task(conn, task, webhook_url="", timeout_poll_seconds=7200, timeout_followup_days=7)
        finally:
            monitor.fetch_task_status = original_fetch

        new_status = conn.execute("SELECT status FROM tasks WHERE id = ?", (task_id,)).fetchone()["status"]
        self.assertEqual(new_status, "Monitoring")
        conn.close()

    def test_edge_no_match_alert_is_bucket_deduped(self) -> None:
        conn = monitor.db_connect(":memory:")
        monitor.init_db(conn)
        old_sender = monitor.deliver_feishu_card
        sent = {"count": 0}
        try:
            monitor.deliver_feishu_card = lambda _webhook, _card: (sent.__setitem__("count", sent["count"] + 1) or True, "ok")
            now = monitor.now_utc()
            task_id, _ = monitor.add_task(
                conn,
                store="edge",
                plugin_name="StreamFab Netflix Downloader for Browser",
                detail_url=None,
                item_id="eboeciamjbjeobdhnikpddmpnijicmhg",
                version="1003",
                submitted_at=monitor.iso(now - monitor.dt.timedelta(hours=36)),
                owner=None,
                operation_id=None,
                check_frequency_seconds=300,
                timeout_hours=72,
            )
            row = conn.execute(
                """
                SELECT
                  id, store, item_id, product_id, version, submitted_at, status,
                  plugin_name, detail_url,
                  next_check_at, last_checked_at, check_frequency_seconds,
                  timeout_hours, timeout_started_at, owner, operation_id
                FROM tasks
                WHERE id = ?
                """,
                (task_id,),
            ).fetchone()
            task = monitor.Task(**dict(row))
            monitor.maybe_send_edge_no_match_alert(
                conn,
                webhook_url="",
                task=task,
                now=now,
                detail="edge email: no matching result yet",
                alert_hours=24,
                repeat_hours=24,
            )
            monitor.maybe_send_edge_no_match_alert(
                conn,
                webhook_url="",
                task=task,
                now=now + monitor.dt.timedelta(hours=1),
                detail="edge email: no matching result yet",
                alert_hours=24,
                repeat_hours=24,
            )
            self.assertEqual(sent["count"], 1)
        finally:
            monitor.deliver_feishu_card = old_sender
            conn.close()


class EdgeApiPublishTests(unittest.TestCase):
    def test_extract_operation_id_from_location(self) -> None:
        location = "https://manage.devcenter.microsoft.com/v1.0/my/products/p/submissions/operations/abc123"
        self.assertEqual(monitor.extract_operation_id_from_location(location), "abc123")

    def test_publish_success_is_not_treated_as_approved(self) -> None:
        status, reason = monitor.normalize_edge_operation_status({"status": "Succeeded", "message": "ok"})
        self.assertEqual(status, "Monitoring")
        self.assertIn("waiting for review result", reason)

    def test_registered_edge_product_id_is_reused_for_task(self) -> None:
        conn = monitor.db_connect(":memory:")
        monitor.init_db(conn)
        monitor.upsert_plugin(
            conn,
            store="edge",
            item_id="eboeciamjbjeobdhnikpddmpnijicmhg",
            product_id="12345678-1234-1234-1234-1234567890ab",
            plugin_name="StreamFab Netflix Downloader for Browser",
            detail_url=None,
        )
        task_id, created = monitor.add_task(
            conn,
            store="edge",
            plugin_name=None,
            detail_url=None,
            item_id="eboeciamjbjeobdhnikpddmpnijicmhg",
            version="1003",
            submitted_at=monitor.iso(monitor.now_utc()),
            owner=None,
            operation_id="op-1",
            check_frequency_seconds=300,
            timeout_hours=72,
        )
        self.assertTrue(created)
        row = conn.execute("SELECT product_id FROM tasks WHERE id = ?", (task_id,)).fetchone()
        self.assertEqual(row["product_id"], "12345678-1234-1234-1234-1234567890ab")
        conn.close()


class EdgeNotificationPolicyTests(unittest.TestCase):
    def test_edge_timeout_monitoring_does_not_send_status_push(self) -> None:
        conn = monitor.db_connect(":memory:")
        monitor.init_db(conn)
        task_id, _ = monitor.add_task(
            conn,
            store="edge",
            plugin_name="StreamFab Video Downloader for Browser",
            detail_url=None,
            item_id="bgfbcbkjjndjeamckkakgkiphdhlmbip",
            version="1009",
            submitted_at=monitor.iso(monitor.now_utc() - monitor.dt.timedelta(hours=80)),
            owner=None,
            operation_id=None,
            check_frequency_seconds=300,
            timeout_hours=72,
            product_id="866bad86-33d0-4a50-8a90-ec419502d110",
        )
        row = conn.execute(
            """
            SELECT
              id, store, item_id, product_id, version, submitted_at, status,
              plugin_name, detail_url,
              next_check_at, last_checked_at, check_frequency_seconds,
              timeout_hours, timeout_started_at, owner, operation_id
            FROM tasks
            WHERE id = ?
            """,
            (task_id,),
        ).fetchone()
        task = monitor.Task(**dict(row))

        original_notify = monitor.notify_feishu
        called = {"count": 0}
        try:
            def fake_notify(*args, **kwargs):
                called["count"] += 1
                return True, "ok"

            monitor.notify_feishu = fake_notify
            monitor.handle_task(conn, task, webhook_url="", timeout_poll_seconds=7200, timeout_followup_days=7)
        finally:
            monitor.notify_feishu = original_notify

        new_status = conn.execute("SELECT status FROM tasks WHERE id = ?", (task_id,)).fetchone()["status"]
        self.assertEqual(new_status, "TimeoutMonitoring")
        self.assertEqual(called["count"], 0)
        notif_count = conn.execute("SELECT COUNT(*) AS c FROM notifications WHERE task_id = ?", (task_id,)).fetchone()["c"]
        self.assertEqual(notif_count, 0)
        conn.close()


if __name__ == "__main__":
    unittest.main()
