import argparse
import csv
import datetime as dt
import email
import email.utils
import imaplib
import json
import os
import re
import sqlite3
import time
from dataclasses import dataclass
from typing import Optional, Tuple

import requests
from dotenv import load_dotenv


TERMINAL_STATUSES = {"Approved", "Rejected", "ActionRequired", "Cancelled", "TimeoutClosed"}
PLUGIN_NAME_BY_ID = {
    "pmblmkemjdeicgahfkiogdkhjhefhhea": "StreamFab Video Downloader for Borwser",
    "glcbkndciojfeepepdoeofgpojigcdmf": "StreamFab Netflix Downloader for Browser",
}
PLUGIN_DETAIL_URL_BY_ID = {
    "pmblmkemjdeicgahfkiogdkhjhefhhea": "https://chromewebstore.google.com/detail/streamfab-video-downloade/pmblmkemjdeicgahfkiogdkhjhefhhea?authuser=0&hl=en",
}


@dataclass
class Task:
    id: int
    store: str
    plugin_name: Optional[str]
    detail_url: Optional[str]
    item_id: str
    version: str
    submitted_at: str
    status: str
    next_check_at: Optional[str]
    last_checked_at: Optional[str]
    check_frequency_seconds: int
    timeout_hours: int
    timeout_started_at: Optional[str]
    owner: Optional[str]
    operation_id: Optional[str]


def now_utc() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def iso(ts: dt.datetime) -> str:
    return ts.astimezone(dt.timezone.utc).replace(microsecond=0).isoformat()


def iso_utc8(ts: dt.datetime) -> str:
    tz8 = dt.timezone(dt.timedelta(hours=8))
    return ts.astimezone(tz8).replace(microsecond=0).strftime("%Y-%m-%d %H:%M:%S")


def parse_iso(v: str) -> dt.datetime:
    return dt.datetime.fromisoformat(v.replace("Z", "+00:00")).astimezone(dt.timezone.utc)


def env_int(name: str, default: int) -> int:
    v = os.getenv(name)
    if not v:
        return default
    try:
        return int(v)
    except ValueError:
        return default


def default_plugin_name(item_id: str) -> Optional[str]:
    return PLUGIN_NAME_BY_ID.get((item_id or "").strip().lower())


def default_plugin_detail_url(item_id: str) -> Optional[str]:
    return PLUGIN_DETAIL_URL_BY_ID.get((item_id or "").strip().lower())


def canonical_version(v: Optional[str]) -> str:
    s = (v or "").strip()
    digits = re.sub(r"[^0-9]", "", s)
    if digits:
        return digits
    return s.lower()


def resolve_path(path_value: str) -> str:
    if os.path.isabs(path_value):
        return path_value
    base_dir = os.path.dirname(os.path.abspath(__file__))
    return os.path.normpath(os.path.join(base_dir, path_value))


