# Remote Installation Batch v1

The installation screen dispatches 1-50 selected targets through one credential
modal and opens `/jobs/?tab=installations&batch=<uuid>` for persistent tracking.
List and drawer have independent, non-overlapping GET polling; closing either
does not stop the backend. Credentials are cleared at dispatch and never stored
in browser storage or URLs. Retry uses fresh credentials and a new child batch.
Batch admission and legacy admission share one
PostgreSQL advisory transaction lock and unique global slots. Concurrency is 1
across batches and legacy jobs. Up to 50 normalized, unique AD FQDNs per batch.

## APIs

All routes require an active staff/superuser. Mutations require HTTPS and CSRF.
JSON bodies are bounded to 32 KiB; no arbitrary IP, URL, DN or remote command.

- GET/POST `/agent-install/install-batches/`
- GET `/agent-install/install-batches/<uuid>/`
- POST `/agent-install/install-batches/<uuid>/retry/`
- POST `/agent-install/install-batches/<uuid>/mark-stalled/`

Creation JSON: `targets` (list of FQDN strings), `username`, `password`.
Retry JSON: `username`, `password` (new credentials).
Manual resolution JSON: `item_id` (UUID of the current item).
Creation/retry return 202 with `batch_id`, `status_url`, `list_url`.
List uses `?page=1`, 20 batches per page, newest first. GET is read-only:
no LDAP/DNS/WinRM, stall reconciliation or job creation. Payloads whitelist
fields, fixed progress/messages/error codes; never return diagnostics wholesale.
Consumers must render identity strings as text, not HTML.

## Workflow

Batch: QUEUED -> RUNNING -> COMPLETED / COMPLETED_WITH_ERRORS / INTERRUPTED.
Item: WAITING -> PREFLIGHT -> INSTALLING -> COMPLETED / FAILED /
REVIEW_REQUIRED; SKIPPED is reserved. Failed preflight continues to the next
target. A fresh AD lookup compares the persisted FQDN/DN before execution.
The existing child runner repeats preflight, release/installer validation and
service/enrollment/heartbeat gates; exit code 0 alone is not success.

Credentials live only in request memory and stdin pipes to detached child
processes. Only UUIDs are passed in argv. Nothing is stored in models, logs,
environment overrides or temporary files. Closing the browser does not stop
the batch. Restarting/killing its process cannot recover its credentials.

## Stall and retry

Only child runner heartbeat age is relevant, never time spent in a stage.
Missing heartbeat falls back to job creation time. Manual action is available
at 60 seconds; automatic resolution in the batch runner starts at 120 seconds.
Legacy stale reconciliation also uses 120 seconds and excludes batch children.

Proven pre-invocation failure becomes INTERRUPTED/FAILED. Missing proof or
possible invocation becomes OUTCOME_UNKNOWN/REVIEW_REQUIRED. Slot release is
transactional, carries safe audit metadata and preserves the target's historical
blocker. A resumed runner checks its lease before sending installer stdin and
cannot rewrite the resolved job. An already dispatched remote process cannot
be recalled: review means uncertainty, not cancellation of Windows execution.

Retry creates a new batch referencing its parent; no old batch/item is edited.
Ambiguous targets require a fresh authenticated absence proof and the original
job admission contract repeats that proof. MANAGED/conflicting targets reject
retry (TARGET_MANAGED_OR_CONFLICT), never reinstall automatically. No blind retry.

Dead parent process recovery is explicit:
`python manage.py reconcile_remote_install_batches` previews;
`--apply` interrupts a parent with heartbeat older than 120 seconds only when no
healthy active child remains. It does not dispatch anything. Credentials must
be re-entered for a new retry. No scheduler/timer is installed in this delivery.

## Operations

Apply dashboard migration 0004 before restarting the backend. No Agent,
Updater, release, campaign or enrollment policy changes. The batch UI deployment
also requires collectstatic for its JS/CSS assets; no additional migration.
Production smoke must exercise only list/detail/auth/route/schema checks,
never POST creation/retry or real WinRM. PostgreSQL is the production admission
backend; SQLite is supported for local tests, not production orchestration.
