# Browser Review Monitoring Logic SOP

## Purpose

This document explains the current monitoring and notification logic used by the Browser Extension Review Monitor for both Chrome Web Store and Microsoft Edge Add-ons.

It is intended as an engineering reference for maintenance, troubleshooting, and future changes.

## High-Level Architecture

The monitor is task-driven.

- Each review item is stored as a task in the local SQLite database.
- The scheduler continuously selects due tasks and evaluates their latest review status.
- Status changes may update the task, generate Feishu notifications, or move the task into timeout follow-up mode.

Core runtime file:

- `monitor.py`

Core database tables:

- `tasks`: active and historical monitoring tasks
- `status_events`: status history for each task
- `notifications`: sent status notifications
- `notification_keys`: deduplication keys for notifications and special alerts
- `plugins`: registered plugin metadata

## Shared Monitoring Model

Each task includes:

- `store`: `chrome` or `edge`
- `item_id`: store extension ID
- `product_id`: Edge Partner Center product ID when available
- `version`: version under observation
- `submitted_at`: submission time used as the baseline for timeout and email filtering
- `status`: current monitoring state
- `next_check_at`: next scheduled poll time
- `timeout_hours`: timeout threshold before follow-up mode starts
- `timeout_started_at`: timestamp marking entry into timeout follow-up mode

Terminal statuses are:

- `Approved`
- `Rejected`
- `ActionRequired`
- `Cancelled`
- `TimeoutClosed`

Non-terminal statuses include:

- `Monitoring`
- `PendingReview`
- `PublishedPublic`
- `MonitorFailed`
- `TimeoutMonitoring`

## Chrome Monitoring Logic

### Data Source

Chrome monitoring uses the official Chrome Web Store status API only.

API entrypoint:

- `publishers/{publisher_id}/items/{item_id}:fetchStatus`

### Authentication

The monitor tries credentials in this order:

1. OAuth refresh flow via:
   - `CHROME_CLIENT_ID`
   - `CHROME_CLIENT_SECRET`
   - `CHROME_REFRESH_TOKEN`
2. Static fallback token:
   - `CHROME_ACCESS_TOKEN`

`CHROME_PUBLISHER_ID` is required.

If the publisher ID or token is missing, the task becomes `MonitorFailed`.

### Status Normalization

Chrome status is derived with this priority:

1. `submittedItemRevisionStatus`
2. `publishedItemRevisionStatus`

The monitor can normalize the API response into:

- `PendingReview`
- `Approved`
- `Rejected`
- `Cancelled`
- `PublishedPublic`
- `Monitoring`
- `MonitorFailed`

### Chrome Notification Policy

Chrome sends notifications for status transitions when all of the following are true:

- the new status is different from the old status
- the transition is allowed by the notification policy
- the transition/version combination has not already been sent

Important Chrome behavior:

- If a newly submitted version is under review while the previous public version is still live, the monitor treats the transition as `PublishedPublic -> PendingReview`
- This makes Chrome notifications reflect the real review lifecycle rather than a generic `Monitoring` transition

### Version Handling

If the Chrome API returns an observed version different from the stored task version, the task version is updated in the database.

Notification deduplication then uses the updated effective version.

## Edge Monitoring Logic

Edge monitoring uses a two-stage strategy:

1. Edge submission operation API when `operation_id` is available
2. IMAP email fallback for final review outcome detection

### Stage 1: Edge API Monitoring

The Edge API is used only when all of the following are available:

- `EDGE_CLIENT_ID`
- `EDGE_API_KEY`
- `task.product_id`
- `task.operation_id`

The API checks the submission operation status in Partner Center.

Important interpretation:

- `SUCCEEDED` means the draft publish operation succeeded
- It does not mean the extension review has been approved
- Therefore `SUCCEEDED` is normalized to `Monitoring`

Possible API-derived outcomes:

- `Monitoring`
- `ActionRequired`
- `MonitorFailed`

The API path can also return terminal conditions if the operation clearly indicates failure.

### Stage 2: Edge Email Fallback

If the Edge API does not produce a final actionable result, the monitor scans email through IMAP.

Required configuration:

- `EDGE_IMAP_HOST`
- `EDGE_IMAP_USER`
- `EDGE_IMAP_PASS`

Optional tuning:

- `EDGE_IMAP_PORT`
- `EDGE_IMAP_FOLDER`
- `EDGE_IMAP_FOLDERS`
- `EDGE_IMAP_SCAN_LIMIT`
- `EDGE_MAIL_FROM_KEYWORDS`
- `EDGE_MAIL_APPROVED_KEYWORDS`
- `EDGE_MAIL_REJECTED_KEYWORDS`
- `EDGE_MAIL_ACTION_KEYWORDS`
- `EDGE_MAIL_NAME_ALIASES`