def db_connect(db_path: str) -> sqlite3.Connection:
    if db_path == ":memory:":
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        return conn
    resolved = resolve_path(db_path)
    parent = os.path.dirname(resolved)
    if parent:
        os.makedirs(parent, exist_ok=True)
    conn = sqlite3.connect(resolved)
    conn.row_factory = sqlite3.Row
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            store TEXT NOT NULL CHECK (store IN ('chrome', 'edge')),
            plugin_name TEXT,
            detail_url TEXT,
            item_id TEXT NOT NULL,
            version TEXT NOT NULL,
            submitted_at TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'Created',
            next_check_at TEXT,
            last_checked_at TEXT,
            check_frequency_seconds INTEGER NOT NULL,
            timeout_hours INTEGER NOT NULL,
            timeout_started_at TEXT,
            owner TEXT,
            operation_id TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS status_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            task_id INTEGER NOT NULL,
            status TEXT NOT NULL,
            source TEXT NOT NULL,
            detail TEXT,
            event_time TEXT NOT NULL,
            FOREIGN KEY (task_id) REFERENCES tasks(id)
        );

        CREATE TABLE IF NOT EXISTS notifications (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            task_id INTEGER NOT NULL,
            status TEXT NOT NULL,
            sent_at TEXT NOT NULL,
            channel TEXT NOT NULL,
            UNIQUE(task_id, status),
            FOREIGN KEY (task_id) REFERENCES tasks(id)
        );

        CREATE TABLE IF NOT EXISTS notification_keys (
            k TEXT PRIMARY KEY,
            sent_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS plugins (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            store TEXT NOT NULL CHECK (store IN ('chrome', 'edge')),
            item_id TEXT NOT NULL,
            plugin_name TEXT NOT NULL,
            detail_url TEXT,
            enabled INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(store, item_id)
        );
        """
    )
    # Backward-compatible migration for existing DB files.
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(tasks)").fetchall()}
    if "plugin_name" not in cols:
        conn.execute("ALTER TABLE tasks ADD COLUMN plugin_name TEXT")
    if "detail_url" not in cols:
        conn.execute("ALTER TABLE tasks ADD COLUMN detail_url TEXT")
    conn.commit()


def get_registered_plugin(conn: sqlite3.Connection, store: str, item_id: str) -> Optional[sqlite3.Row]:
    return conn.execute(
        "SELECT store, item_id, plugin_name, detail_url FROM plugins WHERE store = ? AND item_id = ? AND enabled = 1",
        (store, item_id),
    ).fetchone()


def upsert_plugin(conn: sqlite3.Connection, store: str, item_id: str, plugin_name: str, detail_url: Optional[str]) -> None:
    ts = iso(now_utc())
    conn.execute(
        """
        INSERT INTO plugins(store, item_id, plugin_name, detail_url, enabled, created_at, updated_at)
        VALUES (?, ?, ?, ?, 1, ?, ?)
        ON CONFLICT(store, item_id) DO UPDATE SET
          plugin_name = excluded.plugin_name,
          detail_url = excluded.detail_url,
          enabled = 1,
          updated_at = excluded.updated_at
        """,
        (store, item_id, plugin_name, detail_url, ts, ts),
    )
    conn.commit()


def list_plugins(conn: sqlite3.Connection) -> None:
    rows = conn.execute(
        "SELECT store, item_id, plugin_name, detail_url, enabled FROM plugins ORDER BY store, plugin_name, item_id"
    ).fetchall()
    for r in rows:
        print(
            f"store={r['store']} item={r['item_id']} name={r['plugin_name']} "
            f"enabled={r['enabled']} detail_url={r['detail_url'] or ''}"
        )


def find_active_task_by_version(conn: sqlite3.Connection, store: str, item_id: str, version: str) -> Optional[sqlite3.Row]:
    rows = conn.execute(
        """
        SELECT id, status, version
        FROM tasks
        WHERE store = ? AND item_id = ? AND status NOT IN ('Approved', 'Rejected', 'ActionRequired', 'Cancelled', 'TimeoutClosed')
        ORDER BY id DESC
        """,
        (store, item_id),
    ).fetchall()
    target = canonical_version(version)
    for r in rows:
        if canonical_version(r["version"]) == target:
            return r
    return None


def add_task(conn: sqlite3.Connection, store: str, plugin_name: Optional[str], detail_url: Optional[str], item_id: str, version: str, submitted_at: str,
             owner: Optional[str], operation_id: Optional[str], check_frequency_seconds: int,
             timeout_hours: int, allow_duplicate: bool = False) -> Tuple[int, bool]:
    if not allow_duplicate:
        existing = find_active_task_by_version(conn, store, item_id, version)
        if existing:
            return int(existing["id"]), False

    ts = now_utc()
    reg = get_registered_plugin(conn, store, item_id)
    final_plugin_name = (plugin_name or "").strip() or (reg["plugin_name"] if reg else None) or default_plugin_name(item_id)
    final_detail_url = (detail_url or "").strip() or (reg["detail_url"] if reg else None) or default_plugin_detail_url(item_id)
    conn.execute(
        """
        INSERT INTO tasks (
            store, plugin_name, detail_url, item_id, version, submitted_at, status,
            next_check_at, check_frequency_seconds, timeout_hours,
            owner, operation_id, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, 'Monitoring', ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            store, final_plugin_name, final_detail_url, item_id, version, submitted_at,
            iso(ts), check_frequency_seconds, timeout_hours,
            owner, operation_id, iso(ts), iso(ts),
        ),
    )
    conn.commit()
    return int(conn.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]), True


