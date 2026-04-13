import unittest

import monitor


class TaskDedupTests(unittest.TestCase):
    def setUp(self) -> None:
        self.conn = monitor.db_connect(":memory:")
        monitor.init_db(self.conn)

    def tearDown(self) -> None:
        self.conn.close()

    def test_add_task_dedups_active_same_version(self) -> None:
        task_id1, created1 = monitor.add_task(
            self.conn,
            store="chrome",
            plugin_name=None,
            detail_url=None,
            item_id="abc",
            version="1.0.0.9",
            submitted_at=monitor.iso(monitor.now_utc()),
            owner=None,
            operation_id=None,
            check_frequency_seconds=300,
            timeout_hours=72,
        )
        task_id2, created2 = monitor.add_task(
            self.conn,
            store="chrome",
            plugin_name=None,
            detail_url=None,
            item_id="abc",
            version="1009",
            submitted_at=monitor.iso(monitor.now_utc()),
            owner=None,
            operation_id=None,
            check_frequency_seconds=300,
            timeout_hours=72,
        )
        count = self.conn.execute("SELECT COUNT(*) AS c FROM tasks").fetchone()["c"]
        self.assertTrue(created1)
        self.assertFalse(created2)
        self.assertEqual(task_id1, task_id2)
        self.assertEqual(count, 1)

    def test_add_task_allows_duplicate_with_flag(self) -> None:
        task_id1, created1 = monitor.add_task(
            self.conn,
            store="chrome",
            plugin_name=None,
            detail_url=None,
            item_id="abc",
            version="1.0.0.9",
            submitted_at=monitor.iso(monitor.now_utc()),
            owner=None,
            operation_id=None,
            check_frequency_seconds=300,
            timeout_hours=72,
        )
        task_id2, created2 = monitor.add_task(
            self.conn,
            store="chrome",
            plugin_name=None,
            detail_url=None,
            item_id="abc",
            version="1.0.0.9",
            submitted_at=monitor.iso(monitor.now_utc()),
            owner=None,
            operation_id=None,
            check_frequency_seconds=300,
            timeout_hours=72,
            allow_duplicate=True,
        )
        count = self.conn.execute("SELECT COUNT(*) AS c FROM tasks").fetchone()["c"]
        self.assertTrue(created1)
        self.assertTrue(created2)
        self.assertNotEqual(task_id1, task_id2)
        self.assertEqual(count, 2)

    def test_due_tasks_excludes_publishedpublic(self) -> None:
        task_id, created = monitor.add_task(
            self.conn,
            store="chrome",
            plugin_name=None,
            detail_url=None,
            item_id="abc",
            version="1.0.0.9",
            submitted_at=monitor.iso(monitor.now_utc()),
            owner=None,
            operation_id=None,
            check_frequency_seconds=300,
            timeout_hours=72,
        )
        self.assertTrue(created)
        self.conn.execute(
            "UPDATE tasks SET status = 'PublishedPublic', next_check_at = ? WHERE id = ?",
            (monitor.iso(monitor.now_utc() - monitor.dt.timedelta(minutes=1)), task_id),
        )
        self.conn.commit()

        tasks = monitor.due_tasks(self.conn)

        self.assertEqual(tasks, [])


if __name__ == "__main__":
    unittest.main()