Email matching rules:

- only messages on or after `submitted_at` are considered
- sender must match configured `EDGE_MAIL_FROM_KEYWORDS`
- mail content must match either:
  - the Edge `item_id`
  - the plugin name
  - the plugin name without the `for Browser` suffix
  - an alias defined in `EDGE_MAIL_NAME_ALIASES`

Matched email outcomes:

- `Approved`
- `Rejected`
- `ActionRequired`

If no matching result email is found, the task remains:

- `Monitoring`

with detail:

- `edge email: no matching result yet`

### Edge Notification Policy

Edge is intentionally stricter than Chrome.

Formal Edge review result notifications are sent only for:

- `Approved`
- `Rejected`
- `ActionRequired`

The following internal states do not generate standard Edge review-result pushes:

- `Monitoring`
- `MonitorFailed`
- `TimeoutMonitoring`
- `TimeoutClosed`

This policy prevents noisy pushes for intermediate monitoring states.

## Edge No-Match Email Alert

Edge has an additional alert path separate from standard status transitions.

If all conditions below are true:

- the task store is `edge`
- the status source is email fallback
- the detail contains `no matching result yet`
- elapsed time since `submitted_at` exceeds `EDGE_NO_MATCH_ALERT_HOURS`

the system sends an orange Feishu alert card stating that no matched approval/rejection email has been found yet.

Default thresholds:

- `EDGE_NO_MATCH_ALERT_HOURS = 24`
- `EDGE_NO_MATCH_ALERT_REPEAT_HOURS = 24`

Repeat behavior:

- the alert is deduplicated by time bucket
- at most one alert is sent per repeat interval for the same plugin/version

This is a reminder for manual verification, not a confirmed review result.

## Timeout Logic

All tasks share the same timeout model.

### Step 1: Enter Timeout Follow-Up

If a task has not reached a terminal state and elapsed time since `submitted_at` exceeds `TIMEOUT_HOURS`, the task enters:

- `TimeoutMonitoring`

At that point:

- `timeout_started_at` is recorded
- the next poll interval becomes `TIMEOUT_POLL_SECONDS`

Default:

- `TIMEOUT_HOURS = 72`

### Step 2: End Timeout Follow-Up

If the task stays in timeout follow-up mode longer than `TIMEOUT_FOLLOWUP_DAYS`, it moves to:

- `TimeoutClosed`

Default:

- `TIMEOUT_FOLLOWUP_DAYS = 7`

For Edge tasks, timeout state changes do not produce formal review-result pushes under the current notification policy.

## Notification Delivery

Feishu delivery supports three modes:

- `app_then_webhook` (default)
- `app`
- `webhook`

Default behavior:

1. try Feishu app bot delivery first
2. if app delivery fails, fall back to webhook delivery

## Notification Deduplication

Standard status notifications are deduplicated by:

- `store`
- `item_id`
- `old_status -> new_status`
- canonicalized version

This prevents repeated pushes for the same transition on the same version.

Special Edge no-match email alerts use a separate bucket-based deduplication key:

- `edge_no_match|item_id|version|time_bucket`

## Edge Task Creation Policy

Registered Edge plugins are kept in the `plugins` table.

Current behavior:

- the scheduler may create an initial monitoring task for a registered Edge plugin if no task history exists yet
- it does not automatically recreate a new task after the previous task has already reached a terminal result

This prevents false alerts where an already approved plugin/version would otherwise be reopened and later reported as "no matching review email yet".

## Practical Interpretation

### Chrome

Chrome is primarily API-driven and produces lifecycle-style transition notifications.

Typical examples:

- `PublishedPublic -> PendingReview`
- `PendingReview -> Approved`
- `PendingReview -> Rejected`

### Edge

Edge is hybrid and more conservative.

- API success does not equal review approval
- final approval/rejection/action-required states are typically determined from email fallback
- lack of matching email may trigger a reminder alert, but not a formal approval/rejection result

## Known Operational Tradeoffs

- Edge email matching depends on message content quality and alias coverage
- if Microsoft changes email wording significantly, matching may fail until aliases or keywords are updated
- Edge API tracking is strongest when submissions are created through the script and `operation_id` is captured
- no-match email alerts are useful for visibility, but they should not be interpreted as store verdicts

## Recommended Maintenance Checklist

- keep `.env` credentials valid
- review `EDGE_MAIL_NAME_ALIASES` when plugin naming changes
- confirm `submitted_at` is accurate when creating tasks
- register Edge plugins with `product_id` whenever possible
- use `publish-edge-draft` for Edge submissions when active operation tracking is needed
- inspect `status_events` and `notification_keys` when troubleshooting unexpected pushes or missed pushes