def list_tasks(conn: sqlite3.Connection) -> None:
    rows = conn.execute(
        """
        SELECT id, store, plugin_name, item_id, version, status, submitted_at, next_check_at, operation_id
        FROM tasks
        ORDER BY id DESC
        """
    ).fetchall()
    for r in rows:
        print(
            f"id={r['id']} store={r['store']} name={r['plugin_name'] or ''} item={r['item_id']} version={r['version']} "
            f"status={r['status']} submitted_at={r['submitted_at']} next={r['next_check_at']} op={r['operation_id']}"
        )


def due_tasks(conn: sqlite3.Connection) -> list[Task]:
    rows = conn.execute(
        """
        SELECT
          id, store, item_id, version, submitted_at, status,
          plugin_name, detail_url,
          next_check_at, last_checked_at, check_frequency_seconds,
          timeout_hours, timeout_started_at, owner, operation_id
        FROM tasks
        WHERE next_check_at IS NOT NULL
          AND next_check_at <= ?
          AND status NOT IN ('Approved', 'Rejected', 'ActionRequired', 'Cancelled', 'TimeoutClosed')
        ORDER BY next_check_at ASC
        """,
        (iso(now_utc()),),
    ).fetchall()
    return [Task(**dict(r)) for r in rows]


def update_task_status(conn: sqlite3.Connection, task_id: int, new_status: str, source: str,
                       detail: str, next_check_at: Optional[str],
                       timeout_started_at: Optional[str] = None) -> str:
    ts = iso(now_utc())
    conn.execute(
        """
        UPDATE tasks
        SET status = ?, next_check_at = ?, last_checked_at = ?, timeout_started_at = COALESCE(?, timeout_started_at), updated_at = ?
        WHERE id = ?
        """,
        (new_status, next_check_at, ts, timeout_started_at, ts, task_id),
    )
    conn.execute(
        """
        INSERT INTO status_events(task_id, status, source, detail, event_time)
        VALUES (?, ?, ?, ?, ?)
        """,
        (task_id, new_status, source, detail, ts),
    )
    conn.commit()
    return ts


def touch_task(conn: sqlite3.Connection, task_id: int, next_check_at: str, detail: str, source: str) -> None:
    ts = iso(now_utc())
    conn.execute(
        """
        UPDATE tasks
        SET last_checked_at = ?, next_check_at = ?, updated_at = ?
        WHERE id = ?
        """,
        (ts, next_check_at, ts, task_id),
    )
    conn.execute(
        """
        INSERT INTO status_events(task_id, status, source, detail, event_time)
        VALUES (?, (SELECT status FROM tasks WHERE id = ?), ?, ?, ?)
        """,
        (task_id, task_id, source, detail, ts),
    )
    conn.commit()


def update_task_version(conn: sqlite3.Connection, task_id: int, version: str) -> None:
    conn.execute(
        "UPDATE tasks SET version = ?, updated_at = ? WHERE id = ?",
        (version, iso(now_utc()), task_id),
    )
    conn.commit()


def build_notify_key(task: Task, status: str, effective_version: str) -> str:
    return f"{task.store}|{task.item_id}|{status}|{canonical_version(effective_version)}"


def should_notify(conn: sqlite3.Connection, task: Task, status: str, effective_version: str) -> bool:
    key = build_notify_key(task, status, effective_version)
    row = conn.execute("SELECT 1 FROM notification_keys WHERE k = ?", (key,)).fetchone()
    return row is None


def record_notify(conn: sqlite3.Connection, task: Task, status: str, effective_version: str, channel: str) -> None:
    key = build_notify_key(task, status, effective_version)
    ts = iso(now_utc())
    conn.execute(
        "INSERT OR IGNORE INTO notifications(task_id, status, sent_at, channel) VALUES (?, ?, ?, ?)",
        (task.id, status, ts, channel),
    )
    conn.execute(
        "INSERT OR IGNORE INTO notification_keys(k, sent_at) VALUES (?, ?)",
        (key, ts),
    )
    conn.commit()


