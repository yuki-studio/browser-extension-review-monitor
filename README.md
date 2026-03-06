# Browser Extension Review Monitor

A lightweight monitor for browser extension review status with Feishu card notifications.

## Features

- Chrome status polling via official Chrome Web Store API (`fetchStatus`)
- Edge support via API (when `operation_id` is available) with optional email fallback
- Feishu interactive card notifications
- Notification on status changes (for example `PublishedPublic -> PendingReview -> Approved/Rejected`)
- Global deduplication by `store + item_id + status + store_version`
- Timeout handling: after `72h`, switch to low-frequency polling for `7` days
- Multi-plugin registry (`store`, `item_id`, `plugin_name`, `detail_url`)
- Batch task creation from CSV

## Project Structure

- `monitor.py`: Main script (task management, polling, notification)
- `.env.example`: Environment variable template
- `requirements.txt`: Python dependencies
- `data/`: Local runtime database and optional local CSV files
- `docs/plugin-review-dynamic-push-prd.md`: Requirement sync for Chrome/Edge review push policy

## Setup

```powershell
cd C:\Users\fab\browser-review-monitor
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env
```

Fill `.env` with your own values:

- Feishu App Bot (`FEISHU_APP_ID`, `FEISHU_APP_SECRET`, and `FEISHU_CHAT_ID` or cached chat id from command bot)
- Optional legacy fallback: `FEISHU_WEBHOOK_URL`
- Chrome OAuth (`CHROME_PUBLISHER_ID`, `CHROME_CLIENT_ID`, `CHROME_CLIENT_SECRET`, `CHROME_REFRESH_TOKEN`) or `CHROME_ACCESS_TOKEN`
- Optional Edge API (`EDGE_CLIENT_ID`, `EDGE_API_KEY`)
- Optional Edge IMAP email fallback (`EDGE_IMAP_*`)

## Core Commands

Initialize DB:

```powershell
python monitor.py init-db
```

Register a plugin (recommended for multi-plugin setup):

```powershell
python monitor.py register-plugin --store chrome --item-id <extension_id> --plugin-name "<plugin_name>" --detail-url "<store_detail_url>"
```

For `edge` plugins, once registered, the runner will automatically keep one active monitoring task per enabled plugin.
This means you do not need to manually create a new task for every submission cycle.

List registered plugins:

```powershell
python monitor.py list-plugins
```

Create a monitoring task:

```powershell
python monitor.py add-task --store chrome --item-id <extension_id> --version <version>
```

Default behavior prevents duplicate active tasks for the same `store + item_id + version`.
Use `--allow-duplicate` to force creation:

```powershell
python monitor.py add-task --store chrome --item-id <extension_id> --version <version> --allow-duplicate
```

Create a monitoring task with explicit metadata:

```powershell
python monitor.py add-task --store chrome --plugin-name "<plugin_name>" --detail-url "<store_detail_url>" --item-id <extension_id> --version <version>
```

Batch create tasks from CSV:

```powershell
python monitor.py add-batch --file .\data\plugins_batch.csv
```

Batch mode follows the same dedup rule by default. Add `--allow-duplicate` if needed.

CSV columns:

`store,item_id,plugin_name,detail_url,version,submitted_at,owner,operation_id`

Run one cycle:

```powershell
python monitor.py run --once
```

Run continuously:

```powershell
python monitor.py run
```

Run Feishu status command bot (for `@bot status` style query):

```powershell
python feishu_status_bot.py
```

List tasks:

```powershell
python monitor.py list-tasks
```

## Notification Card Fields

- Plugin Name
- Plugin ID
- Store
- Version (from store response when available)
- Status Change (`old -> new`)
- Status Change Time (`UTC+8`)
- Detail page button (if `detail_url` exists)

## Security Notes

- Never commit real secrets to `.env.example`
- Keep actual credentials in local `.env`
- Rotate webhook and OAuth credentials immediately if exposed

## Feishu Command Bot Setup

To support querying status in group chat by mentioning the bot and sending `status`:

1. Create/prepare a Feishu app bot with event subscription enabled.
2. Configure event callback URL to your server endpoint running `feishu_status_bot.py`.
3. Subscribe to message receive events (text messages in group).
4. Fill these env vars in `.env`:
   - `FEISHU_APP_ID`
   - `FEISHU_APP_SECRET`
   - `FEISHU_CHAT_ID` (optional if you trigger `status` at least once; bot will cache chat id automatically)
   - `FEISHU_STATUS_PORT` (default `8088`)
   - `FEISHU_STATUS_COMMAND` (default `status`)
   - `FEISHU_STATUS_PLUGIN_TARGETS` (4 plugin ids to return)
5. Start bot service:
   - `python feishu_status_bot.py`

After first successful `@bot status`, active monitoring push will use the same App Bot identity.

## Recommended GitHub Repository Name

`browser-extension-review-monitor`
