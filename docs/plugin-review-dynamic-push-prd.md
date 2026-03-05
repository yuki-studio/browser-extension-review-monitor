# Plugin Review Dynamic Push (Requirement Sync)

## Background

Current monitor behavior differs by store capability:

- Chrome: can detect review state transitions via API (for example `PublishedPublic -> PendingReview -> Approved/Rejected`).
- Edge (manual web upload path): usually cannot provide `operation_id`, so API progression tracking is unavailable.
- Edge fallback relies on review emails and is suitable for final decision notifications.

## Goal

Define a clear, non-misleading notification strategy for plugin audit push:

- Keep dynamic state-change push for Chrome.
- Use a dedicated Edge template for final result push only.
- Avoid fake transition messages for Edge when no authoritative evidence exists.

## Scope

- In scope:
  - Feishu message template policy for Chrome and Edge.
  - Edge state mapping when using email fallback.
- Out of scope:
  - Edge API full-flow tracking without `operation_id`.

## State Policy

### Chrome

- Data source: Chrome Web Store API.
- Allowed transition push examples:
  - `PublishedPublic -> PendingReview`
  - `PendingReview -> Approved`
  - `PendingReview -> Rejected`

### Edge (manual upload + email fallback)

- Data source: Edge review emails.
- Allowed push timing:
  - Final decision only (`Approved`, `Rejected`, `ActionRequired`).
- Not allowed:
  - Pushing pseudo transitions like `Monitoring -> Monitoring`.
  - Claiming `In review` as confirmed from email if no matching decision mail exists.

## Custom Variable Templates

### Edge Template (Final Result Only)

```text
Edge Extension Audit Monitor

[Title]: {{edge_title}}
[ID]: {{addon_id}}
[Version]: {{version}}
[Date(UTC+8)]: {{event_time_utc8}}
```

Variable mapping:

- `{{edge_title}}`: from review email subject/body, for example `Your product, {{name}}, has been successfully published`.
- `{{addon_id}}`: Edge add-on id (32 chars), for example `bgfbcbkjjndjeamckkakgkiphdhlmbip`.
- `{{version}}`: task version under monitoring.
- `{{event_time_utc8}}`: email decision time converted to `UTC+8`.

### Chrome Template (State Transition)

```text
Chrome Extension Audit Monitor

[Name]: {{name}}
[ID]: {{id}}
[Version]: {{version}}
[Status Update]: {{old_status}} -> {{new_status}}
[Date(UTC+8)]: {{event_time_utc8}}
```

Variable mapping:

- `{{name}}`: plugin display name.
- `{{id}}`: Chrome extension id.
- `{{version}}`: effective version from task/API.
- `{{old_status}}`: previous status.
- `{{new_status}}`: current status.
- `{{event_time_utc8}}`: status change time in `UTC+8`.

## Push Trigger Rule

- Chrome: push immediately when status changes.
- Edge: push immediately only when email evidence confirms one of:
  - `Approved`
  - `Rejected`
  - `ActionRequired`
- Edge must not push `Monitoring -> Monitoring`.

## Acceptance Criteria

1. Chrome continues to send real state transitions.
2. Edge manual-upload tasks send notifications only on final decision.
3. Edge no longer sends cards that imply progression without evidence.
4. Notification content follows the variable templates above.