def notify_feishu(
    webhook_url: str,
    task: Task,
    old_status: str,
    new_status: str,
    detail: str,
    effective_version: str,
    changed_at: str,
) -> Tuple[bool, str]:
    if not webhook_url:
        return False, "FEISHU_WEBHOOK_URL not set"
    plugin_name = (task.plugin_name or "").strip() or default_plugin_name(task.item_id) or "未命名插件"
    detail_url = (task.detail_url or "").strip() or default_plugin_detail_url(task.item_id) or ""
    header_template = "blue"
    if new_status in {"Rejected", "MonitorFailed", "TimeoutClosed"}:
        header_template = "red"
    elif new_status in {"PendingReview", "TimeoutMonitoring", "ActionRequired"}:
        header_template = "orange"
    elif new_status in {"Approved", "PublishedPublic"}:
        header_template = "green"

    def edge_status_text(status: str) -> str:
        return {
            "Approved": "✅ 审核通过",
            "Rejected": "❌ 审核拒绝",
            "ActionRequired": "⚠️ 需要处理",
        }.get(status, "⏳ 审核中")

    def edge_title_text(status: str, name: str) -> str:
        if status == "Approved":
            return f"Your product, {name}, has been successfully published"
        if status == "Rejected":
            return f"Your product, {name}, was rejected"
        if status == "ActionRequired":
            return f"Your product, {name}, needs action"
        return f"Your product, {name}, is in review"

    actions = []
    if detail_url:
        actions.append(
            {
                "tag": "button",
                "type": "primary",
                "text": {"tag": "plain_text", "content": "View Extension Detail"},
                "url": detail_url,
            }
        )

    if task.store == "edge":
        card_title = "Edge Extension Audit Monitor"
        body = (
            f"[Title]: {edge_title_text(new_status, plugin_name)}\n"
            f"[ID]: {task.item_id}\n"
            f"[Version]: {effective_version}\n"
            f"[Date(UTC+8)]: {iso_utc8(parse_iso(changed_at))}"
        )
    else:
        card_title = "Chrome Extension Audit Monitor"
        body = (
            "[Name]: {name}\n"
            "[ID]: {item}\n"
            "[Version]: {version}\n"
            "[Status Update]: {old_s} -> {new_s}\n"
            "[Date(UTC+8)]: {changed}"
        ).format(
            name=plugin_name,
            item=task.item_id,
            version=effective_version,
            old_s=old_status,
            new_s=new_status,
            changed=iso_utc8(parse_iso(changed_at)),
        )

    payload = {
        "msg_type": "interactive",
        "card": {
            "config": {"wide_screen_mode": True},
            "header": {
                "template": header_template,
                "title": {"tag": "plain_text", "content": card_title},
            },
            "elements": [
                {
                    "tag": "markdown",
                    "content": body,
                },
                {"tag": "action", "actions": actions} if actions else {"tag": "hr"},
            ],
        },
    }
    try:
        resp = requests.post(webhook_url, json=payload, timeout=10)
        if resp.status_code == 200:
            return True, "ok"
        return False, f"http {resp.status_code}: {resp.text[:200]}"
    except Exception as e:
        return False, str(e)


def get_chrome_access_token() -> Optional[str]:
    client_id = os.getenv("CHROME_CLIENT_ID", "").strip()
    client_secret = os.getenv("CHROME_CLIENT_SECRET", "").strip()
    refresh_token = os.getenv("CHROME_REFRESH_TOKEN", "").strip()
    if client_id and client_secret and refresh_token:
        data = {
            "client_id": client_id,
            "client_secret": client_secret,
            "refresh_token": refresh_token,
            "grant_type": "refresh_token",
        }
        resp = requests.post("https://oauth2.googleapis.com/token", data=data, timeout=15)
        resp.raise_for_status()
        body = resp.json()
        token = body.get("access_token")
        if token:
            return token

    token = os.getenv("CHROME_ACCESS_TOKEN", "").strip()
    if token:
        return token
    return None


def normalize_status(raw_json: dict) -> Tuple[str, str]:
    txt = json.dumps(raw_json, ensure_ascii=False).upper()

    if any(k in txt for k in ["REJECT", "DENIED", "FAILED"]):
        return "Rejected", "matched reject-like keyword"
    if any(k in txt for k in ["ACTION_REQUIRED", "ACTION REQUIRED", "NEEDS ATTENTION"]):
        return "ActionRequired", "matched action-required keyword"
    if any(k in txt for k in ["PUBLISHED", "APPROVED", "STAGED", "SUCCESS", "SUCCEEDED"]):
        return "Approved", "matched approved-like keyword"
    if any(k in txt for k in ["PENDING", "REVIEW", "IN_PROGRESS", "RUNNING"]):
        return "Monitoring", "still under review"
    return "Monitoring", "no known terminal keyword"


