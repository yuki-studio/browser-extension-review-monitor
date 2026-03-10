# Browser Extension Review Monitor

Browser review monitoring for Chrome Web Store and Microsoft Edge Add-ons, with Feishu notifications and local task persistence.

## Overview

This project tracks extension review status across multiple plugins and stores.

- Chrome uses the official Chrome Web Store status API.
- Edge supports two modes:
  - Active API-based tracking after `publish-edge-draft` captures the current `operation_id`
  - Email fallback for final review outcomes when API tracking is unavailable

The main goal is to reduce passive waiting. In particular, Edge reviews are no longer limited to "wait and hope an email arrives" once the draft publish step is executed through the script.

## Features

- Chrome review polling via `fetchStatus`
- Edge draft publish integration with automatic `operation_id` capture
- Edge email fallback with plugin-name alias matching
- Proactive Edge "no matching review email yet" alerts
- Feishu interactive card notifications
- Global notification deduplication by transition and version
- Timeout handling with low-frequency follow-up polling
- Multi-plugin registry with `item_id`, `product_id`, and store metadata
- Batch task creation from CSV

## Project Structure

- `monitor.py`: main CLI and monitoring loop
- `feishu_status_bot.py`: Feishu group status bot
- `.env.example`: environment template
- `data/`: local SQLite database and runtime data
- `docs/plugin-review-dynamic-push-prd.md`: notification policy notes
- `tests/`: unit tests

## Setup

```powershell
cd C:\Users\fab\browser-review-monitor
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env
```

Configure `.env` with the credentials you actually use:

- Feishu app bot: `FEISHU_APP_ID`, `FEISHU_APP_SECRET`, `FEISHU_CHAT_ID`
- Optional Feishu webhook fallback: `FEISHU_WEBHOOK_URL`
- Chrome API: `CHROME_PUBLISHER_ID`, `CHROME_CLIENT_ID`, `CHROME_CLIENT_SECRET`, `CHROME_REFRESH_TOKEN`
- Edge API: `EDGE_CLIENT_ID`, `EDGE_API_KEY`
- Edge IMAP fallback: `EDGE_IMAP_*`
- Edge email matching and alert tuning: `EDGE_MAIL_NAME_ALIASES`, `EDGE_NO_MATCH_ALERT_HOURS`, `EDGE_NO_MATCH_ALERT_REPEAT_HOURS`

## Core Workflow

### Chrome

1. Register the plugin.
2. Create or update the monitoring task.
3. Run the monitor loop.

### Edge

1. Prepare the draft manually in Partner Center.
2. Do not click Publish manually.
3. Run `publish-edge-draft` so the tool captures the current `operation_id`.
4. Keep `monitor.py run` active so the task is tracked automatically.

This is the key step that converts Edge monitoring from passive email-only tracking into active submission tracking.

## Core Commands

Initialize the database:

```powershell
python monitor.py init-db
```

Register a Chrome plugin:

```powershell
python monitor.py register-plugin --store chrome --item-id <extension_id> --plugin-name "<plugin_name>" --detail-url "<store_detail_url>"
```

Register an Edge plugin:

```powershell
python monitor.py register-plugin --store edge --item-id <edge_store_id> --product-id <partner_center_product_guid> --plugin-name "<plugin_name>" --detail-url "<store_detail_url>"
```

List registered plugins:

```powershell
python monitor.py list-plugins
```

Create a monitoring task manually:

```powershell
python monitor.py add-task --store chrome --item-id <extension_id> --version <version>
```

Force-create a duplicate active task for the same version:

```powershell
python monitor.py add-task --store chrome --item-id <extension_id> --version <version> --allow-duplicate
```

Publish the current Edge draft and automatically persist the new `operation_id`:

```powershell
python monitor.py publish-edge-draft --item-id <edge_store_id> --version <version>
```

If the Edge plugin is not pre-registered with `product_id`, pass it explicitly:

```powershell
python monitor.py publish-edge-draft --item-id <edge_store_id> --product-id <partner_center_product_guid> --version <version>
```

Batch-create tasks from CSV:

```powershell
python monitor.py add-batch --file .\data\plugins_batch.csv
```

CSV columns:

`store,item_id,product_id,plugin_name,detail_url,version,submitted_at,owner,operation_id`

Run one monitoring cycle:

```powershell
python monitor.py run --once
```

Run the monitor continuously:

```powershell
python monitor.py run
```

Run the Feishu status bot:

```powershell
python feishu_status_bot.py
```

List tasks:

```powershell
python monitor.py list-tasks
```

## Notification Policy

### Chrome

Chrome sends real status transitions such as:

- `PublishedPublic -> PendingReview`
- `PendingReview -> Approved`
- `PendingReview -> Rejected`

### Edge

Edge is intentionally stricter:

- Final-result notifications are sent only for `Approved`, `Rejected`, and `ActionRequired`
- Timeout and internal monitoring states are not pushed as review updates
- If no matching review email is found for too long, the tool can send a separate alert asking for manual verification

`publish-edge-draft` confirms that the current draft submission request succeeded and stores the returned `operation_id`. It does not, by itself, mean that the extension review has been approved.

## Feishu Command Bot Setup

To support `@bot status` in a Feishu group:

1. Create a Feishu app bot and enable event subscriptions.
2. Point the callback URL to the service running `feishu_status_bot.py`.
3. Subscribe to message receive events for group text messages.
4. Set these values in `.env`:
   - `FEISHU_APP_ID`
   - `FEISHU_APP_SECRET`
   - `FEISHU_CHAT_ID`
   - `FEISHU_STATUS_PORT`
   - `FEISHU_STATUS_COMMAND`
   - `FEISHU_STATUS_PLUGIN_TARGETS`
5. Start the bot:

```powershell
python feishu_status_bot.py
```

After the first successful `@bot status`, the same app bot identity can also be used for active push notifications.

## Security Notes

- Do not commit real secrets to `.env.example`
- Keep production credentials in local `.env`
- Rotate leaked webhook, OAuth, or API credentials immediately
