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

    def test_chrome_pending_review_message_uses_version_aware_text(self) -> None:
        sent = {}
        original_sender = monitor.deliver_feishu_card
        try:
            def fake_sender(_webhook_url, card):
                sent["card"] = card
                return True, "ok"

            monitor.deliver_feishu_card = fake_sender
            ok, _ = monitor.notify_feishu(
                webhook_url="",
                task=self.task,
                old_status="PublishedPublic",
                new_status="PendingReview",
                detail="chrome api chrome submitted state=PENDING_REVIEW; submitted_version=1.0.0.4; published state=PUBLISHED; published_version=1.0.0.3",
                effective_version="1.0.0.4",
                changed_at=monitor.iso(monitor.now_utc()),
            )
            self.assertTrue(ok)
        finally:
            monitor.deliver_feishu_card = original_sender

        body = sent["card"]["elements"][0]["content"]
        self.assertIn("v1.0.0.3 PublishedPublic -> v1.0.0.4 PendingReview", body)
        self.assertIn("**Version**: 1.0.0.4", body)
        self.assertNotIn("PublishedPublic -> PendingReview", body)

    def test_chrome_notification_uses_inferred_detail_url_when_missing(self) -> None:
        sent = {}
        original_sender = monitor.deliver_feishu_card
        try:
            def fake_sender(_webhook_url, card):
                sent["card"] = card
                return True, "ok"

            monitor.deliver_feishu_card = fake_sender
            ok, _ = monitor.notify_feishu(
                webhook_url="",
                task=self.task,
                old_status="PendingReview",
                new_status="PublishedPublic",
                detail="chrome api chrome published state=PUBLISHED; published_version=1.0.0.4",
                effective_version="1.0.0.4",
                changed_at=monitor.iso(monitor.now_utc()),
            )
            self.assertTrue(ok)
        finally:
            monitor.deliver_feishu_card = original_sender

        actions = sent["card"]["elements"][-1]["actions"]
        self.assertEqual(
            actions[0]["url"],
            "https://chromewebstore.google.com/detail/n/abc?authuser=0&hl=en",
        )

    def test_chrome_terminal_notification_hides_timeout_transition(self) -> None:
        sent = {}
        original_sender = monitor.deliver_feishu_card
        try:
            def fake_sender(_webhook_url, card):
                sent["card"] = card
                return True, "ok"

            monitor.deliver_feishu_card = fake_sender
            ok, _ = monitor.notify_feishu(
                webhook_url="",
                task=self.task,
                old_status="PendingReview",
                new_status="PublishedPublic",
                detail="chrome api chrome published state=PUBLISHED; published_version=1.0.0.4",
                effective_version="1.0.0.4",
                changed_at=monitor.iso(monitor.now_utc()),
            )
            self.assertTrue(ok)
        finally:
            monitor.deliver_feishu_card = original_sender

        body = sent["card"]["elements"][0]["content"]
        self.assertIn("**Status Update**: 🟢 PublishedPublic", body)
        self.assertNotIn("PendingReview -> PublishedPublic", body)
        self.assertNotIn("TimeoutMonitoring -> PublishedPublic", body)


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

    def test_cleanup_duplicate_active_tasks_keeps_newest(self) -> None:
        conn = monitor.db_connect(":memory:")
        monitor.init_db(conn)
        older_id, _ = monitor.add_task(
            conn,
            store="edge",
            plugin_name="StreamFab Disney Plus Downloader",
            detail_url=None,
            item_id="dup-item",
            version="1.0.0.1",
            submitted_at=monitor.iso(monitor.now_utc()),
            owner=None,
            operation_id=None,
            check_frequency_seconds=300,
            timeout_hours=72,
            allow_duplicate=True,
        )
        newer_id, _ = monitor.add_task(
            conn,
            store="edge",
            plugin_name="StreamFab Disney Plus Downloader",
            detail_url=None,
            item_id="dup-item",
            version="1001",
            submitted_at=monitor.iso(monitor.now_utc()),
            owner=None,
            operation_id=None,
            check_frequency_seconds=300,
            timeout_hours=72,
            allow_duplicate=True,
        )

        monitor.cleanup_duplicate_tasks(conn)

        rows = conn.execute("SELECT id, status, next_check_at FROM tasks WHERE item_id = ? ORDER BY id ASC", ("dup-item",)).fetchall()
        self.assertEqual(rows[0]["id"], older_id)
        self.assertEqual(rows[0]["status"], "Cancelled")
        self.assertIsNone(rows[0]["next_check_at"])
        self.assertEqual(rows[1]["id"], newer_id)
        self.assertEqual(rows[1]["status"], "Monitoring")
        conn.close()

    def test_cleanup_duplicate_active_task_inherits_existing_terminal_status(self) -> None:
        conn = monitor.db_connect(":memory:")
        monitor.init_db(conn)
        terminal_id, _ = monitor.add_task(
            conn,
            store="edge",
            plugin_name="StreamFab Disney Plus Downloader",
            detail_url=None,
            item_id="dup-terminal",
            version="1.0.0.1",
            submitted_at=monitor.iso(monitor.now_utc()),
            owner=None,
            operation_id=None,
            check_frequency_seconds=300,
            timeout_hours=72,
        )
        conn.execute("UPDATE tasks SET status='Approved', next_check_at=NULL WHERE id = ?", (terminal_id,))
        conn.commit()
        active_id, _ = monitor.add_task(
            conn,
            store="edge",
            plugin_name="StreamFab Disney Plus Downloader",
            detail_url=None,
            item_id="dup-terminal",
            version="1001",
            submitted_at=monitor.iso(monitor.now_utc()),
            owner=None,
            operation_id=None,
            check_frequency_seconds=300,
            timeout_hours=72,
            allow_duplicate=True,
        )

        monitor.cleanup_duplicate_tasks(conn)

        active_row = conn.execute("SELECT id, status, next_check_at FROM tasks WHERE id = ?", (active_id,)).fetchone()
        self.assertEqual(active_row["status"], "Approved")
        self.assertIsNone(active_row["next_check_at"])
        conn.close()

    def test_cleanup_duplicate_terminal_tasks_prefers_final_result_over_timeoutclosed(self) -> None:
        conn = monitor.db_connect(":memory:")
        monitor.init_db(conn)
        timeout_id, _ = monitor.add_task(
            conn,
            store="chrome",
            plugin_name="StreamFab Netflix Downloader for Browser",
            detail_url=None,
            item_id="dup-terminal-chrome",
            version="1.0.0.4",
            submitted_at=monitor.iso(monitor.now_utc()),
            owner=None,
            operation_id=None,
            check_frequency_seconds=300,
            timeout_hours=72,
            allow_duplicate=True,
        )
        published_id, _ = monitor.add_task(
            conn,
            store="chrome",
            plugin_name="StreamFab Netflix Downloader for Browser",
            detail_url=None,
            item_id="dup-terminal-chrome",
            version="1004",
            submitted_at=monitor.iso(monitor.now_utc()),
            owner=None,
            operation_id=None,
            check_frequency_seconds=300,
            timeout_hours=72,
            allow_duplicate=True,
        )
        conn.execute(
            "UPDATE tasks SET status='TimeoutClosed', next_check_at=NULL, timeout_started_at=? WHERE id = ?",
            (monitor.iso(monitor.now_utc() - monitor.dt.timedelta(hours=1)), timeout_id),
        )
        conn.execute(
            "UPDATE tasks SET status='PublishedPublic', next_check_at=NULL, timeout_started_at=? WHERE id = ?",
            (monitor.iso(monitor.now_utc() - monitor.dt.timedelta(hours=2)), published_id),
        )
        conn.commit()

        monitor.cleanup_duplicate_tasks(conn)

        rows = conn.execute(
            "SELECT id, status, timeout_started_at FROM tasks WHERE item_id = ? ORDER BY id ASC",
            ("dup-terminal-chrome",),
        ).fetchall()
        self.assertEqual(rows[0]["id"], timeout_id)
        self.assertEqual(rows[0]["status"], "PublishedPublic")
        self.assertIsNone(rows[0]["timeout_started_at"])
        self.assertEqual(rows[1]["id"], published_id)
        self.assertEqual(rows[1]["status"], "PublishedPublic")
        self.assertIsNone(rows[1]["timeout_started_at"])
        conn.close()

    def test_edge_no_match_alert_repeats_only_after_full_interval(self) -> None:
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
            monitor.maybe_send_edge_no_match_alert(
                conn,
                webhook_url="",
                task=task,
                now=now + monitor.dt.timedelta(hours=24, minutes=1),
                detail="edge email: no matching result yet",
                alert_hours=24,
                repeat_hours=24,
            )
            self.assertEqual(sent["count"], 2)
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

    def test_ensure_edge_watch_tasks_does_not_recreate_after_terminal_status(self) -> None:
        conn = monitor.db_connect(":memory:")
        monitor.init_db(conn)
        monitor.upsert_plugin(
            conn,
            store="edge",
            item_id="bgfbcbkjjndjeamckkakgkiphdhlmbip",
            product_id="866bad86-33d0-4a50-8a90-ec419502d110",
            plugin_name="StreamFab Video Downloader for Browser",
            detail_url=None,
        )
        task_id, created = monitor.add_task(
            conn,
            store="edge",
            plugin_name="StreamFab Video Downloader for Browser",
            detail_url=None,
            item_id="bgfbcbkjjndjeamckkakgkiphdhlmbip",
            version="1009",
            submitted_at=monitor.iso(monitor.now_utc()),
            owner=None,
            operation_id=None,
            check_frequency_seconds=300,
            timeout_hours=72,
            product_id="866bad86-33d0-4a50-8a90-ec419502d110",
        )
        self.assertTrue(created)
        conn.execute("UPDATE tasks SET status = 'Approved', next_check_at = NULL WHERE id = ?", (task_id,))
        conn.commit()

        monitor.ensure_edge_watch_tasks(conn)

        count = conn.execute(
            "SELECT COUNT(*) AS c FROM tasks WHERE store = 'edge' AND item_id = ?",
            ("bgfbcbkjjndjeamckkakgkiphdhlmbip",),
        ).fetchone()["c"]
        self.assertEqual(count, 1)
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