def normalize_chrome_status(raw_json: dict) -> Tuple[str, str, Optional[str]]:
    submitted = (raw_json.get("submittedItemRevisionStatus") or {}).get("state", "")
    published = (raw_json.get("publishedItemRevisionStatus") or {}).get("state", "")
    submitted_channels = (raw_json.get("submittedItemRevisionStatus") or {}).get("distributionChannels") or []
    published_channels = (raw_json.get("publishedItemRevisionStatus") or {}).get("distributionChannels") or []
    submitted_version = ""
    if submitted_channels and isinstance(submitted_channels[0], dict):
        submitted_version = str(submitted_channels[0].get("crxVersion", "")).strip()
    published_version = ""
    if published_channels and isinstance(published_channels[0], dict):
        published_version = str(published_channels[0].get("crxVersion", "")).strip()

    s = str(submitted).upper()
    p = str(published).upper()

    # Priority: submitted revision reflects the newly uploaded version under review.
    if s == "PENDING_REVIEW":
        return "PendingReview", (
            f"chrome submitted state=PENDING_REVIEW; submitted_version={submitted_version or 'UNKNOWN'}; "
            f"published state={p or 'UNKNOWN'}; published_version={published_version or 'UNKNOWN'}"
        ), (submitted_version or published_version or None)
    if s in {"REJECTED", "DENIED"}:
        return "Rejected", f"chrome submitted state={s}; submitted_version={submitted_version or 'UNKNOWN'}", (submitted_version or None)
    if s in {"CANCELLED", "CANCELED"}:
        return "Cancelled", f"chrome submitted state={s}; submitted_version={submitted_version or 'UNKNOWN'}", (submitted_version or None)
    if s in {"PUBLISHED", "APPROVED"}:
        return "Approved", f"chrome submitted state={s}; submitted_version={submitted_version or 'UNKNOWN'}", (submitted_version or None)

    # Fallback to published revision when submitted revision is absent/unknown.
    if p == "PUBLISHED":
        return "PublishedPublic", f"chrome published state=PUBLISHED; published_version={published_version or 'UNKNOWN'}", (published_version or None)
    if p in {"REJECTED", "DENIED"}:
        return "Rejected", f"chrome published state={p}; published_version={published_version or 'UNKNOWN'}", (published_version or None)
    return "Monitoring", "chrome state unresolved", None


def fetch_chrome_status(task: Task) -> Tuple[str, str, Optional[str]]:
    publisher = os.getenv("CHROME_PUBLISHER_ID", "").strip()
    token = get_chrome_access_token()
    if not publisher or not token:
        return "MonitorFailed", "missing CHROME_PUBLISHER_ID or access token config", None

    url = f"https://chromewebstore.googleapis.com/v2/publishers/{publisher}/items/{task.item_id}:fetchStatus"
    headers = {"Authorization": f"Bearer {token}"}
    try:
        resp = requests.get(url, headers=headers, timeout=15)
        if resp.status_code >= 400:
            return "MonitorFailed", f"chrome api http {resp.status_code}: {resp.text[:200]}", None
        raw = resp.json()
        status, reason, observed_version = normalize_chrome_status(raw)
        return status, f"chrome api {reason}", observed_version
    except Exception as e:
        return "MonitorFailed", f"chrome api error: {e}", None


def fetch_edge_status_from_api(task: Task) -> Tuple[str, str]:
    client_id = os.getenv("EDGE_CLIENT_ID", "").strip()
    api_key = os.getenv("EDGE_API_KEY", "").strip()
    if not (client_id and api_key and task.operation_id):
        return "Monitoring", "edge api not configured or operation_id missing"

    url = f"https://manage.devcenter.microsoft.com/v1.0/my/applications/{task.item_id}/submissions/draft/package/operations/{task.operation_id}"
    headers = {"Authorization": f"ApiKey {api_key}", "X-ClientID": client_id}
    try:
        resp = requests.get(url, headers=headers, timeout=15)
        if resp.status_code >= 400:
            return "MonitorFailed", f"edge api http {resp.status_code}: {resp.text[:200]}"
        raw = resp.json()
        status, reason = normalize_status(raw)
        return status, f"edge api {reason}"
    except Exception as e:
        return "MonitorFailed", f"edge api error: {e}"


