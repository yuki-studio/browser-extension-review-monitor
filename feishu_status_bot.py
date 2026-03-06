import json
import os
import sqlite3
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import List, Tuple

import requests
from dotenv import load_dotenv


def load_env() -> None:
    base = os.path.dirname(os.path.abspath(__file__))
    env_path = os.path.join(base, ".env")
    env_example_path = os.path.join(base, ".env.example")
    if os.path.exists(env_path):
        load_dotenv(dotenv_path=env_path, override=True)
    elif os.path.exists(env_example_path):
        load_dotenv(dotenv_path=env_example_path, override=True)
    else:
        load_dotenv(override=True)


def resolve_path(path_value: str) -> str:
    if os.path.isabs(path_value):
        return path_value
    base_dir = os.path.dirname(os.path.abspath(__file__))
    return os.path.normpath(os.path.join(base_dir, path_value))


def cache_chat_id(chat_id: str) -> None:
    if not chat_id:
        return
    p = resolve_path("./data/feishu_chat_id.txt")
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        f.write(chat_id.strip())


def db_connect(db_path: str) -> sqlite3.Connection:
    resolved = resolve_path(db_path)
    conn = sqlite3.connect(resolved)
    conn.row_factory = sqlite3.Row
    return conn


def parse_targets(raw: str) -> List[Tuple[str, str]]:
    targets: List[Tuple[str, str]] = []
    for seg in (raw or "").split(";"):
        s = seg.strip()
        if not s or ":" not in s:
            continue
        store, item_id = s.split(":", 1)
        store = store.strip().lower()
        item_id = item_id.strip()
        if store in {"chrome", "edge"} and item_id:
            targets.append((store, item_id))
    return targets


def latest_task_for(conn: sqlite3.Connection, store: str, item_id: str):
    return conn.execute(
        """
        SELECT id, store, plugin_name, item_id, version, status, updated_at
        FROM tasks
        WHERE store = ? AND item_id = ?
        ORDER BY id DESC
        LIMIT 1
        """,
        (store, item_id),
    ).fetchone()


def build_status_snapshot() -> str:
    db_path = os.getenv("DB_PATH", "./data/monitor.db")
    default_targets = (
        "chrome:glcbkndciojfeepepdoeofgpojigcdmf;"
        "chrome:pmblmkemjdeicgahfkiogdkhjhefhhea;"
        "edge:bgfbcbkjjndjeamckkakgkiphdhlmbip;"
        "edge:eboeciamjbjeobdhnikpddmpnijicmhg"
    )
    targets = parse_targets(os.getenv("FEISHU_STATUS_PLUGIN_TARGETS", default_targets))
    if not targets:
        return "No status targets configured."

    lines = ["Plugin Status Snapshot"]
    conn = db_connect(db_path)
    try:
        for store, item_id in targets:
            row = latest_task_for(conn, store, item_id)
            if not row:
                lines.append(f"- [{store}] {item_id}: NO_TASK")
                continue
            name = (row["plugin_name"] or "").strip() or "Unknown Plugin"
            version = (row["version"] or "").strip() or "unknown"
            status = (row["status"] or "").strip() or "unknown"
            lines.append(f"- [{store}] {name} ({item_id}) | v={version} | status={status}")
    finally:
        conn.close()
    return "\n".join(lines)


def get_tenant_access_token() -> str:
    app_id = os.getenv("FEISHU_APP_ID", "").strip()
    app_secret = os.getenv("FEISHU_APP_SECRET", "").strip()
    if not app_id or not app_secret:
        raise RuntimeError("missing FEISHU_APP_ID or FEISHU_APP_SECRET")
    resp = requests.post(
        "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal",
        json={"app_id": app_id, "app_secret": app_secret},
        timeout=10,
    )
    resp.raise_for_status()
    data = resp.json()
    if data.get("code") != 0:
        raise RuntimeError(f"tenant_access_token failed: {data}")
    token = data.get("tenant_access_token", "")
    if not token:
        raise RuntimeError("tenant_access_token missing in response")
    return token


def send_chat_text(chat_id: str, text: str) -> None:
    token = get_tenant_access_token()
    url = "https://open.feishu.cn/open-apis/im/v1/messages?receive_id_type=chat_id"
    payload = {
        "receive_id": chat_id,
        "msg_type": "text",
        "content": json.dumps({"text": text}, ensure_ascii=False),
    }
    resp = requests.post(
        url,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        json=payload,
        timeout=10,
    )
    resp.raise_for_status()
    data = resp.json()
    if data.get("code") != 0:
        raise RuntimeError(f"send message failed: {data}")


def normalize_text_content(content: str) -> str:
    if not content:
        return ""
    try:
        obj = json.loads(content)
        if isinstance(obj, dict):
            return str(obj.get("text", ""))
    except Exception:
        pass
    return content


class Handler(BaseHTTPRequestHandler):
    def do_POST(self) -> None:
        try:
            length = int(self.headers.get("Content-Length", "0"))
            raw = self.rfile.read(length) if length > 0 else b""
            body = json.loads(raw.decode("utf-8") or "{}")
        except Exception:
            self.send_response(400)
            self.end_headers()
            self.wfile.write(b"bad request")
            return

        # URL verification callback
        if "challenge" in body:
            resp = {"challenge": body.get("challenge")}
            data = json.dumps(resp).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return

        event = body.get("event", {}) if isinstance(body, dict) else {}
        sender = event.get("sender", {})
        if sender.get("sender_type") == "app":
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"ok")
            return

        message = event.get("message", {}) if isinstance(event, dict) else {}
        chat_id = str(message.get("chat_id", "")).strip()
        msg_type = str(message.get("message_type", "")).strip().lower()
        text = normalize_text_content(str(message.get("content", ""))).strip()
        cmd = os.getenv("FEISHU_STATUS_COMMAND", "status").strip().lower()

        if chat_id and msg_type == "text" and cmd and cmd in text.lower():
            try:
                cache_chat_id(chat_id)
                snapshot = build_status_snapshot()
                send_chat_text(chat_id, snapshot)
            except Exception as e:
                try:
                    send_chat_text(chat_id, f"Status command failed: {e}")
                except Exception:
                    pass

        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"ok")

    def log_message(self, format: str, *args) -> None:  # noqa: A003
        return


def main() -> None:
    load_env()
    host = os.getenv("FEISHU_STATUS_HOST", "0.0.0.0").strip() or "0.0.0.0"
    port = int(os.getenv("FEISHU_STATUS_PORT", "8088").strip() or "8088")
    server = ThreadingHTTPServer((host, port), Handler)
    print(f"feishu status bot listening on {host}:{port}")
    server.serve_forever()


if __name__ == "__main__":
    main()