class ChromeDiscoveryTests(unittest.TestCase):
    def test_ensure_chrome_watch_tasks_creates_task_for_new_submitted_version(self) -> None:
        conn = monitor.db_connect(":memory:")
        monitor.init_db(conn)
        monitor.upsert_plugin(
            conn,
            store="chrome",
            item_id="glcbkndciojfeepepdoeofgpojigcdmf",
            product_id=None,
            plugin_name="StreamFab Netflix Downloader for Browser",
            detail_url=None,
        )

        original_fetch = monitor.fetch_chrome_item_snapshot
        try:
            monitor.fetch_chrome_item_snapshot = lambda _item_id: (
                {
                    "submitted_state": "PENDING_REVIEW",
                    "submitted_version": "1004",
                    "published_state": "PUBLISHED",
                    "published_version": "1003",
                    "status": "PendingReview",
                    "reason": "chrome submitted state=PENDING_REVIEW; submitted_version=1004",
                    "observed_version": "1004",
                },
                "chrome api ok",
            )
            monitor.ensure_chrome_watch_tasks(conn)
        finally:
            monitor.fetch_chrome_item_snapshot = original_fetch

        row = conn.execute(
            "SELECT store, item_id, version, status FROM tasks WHERE item_id = ? ORDER BY id DESC LIMIT 1",
            ("glcbkndciojfeepepdoeofgpojigcdmf",),
        ).fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(row["store"], "chrome")
        self.assertEqual(monitor.canonical_version(row["version"]), "1004")
        self.assertEqual(row["status"], "Monitoring")
        conn.close()

    def test_ensure_chrome_watch_tasks_skips_already_public_same_version(self) -> None:
        conn = monitor.db_connect(":memory:")
        monitor.init_db(conn)
        monitor.upsert_plugin(
            conn,
            store="chrome",
            item_id="glcbkndciojfeepepdoeofgpojigcdmf",
            product_id=None,
            plugin_name="StreamFab Netflix Downloader for Browser",
            detail_url=None,
        )

        original_fetch = monitor.fetch_chrome_item_snapshot
        try:
            monitor.fetch_chrome_item_snapshot = lambda _item_id: (
                {
                    "submitted_state": "PUBLISHED",
                    "submitted_version": "1004",
                    "published_state": "PUBLISHED",
                    "published_version": "1004",
                    "status": "PublishedPublic",
                    "reason": "chrome published state=PUBLISHED; published_version=1004",
                    "observed_version": "1004",
                },
                "chrome api ok",
            )
            monitor.ensure_chrome_watch_tasks(conn)
        finally:
            monitor.fetch_chrome_item_snapshot = original_fetch

        row = conn.execute(
            "SELECT COUNT(*) AS c FROM tasks WHERE item_id = ?",
            ("glcbkndciojfeepepdoeofgpojigcdmf",),
        ).fetchone()
        self.assertEqual(row["c"], 0)
        conn.close()

    def test_chrome_pending_review_sends_status_push(self) -> None:
        conn = monitor.db_connect(":memory:")
        monitor.init_db(conn)
        task_id, _ = monitor.add_task(
            conn,
            store="chrome",
            plugin_name="StreamFab Netflix Downloader for Browser",
            detail_url=None,
            item_id="glcbkndciojfeepepdoeofgpojigcdmf",
            version="1004",
            submitted_at=monitor.iso(monitor.now_utc()),
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

        original_fetch = monitor.fetch_task_status
        original_notify = monitor.notify_feishu
        called = {"count": 0}
        try:
            monitor.fetch_task_status = lambda _task: ("PendingReview", "chrome api pending", "chrome_api", "1004")

            def fake_notify(*args, **kwargs):
                called["count"] += 1
                return True, "ok"

            monitor.notify_feishu = fake_notify
            monitor.handle_task(conn, task, webhook_url="", timeout_poll_seconds=7200, timeout_followup_days=7)
        finally:
            monitor.fetch_task_status = original_fetch
            monitor.notify_feishu = original_notify

        new_status = conn.execute("SELECT status FROM tasks WHERE id = ?", (task_id,)).fetchone()["status"]
        self.assertEqual(new_status, "PendingReview")
        self.assertEqual(called["count"], 1)
        notif_count = conn.execute("SELECT COUNT(*) AS c FROM notifications WHERE task_id = ?", (task_id,)).fetchone()["c"]
        self.assertEqual(notif_count, 1)
        conn.close()

    def test_chrome_pending_review_is_held_when_publish_signal_is_too_early(self) -> None:
        conn = monitor.db_connect(":memory:")
        monitor.init_db(conn)
        task_id, _ = monitor.add_task(
            conn,
            store="chrome",
            plugin_name="StreamFab Netflix Downloader for Browser",
            detail_url=None,
            item_id="glcbkndciojfeepepdoeofgpojigcdmf",
            version="1.0.0.4",
            submitted_at=monitor.iso(monitor.now_utc() - monitor.dt.timedelta(minutes=10)),
            owner=None,
            operation_id=None,
            check_frequency_seconds=300,
            timeout_hours=72,
        )
        conn.execute("UPDATE tasks SET status = 'PendingReview' WHERE id = ?", (task_id,))
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
        original_notify = monitor.notify_feishu
        called = {"count": 0}
        try:
            monitor.fetch_task_status = lambda _task: (
                "PublishedPublic",
                "chrome api chrome published state=PUBLISHED; published_version=1.0.0.4",
                "chrome_api",
                "1.0.0.4",
            )

            def fake_notify(*args, **kwargs):
                called["count"] += 1
                return True, "ok"

            monitor.notify_feishu = fake_notify
            monitor.handle_task(conn, task, webhook_url="", timeout_poll_seconds=7200, timeout_followup_days=7)
        finally:
            monitor.fetch_task_status = original_fetch
            monitor.notify_feishu = original_notify

        row2 = conn.execute(
            "SELECT status, next_check_at FROM tasks WHERE id = ?",
            (task_id,),
        ).fetchone()
        self.assertEqual(row2["status"], "PendingReview")
        self.assertIsNotNone(row2["next_check_at"])
        self.assertEqual(called["count"], 0)
        conn.close()

    def test_chrome_recent_pending_review_can_revert_from_publishedpublic(self) -> None:
        conn = monitor.db_connect(":memory:")
        monitor.init_db(conn)
        task_id, _ = monitor.add_task(
            conn,
            store="chrome",
            plugin_name="StreamFab Netflix Downloader for Browser",
            detail_url=None,
            item_id="glcbkndciojfeepepdoeofgpojigcdmf",
            version="1.0.0.4",
            submitted_at=monitor.iso(monitor.now_utc() - monitor.dt.timedelta(minutes=20)),
            owner=None,
            operation_id=None,
            check_frequency_seconds=300,
            timeout_hours=72,
        )
        conn.execute("UPDATE tasks SET status = 'PublishedPublic' WHERE id = ?", (task_id,))
        conn.execute(
            "INSERT INTO status_events(task_id, status, source, detail, event_time) VALUES (?, ?, ?, ?, ?)",
            (task_id, "PendingReview", "chrome_api", "earlier pending", monitor.iso(monitor.now_utc() - monitor.dt.timedelta(minutes=5))),
        )
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
        original_notify = monitor.notify_feishu
        called = {"count": 0}
        try:
            monitor.fetch_task_status = lambda _task: (
                "PublishedPublic",
                "chrome api chrome published state=PUBLISHED; published_version=1.0.0.4",
                "chrome_api",
                "1.0.0.4",
            )

            def fake_notify(*args, **kwargs):
                called["count"] += 1
                return True, "ok"

            monitor.notify_feishu = fake_notify
            monitor.handle_task(conn, task, webhook_url="", timeout_poll_seconds=7200, timeout_followup_days=7)
        finally:
            monitor.fetch_task_status = original_fetch
            monitor.notify_feishu = original_notify

        row2 = conn.execute(
            "SELECT status FROM tasks WHERE id = ?",
            (task_id,),
        ).fetchone()
        self.assertEqual(row2["status"], "PendingReview")
        self.assertEqual(called["count"], 0)
        conn.close()

    def test_chrome_can_backfill_missing_published_notification(self) -> None:
        conn = monitor.db_connect(":memory:")
        monitor.init_db(conn)
        task_id, _ = monitor.add_task(
            conn,
            store="chrome",
            plugin_name="StreamFab Netflix Downloader for Browser",
            detail_url=None,
            item_id="glcbkndciojfeepepdoeofgpojigcdmf",
            version="1.0.0.4",
            submitted_at=monitor.iso(monitor.now_utc() - monitor.dt.timedelta(hours=3)),
            owner=None,
            operation_id=None,
            check_frequency_seconds=300,
            timeout_hours=72,
        )
        conn.execute("UPDATE tasks SET status = 'PublishedPublic' WHERE id = ?", (task_id,))
        conn.execute(
            "INSERT INTO status_events(task_id, status, source, detail, event_time) VALUES (?, ?, ?, ?, ?)",
            (task_id, "PendingReview", "chrome_api", "earlier pending", monitor.iso(monitor.now_utc() - monitor.dt.timedelta(hours=2))),
        )
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
        original_notify = monitor.notify_feishu
        called = {"count": 0}
        try:
            monitor.fetch_task_status = lambda _task: (
                "PublishedPublic",
                "chrome api chrome published state=PUBLISHED; published_version=1.0.0.4",
                "chrome_api",
                "1.0.0.4",
            )

            def fake_notify(*args, **kwargs):
                called["count"] += 1
                return True, "ok"

            monitor.notify_feishu = fake_notify
            monitor.handle_task(conn, task, webhook_url="", timeout_poll_seconds=7200, timeout_followup_days=7)
        finally:
            monitor.fetch_task_status = original_fetch
            monitor.notify_feishu = original_notify

        self.assertEqual(called["count"], 1)
        notif = conn.execute("SELECT status FROM notifications WHERE task_id = ? ORDER BY id DESC LIMIT 1", (task_id,)).fetchone()
        self.assertEqual(notif["status"], "PublishedPublic")
        conn.close()

    def test_timeout_terminal_notification_uses_pending_review_as_old_status(self) -> None:
        conn = monitor.db_connect(":memory:")
        monitor.init_db(conn)
        task_id, _ = monitor.add_task(
            conn,
            store="chrome",
            plugin_name="StreamFab Netflix Downloader for Browser",
            detail_url=None,
            item_id="glcbkndciojfeepepdoeofgpojigcdmf",
            version="1.0.0.4",
            submitted_at=monitor.iso(monitor.now_utc() - monitor.dt.timedelta(hours=80)),
            owner=None,
            operation_id=None,
            check_frequency_seconds=300,
            timeout_hours=72,
        )
        conn.execute(
            "UPDATE tasks SET status = 'TimeoutMonitoring', timeout_started_at = ? WHERE id = ?",
            (monitor.iso(monitor.now_utc() - monitor.dt.timedelta(hours=2)), task_id),
        )
        conn.execute(
            "INSERT INTO status_events(task_id, status, source, detail, event_time) VALUES (?, ?, ?, ?, ?)",
            (task_id, "PendingReview", "chrome_api", "earlier pending", monitor.iso(monitor.now_utc() - monitor.dt.timedelta(hours=10))),
        )
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
        original_notify = monitor.notify_feishu
        sent = {}
        try:
            monitor.fetch_task_status = lambda _task: (
                "PublishedPublic",
                "chrome api chrome published state=PUBLISHED; published_version=1.0.0.4",
                "chrome_api",
                "1.0.0.4",
            )

            def fake_notify(_webhook_url, _task, old_status, new_status, detail, effective_version, changed_at):
                sent["old_status"] = old_status
                sent["new_status"] = new_status
                sent["detail"] = detail
                sent["effective_version"] = effective_version
                sent["changed_at"] = changed_at
                return True, "ok"

            monitor.notify_feishu = fake_notify
            monitor.handle_task(conn, task, webhook_url="", timeout_poll_seconds=7200, timeout_followup_days=7)
        finally:
            monitor.fetch_task_status = original_fetch
            monitor.notify_feishu = original_notify

        self.assertEqual(sent["old_status"], "PendingReview")
        self.assertEqual(sent["new_status"], "PublishedPublic")
        conn.close()


if __name__ == "__main__":
    unittest.main()