def decode_mail_part(msg: email.message.Message) -> str:
    if msg.is_multipart():
        parts = []
        for part in msg.walk():
            ctype = part.get_content_type()
            if ctype == "text/plain":
                payload = part.get_payload(decode=True)
                if payload:
                    parts.append(payload.decode(part.get_content_charset() or "utf-8", errors="ignore"))
        return "\n".join(parts)
    payload = msg.get_payload(decode=True)
    if payload:
        return payload.decode(msg.get_content_charset() or "utf-8", errors="ignore")
    return ""


def edge_id_candidates(task: Task) -> list[str]:
    candidates = {(task.item_id or "").strip().lower()}
    detail_url = (task.detail_url or "").strip().lower()
    m = re.search(r"/detail/([a-z0-9]{32})", detail_url)
    if m:
        candidates.add(m.group(1))
    return [c for c in candidates if c]


def fetch_edge_status_from_email(task: Task) -> Tuple[str, str]:
    host = os.getenv("EDGE_IMAP_HOST", "").strip()
    user = os.getenv("EDGE_IMAP_USER", "").strip()
    pwd = os.getenv("EDGE_IMAP_PASS", "").strip()
    folder = os.getenv("EDGE_IMAP_FOLDER", "INBOX").strip()
    port = env_int("EDGE_IMAP_PORT", 993)
    if not (host and user and pwd):
        return "Monitoring", "edge email fallback not configured"

    from_keywords = [k.strip().lower() for k in os.getenv("EDGE_MAIL_FROM_KEYWORDS", "microsoftedge").split(",") if k.strip()]
    approved_keywords = [k.strip().lower() for k in os.getenv("EDGE_MAIL_APPROVED_KEYWORDS", "approved,published").split(",") if k.strip()]
    rejected_keywords = [k.strip().lower() for k in os.getenv("EDGE_MAIL_REJECTED_KEYWORDS", "rejected,failed").split(",") if k.strip()]
    action_keywords = [k.strip().lower() for k in os.getenv("EDGE_MAIL_ACTION_KEYWORDS", "action required").split(",") if k.strip()]

    try:
        submitted = parse_iso(task.submitted_at)
        id_candidates = edge_id_candidates(task)
        mail = imaplib.IMAP4_SSL(host, port)
        mail.login(user, pwd)
        mail.select(folder)
        status, data = mail.search(None, "ALL")
        if status != "OK":
            return "MonitorFailed", "edge email search failed"

        ids = data[0].split()
        recent_ids = ids[-50:] if len(ids) > 50 else ids
        for mid in reversed(recent_ids):
            status, msg_data = mail.fetch(mid, "(RFC822)")
            if status != "OK" or not msg_data:
                continue
            raw = msg_data[0][1]
            msg = email.message_from_bytes(raw)
            mail_from = str(msg.get("From", "")).lower()
            if from_keywords and not any(k in mail_from for k in from_keywords):
                continue

            msg_date = str(msg.get("Date", "")).strip()
            if msg_date:
                try:
                    msg_ts = email.utils.parsedate_to_datetime(msg_date)
                    if msg_ts is not None:
                        if msg_ts.tzinfo is None:
                            msg_ts = msg_ts.replace(tzinfo=dt.timezone.utc)
                        msg_ts = msg_ts.astimezone(dt.timezone.utc)
                        if msg_ts < submitted:
                            continue
                except Exception:
                    pass

            subject = str(msg.get("Subject", ""))
            body = decode_mail_part(msg)
            text = f"{subject}\n{body}".lower()

            if not any(c in text for c in id_candidates):
                continue

            if any(k in text for k in rejected_keywords):
                return "Rejected", "edge email matched rejected keyword"
            if any(k in text for k in action_keywords):
                return "ActionRequired", "edge email matched action-required keyword"
            if any(k in text for k in approved_keywords):
                return "Approved", "edge email matched approved keyword"

        return "Monitoring", "edge email: no matching result yet"
    except Exception as e:
        return "MonitorFailed", f"edge email error: {e}"


def fetch_task_status(task: Task) -> Tuple[str, str, str, Optional[str]]:
    if task.store == "chrome":
        s, d, v = fetch_chrome_status(task)
        return s, d, "chrome_api", v

    api_status, api_detail = fetch_edge_status_from_api(task)
    if api_status in {"Approved", "Rejected", "ActionRequired", "MonitorFailed"}:
        return api_status, api_detail, "edge_api", None

    mail_status, mail_detail = fetch_edge_status_from_email(task)
    return mail_status, mail_detail, "edge_email", None


def handle_task(conn: sqlite3.Connection, task: Task, webhook_url: str,
                timeout_poll_seconds: int, timeout_followup_days: int) -> None:
    now = now_utc()
    submitted = parse_iso(task.submitted_at)
    old_status = task.status
    notify_old_status = old_status
    effective_version = task.version

    if task.timeout_started_at:
        timeout_begin = parse_iso(task.timeout_started_at)
        if now >= timeout_begin + dt.timedelta(days=timeout_followup_days):
            new_status = "TimeoutClosed"
            detail = f"timeout follow-up window ended after {timeout_followup_days} days"
            changed_at = update_task_status(conn, task.id, new_status, "scheduler", detail, None)
            if old_status != new_status:
                ok, note = notify_feishu(webhook_url, task, notify_old_status, new_status, detail, effective_version, changed_at)
                if ok:
                    record_notify(conn, task, new_status, effective_version, "feishu")
            return

    if not task.timeout_started_at and now >= submitted + dt.timedelta(hours=task.timeout_hours):
        new_status = "TimeoutMonitoring"
        next_check = iso(now + dt.timedelta(seconds=timeout_poll_seconds))
        detail = f"review timeout (> {task.timeout_hours}h), switched to low-frequency monitoring"
        changed_at = update_task_status(conn, task.id, new_status, "scheduler", detail, next_check, timeout_started_at=iso(now))
        if old_status != new_status:
            ok, note = notify_feishu(webhook_url, task, notify_old_status, new_status, detail, effective_version, changed_at)
            if ok:
                record_notify(conn, task, new_status, effective_version, "feishu")
        return

    new_status, detail, source, observed_version = fetch_task_status(task)
    if observed_version and observed_version != task.version:
        update_task_version(conn, task.id, observed_version)
        task.version = observed_version
        effective_version = observed_version
    if (
        source == "chrome_api"
        and new_status == "PendingReview"
        and "published state=PUBLISHED" in detail
        and old_status in {"Created", "Monitoring", "MonitorFailed"}
    ):
        # Review flow semantic: a newly submitted version transitions from public published to pending review.
        notify_old_status = "PublishedPublic"

    if new_status == "Monitoring":
        next_check = iso(now + dt.timedelta(seconds=task.check_frequency_seconds))
        touch_task(conn, task.id, next_check, detail, source)
        return

    if new_status == "MonitorFailed":
        next_check = iso(now + dt.timedelta(seconds=task.check_frequency_seconds))
        changed_at = update_task_status(conn, task.id, new_status, source, detail, next_check)
    elif new_status in TERMINAL_STATUSES:
        changed_at = update_task_status(conn, task.id, new_status, source, detail, None)
    else:
        next_check = iso(now + dt.timedelta(seconds=task.check_frequency_seconds))
        changed_at = update_task_status(conn, task.id, new_status, source, detail, next_check)

    if new_status != old_status and should_notify(conn, task, new_status, effective_version):
        ok, note = notify_feishu(webhook_url, task, notify_old_status, new_status, detail, effective_version, changed_at)
        if ok:
            record_notify(conn, task, new_status, effective_version, "feishu")


def run_loop(conn: sqlite3.Connection, once: bool = False) -> None:
    webhook_url = os.getenv("FEISHU_WEBHOOK_URL", "").strip()
    loop_interval = env_int("LOOP_INTERVAL_SECONDS", 30)
    timeout_poll_seconds = env_int("TIMEOUT_POLL_SECONDS", 7200)
    timeout_followup_days = env_int("TIMEOUT_FOLLOWUP_DAYS", 7)

    while True:
        tasks = due_tasks(conn)
        for t in tasks:
            handle_task(conn, t, webhook_url, timeout_poll_seconds, timeout_followup_days)

        if once:
            break
        time.sleep(max(1, loop_interval))


def main() -> None:
    base = os.path.dirname(os.path.abspath(__file__))
    env_path = os.path.join(base, ".env")
    env_example_path = os.path.join(base, ".env.example")
    if os.path.exists(env_path):
        load_dotenv(dotenv_path=env_path, override=True)
    elif os.path.exists(env_example_path):
        load_dotenv(dotenv_path=env_example_path, override=True)
    else:
        load_dotenv(override=True)

    parser = argparse.ArgumentParser(description="Browser Extension Review Monitor")
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("init-db")
    sub.add_parser("list-tasks")
    sub.add_parser("list-plugins")

    add = sub.add_parser("add-task")
    add.add_argument("--store", required=True, choices=["chrome", "edge"])
    add.add_argument("--plugin-name", default="")
    add.add_argument("--detail-url", default="")
    add.add_argument("--item-id", required=True)
    add.add_argument("--version", required=True)
    add.add_argument("--submitted-at", default=iso(now_utc()))
    add.add_argument("--owner", default="")
    add.add_argument("--operation-id", default="")
    add.add_argument("--allow-duplicate", action="store_true", help="create task even when active task with same version exists")

    reg = sub.add_parser("register-plugin")
    reg.add_argument("--store", required=True, choices=["chrome", "edge"])
    reg.add_argument("--item-id", required=True)
    reg.add_argument("--plugin-name", required=True)
    reg.add_argument("--detail-url", default="")

    batch = sub.add_parser("add-batch")
    batch.add_argument("--file", required=True, help="CSV file with columns: store,item_id,plugin_name,detail_url,version,submitted_at,owner,operation_id")
    batch.add_argument("--default-store", default="chrome", choices=["chrome", "edge"])
    batch.add_argument("--default-version", default="")
    batch.add_argument("--default-submitted-at", default=iso(now_utc()))
    batch.add_argument("--allow-duplicate", action="store_true", help="create duplicate tasks for same active version")

    run = sub.add_parser("run")
    run.add_argument("--once", action="store_true")

    args = parser.parse_args()

    db_path = os.getenv("DB_PATH", "./data/monitor.db")
    conn = db_connect(db_path)

    if args.cmd == "init-db":
        init_db(conn)
        print(f"db initialized: {db_path}")
        return

    init_db(conn)
    if args.cmd == "add-task":
        task_id, created = add_task(
            conn,
            store=args.store,
            plugin_name=args.plugin_name or None,
            detail_url=args.detail_url or None,
            item_id=args.item_id,
            version=args.version,
            submitted_at=args.submitted_at,
            owner=args.owner or None,
            operation_id=args.operation_id or None,
            check_frequency_seconds=env_int("DEFAULT_POLL_SECONDS", 300),
            timeout_hours=env_int("TIMEOUT_HOURS", 72),
            allow_duplicate=args.allow_duplicate,
        )
        if created:
            print(f"task created: id={task_id}")
        else:
            print(f"task already exists (active same version): id={task_id}")
        return

    if args.cmd == "register-plugin":
        upsert_plugin(
            conn,
            store=args.store,
            item_id=args.item_id,
            plugin_name=args.plugin_name,
            detail_url=args.detail_url or None,
        )
        print(f"plugin registered: store={args.store} item={args.item_id} name={args.plugin_name}")
        return

    if args.cmd == "list-plugins":
        list_plugins(conn)
        return

    if args.cmd == "add-batch":
        file_path = resolve_path(args.file)
        created_ids = []
        reused_ids = []
        with open(file_path, "r", encoding="utf-8-sig", newline="") as f:
            reader = csv.DictReader(f)
            for row in reader:
                item_id = (row.get("item_id") or "").strip()
                if not item_id:
                    continue
                store = (row.get("store") or args.default_store).strip().lower()
                version = (row.get("version") or args.default_version).strip()
                if not version:
                    continue
                task_id, created = add_task(
                    conn,
                    store=store,
                    plugin_name=(row.get("plugin_name") or "").strip() or None,
                    detail_url=(row.get("detail_url") or "").strip() or None,
                    item_id=item_id,
                    version=version,
                    submitted_at=(row.get("submitted_at") or args.default_submitted_at).strip(),
                    owner=(row.get("owner") or "").strip() or None,
                    operation_id=(row.get("operation_id") or "").strip() or None,
                    check_frequency_seconds=env_int("DEFAULT_POLL_SECONDS", 300),
                    timeout_hours=env_int("TIMEOUT_HOURS", 72),
                    allow_duplicate=args.allow_duplicate,
                )
                if created:
                    created_ids.append(task_id)
                else:
                    reused_ids.append(task_id)
        print(f"batch result: created={len(created_ids)} reused={len(reused_ids)} created_ids={created_ids} reused_ids={reused_ids}")
        return

    if args.cmd == "list-tasks":
        list_tasks(conn)
        return

    if args.cmd == "run":
        run_loop(conn, once=args.once)
        return


if __name__ == "__main__":
    main()

