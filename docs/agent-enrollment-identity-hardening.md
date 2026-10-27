# Enrollment identity hardening - incident 2026-10-27

## Scope and root cause

Review-only delivery on `fix/agent-enrollment-identity-hardening`. No production
code, database, endpoint, GPO, job or credential was changed during this work.
Production evidence below was obtained with SELECTs in a read-only transaction.

Previously `_find_machine` selected the first matching machine_id or name and,
failing those, reused an endpoint with a unique serial. Both generic OEM serials
and plausible unique serials are not proof of machine identity. Existing records
could then receive a newly generated credential (`existing_rotated` or deployment
`existing_recovered`). A contradictory machine_id only generated a warning.
Heartbeat and inventory could also replace names after detecting an ID mismatch.

## Corrected contract

- Serial is auxiliary inventory only; no serial-based lookup or uniqueness claim.
- New enrollment requires a non-placeholder machine_id and valid installation
  authorization. Shared serials create separate endpoints with separate tokens.
- Existing machine_id/name candidates must be unambiguous and mutually consistent.
- Re-enrollment requires the current active bearer of that exact endpoint, plus
  consistent identity. Installation authorization alone cannot rotate/recover it.
- Successful re-enrollment preserves the current credential (`existing_preserved`).
  Lost-credential reinstallation intentionally requires reviewed recovery.
- Conflicts return HTTP 409 with a stable code, a denied AgentEnrollmentLog and
  `endpoint.identity_conflict`. No token use, credential rotation, endpoint update,
  snapshot or deployment mutation is committed. Unexpected enrollment failures
  roll back a nested transaction before returning a sanitized error.
- Heartbeat, collection, operational status and inventory job materialization check
  identity before writes. Status retains its HTTP 403 conflict contract.
- A rename requires the same established machine_id, current bearer, same domain,
  no conflicting endpoint and no contradictory meaningful hardware serial.
  It records `endpoint.identity_renamed`; histories are not rewritten.
- Missing legacy fields preserve identity, including domain and serial. An empty
  stored machine_id can be bound once if the reported ID is not already assigned.
- Domain changes or meaningful chassis serial changes require operator review.
- Tokens named `NightOwlGpo-<HOSTNAME>` require that exact hostname (case-insensitive),
  with existing domain/manual validation still applied. They are excluded from the
  implicit domain token pool and must be explicitly supplied. This is an additional
  scope check, not AD attestation; token possession still matters.

## Concurrency and compatibility

PostgreSQL identity-mutating paths acquire one transaction advisory lock before
row locks. This serializes simultaneous first enrollments/empty-ID bindings as
well as renames. The lock ends on commit/rollback, including rejection. This is
deliberately conservative for the MVP and may add latency during long inventory
transactions; monitor it. It is shared by these services, not arbitrary SQL/admin
writes. SQLite tests do not prove PostgreSQL concurrency behavior.

No model or migration is added. Existing raw machine_id values are not rewritten.
Case and surrounding whitespace are normalized for lookup. Existing duplicate
IDs block enrollment/binding/rename, but unchanged authenticated runtime reports
are not globally disabled. A future uniqueness constraint needs a separate audit
and approved remediation, including normalized duplicates and terminal records.

This is continuity validation, not hardware attestation. A clone holding both the
same bearer and all reported identity fields cannot be distinguished by these
checks. Serial changes after legitimate motherboard replacement need review.
Agents already affected by an identity collision may be rejected rather than
silently contaminating the remaining endpoint; that is intentional containment.

## Read-only evidence

Point-in-time production audit on 2026-10-27 UTC: 94 AgentMachine rows, zero blank
machine_ids and zero case/whitespace-normalized duplicate machine_id groups.
This does NOT disprove collisions: the incidents reused one row, not two rows.

| Endpoint | Original enrollment UTC | Later association UTC | Evidence |
| --- | --- | --- | --- |
| 610fb016-5485-4aa2-a7d0-10892c92bdbd | 0262: 2026-10-27 13:14:56.493787 | 0059: 2026-10-27 14:57:42.299842 | serial_number / existing_rotated; Default string |
| ab9352a5-6e6f-4652-90e5-b3dee5a6f4e3 | 0133: 2026-10-16 14:28:53.899737 | 0029: 2026-10-20 18:13:03.793148 | serial_number / existing_recovered; To Be Filled By O.E.M. |

Snapshot groups at collection: 0262=22 / 0059=41 and 0133=249 / 0029=262.
The current names were 0059 and 0029. Counts can increase with normal ingestion.
Mixed names alone can also result from a legitimate rename: use enrollment
transitions and independently proven identities, never classify by timing alone.

### Repeatable audit (after approval of the code, not executed by this delivery)

Use a database role restricted to SELECT, preferably a restored forensic copy.
Protect the report as inventory data. Do not export tables, raw metadata, payloads,
token hashes, token prefixes or library exception details into a public report.

```bash
cd /opt/nightowl
.venv/bin/python manage.py audit_agent_identity > /secure/report/identity-audit.json
.venv/bin/python manage.py audit_agent_identity \
  --endpoint 610fb016-5485-4aa2-a7d0-10892c92bdbd \
  --endpoint ab9352a5-6e6f-4652-90e5-b3dee5a6f4e3
```

The command enforces REPEATABLE READ / READ ONLY on PostgreSQL, queries all
machine IDs for missing/duplicate groups, and whitelists enrollment transitions,
snapshot identity aggregates and audit event identifiers/timestamps. It does not
dump messages or unrestricted metadata. It has no apply/repair option.

## Recovery: server-only versus station execution

Keep the installation GPO paused, with no remote jobs to the affected UUIDs.
Do not copy credentials between endpoints or edit UUID/machine_id/token hashes.
Do not delete or move historical snapshots, enrollment logs or audit events.

### Server-only investigation

1. Preserve an encrypted database backup, deployment configuration and audit
   extracts under restricted access; correlate both pairs with trusted AD SID/DN,
   FQDN and independent administrative identity evidence.
2. Inventory existing backups without printing secrets. Database dumps were found
   under `/opt/nightowl/backups`; their contents were not restored or exported.
   A dump containing a pre-collision HMAC cannot reconstruct the agent token.
3. `hash_agent_token` is HMAC-SHA256 with the deployment SECRET_KEY. Restoring an
   old hash into the current shared row would break the other machine, violate
   this incident's constraints, and leave history ambiguous. Do not do it.
4. No existing securely escrowed plaintext credential plus independently proven
   original identity was established. Therefore server-only authentication
   recovery is NOT available from the evidence collected. A restored database
   may be used offline for forensics, not for editing live credential mappings.
5. Reconcile pending/unknown operations read-only. Do not dispatch jobs through the
   mixed endpoint. Keep it as evidence and document which machine currently owns
   its active credential before deciding any later lifecycle action.

### Targeted station recovery (separate approval and implementation)

For 0262/0059, first recover 0262 as a new independent endpoint, leaving the
operational 0059 untouched until its ownership is independently confirmed. For
0133/0029, apply the same sequence to 0133, then independently review 0029.

Specifically, 0059/0029 may already have a valid bearer but a local machine_id
different from the historically retained server ID. The new guard will reject
that contradiction; do not promise these two contaminated records stay healthy.
A fresh token alone cannot recover the same currently occupied hostname either.
Their second-stage recovery needs an explicitly approved server reconciliation
workflow for the mixed record, not direct SQL edits or a bypass in enrollment.
That workflow is not implemented here. Preserve evidence and block remote jobs
until ownership and the reviewed remediation are established for each machine.

1. Authorize one exact AD computer SID/FQDN at a time, not a hostname wildcard.
   Exclude every other computer. Validate the target's domain/SID locally.
2. Use a separately reviewed computer GPO SYSTEM recovery action, with a one-shot
   gate, mutex, bounded retries, authenticated distribution and integrity checks.
   It must preserve service binaries, ACLs, buffers, inventory, URLs and settings.
3. Back up local identity/configuration under SYSTEM-only ACL before changing it.
   The colliding local machine_id cannot simply be retried against the old row:
   this patch correctly rejects that request without the current credential.
4. A targeted recovery action must generate a fresh local machine_id and use a
   fresh, one-use, short-lived hostname/domain-bound enrollment authorization.
   This is station-side identity recovery, not manual editing of production IDs.
   Do not expose the token in command arguments, logs, SYSVOL or ordinary files;
   define authenticated secret delivery before approving that action.
5. Require a new endpoint UUID, local credential persistence, successful heartbeat
   and consistent inventory before declaring recovery. Do not announce recovery
   from enrollment success alone. Preserve and annotate the historical mixed row.
6. Remove the one-shot action and revoke unused authorization. On failure, stop
   for reconciliation; do not loop enrollment or restore a credential over a
   different endpoint. Do not reinstall/uninstall the functioning agent blindly.

The GPO recovery action and its secret-delivery channel are NOT implemented in
this PR. Without them, remote recovery remains blocked; no mass repair is offered.

## Deployment plan (future approval only)

1. Review this PR and test in an isolated PostgreSQL database. Re-run the read-only
   audit; explicitly review duplicate/missing IDs and hostname scopes. Do not add
   constraints as part of this deploy. Keep enrollment GPO paused.
2. Back up the production database and deployed code/configuration securely, note
   current SHA, ensure backup restore is tested offline. Never print SECRET_KEY.
3. Only after merge/deploy authorization, deploy the reviewed commit through the
   normal process and restart only nightowl.service. No database migration needed.
4. Smoke: service/HTTPS health, current agent heartbeat/inventory, isolated fresh
   enrollment, same-bearer re-enrollment without credential change, conflict 409
   without token use, scoped-token mismatch, meaningful hardware mismatch and
   audited legitimate rename. Run synthetic conflict cases in staging, NOT by
   impersonating the affected production machines. Compare stored records before
   and after; check audit codes and unexpected rejection rates.
5. Never resume broad GPO installation until the pilot and recovery plan pass.

### Rollback

Stop new enrollments first by keeping GPO paused and temporarily blocking the
enrollment route at the application ingress if rollback is required. Keep agent
heartbeat/collection routes available. Revert code only with explicit approval,
then restore service normally. Reverting code reintroduces the serial vulnerability,
so do not reopen enrollment until a reviewed secure replacement is deployed.
No schema rollback or production data restoration is needed for this patch.
Do not restore an entire old database over new telemetry or undo audit evidence.

## Validation evidence

Local Windows / Python 3.14, Django test databases only; DATABASE_URL was explicitly
set to `sqlite:///:memory:`. No test suite used the production database.

| Validation | Actual result |
| --- | --- |
| Latest dedicated identity/audit suite | 28 discovered; 27 PASS, 1 PostgreSQL-only SKIP |
| Focused identity + inventory results + deployment tokens + operational status | 97 discovered; 96 PASS, 1 PostgreSQL-only SKIP |
| Broad agents + remote enrollment + install workflow regression | 572 discovered; 542 PASS, 29 SKIP, 1 pre-existing FAIL |
| Same failing test on clean base af714baaa948ec94a7fae1768b5816e28d3370fa | Reproduced identical assertion |
| manage.py check | PASS |
| makemigrations --check --dry-run | PASS: no changes detected |
| Python compileall on changed implementation/tests | PASS |
| git diff --cached --check | PASS |

The final dedicated rerun additionally covers a swallowed/missing success audit
causing rollback instead of a false 200. Broad tests were not bypassed or weakened.
The known failure is
`agents.tests.SecurityPreflightCommandTests.test_settings_test_disables_ssl_redirect_from_runtime_environment`,
line 152: expected false, got true for the settings_test SSL redirect. It was
reproduced with the same environment in a clean detached worktree of the base.
It is outside this identity change and was not modified.

Commands (activate the development virtualenv first):

```powershell
$env:DATABASE_URL = 'sqlite:///:memory:'
python manage.py test agents.test_enrollment_identity --noinput
python manage.py test agents.test_enrollment_identity agents.test_inventory_job_results agents.tests.AgentDeploymentTokenTests agents.tests.AgentOperationalDiagnosticsTests --noinput
python manage.py test agents dashboard.test_remote_install_enrollment dashboard.test_agent_install_workflow --noinput
python manage.py check
python manage.py makemigrations --check --dry-run
git diff --check
```

PostgreSQL, Docker and an installed WSL distribution were unavailable on the local
host. The real PostgreSQL concurrency test is included but NOT claimed as executed:
run `PostgreSQLEnrollmentIdentityTests` using an isolated PostgreSQL test database
before merge/deploy approval. The mocked advisory SQL test is not a substitute.
No production smoke enrollment, endpoint recovery, remote command or deploy was
performed. The original dirty telemetry worktree was preserved; only this isolated
feature worktree is included in the PR.

## PR review follow-up (2026-10-27)

The follow-up review found that embedded operational status could fail after the
heartbeat transaction had committed. Heartbeat and status now share one atomic
boundary; embedded diagnostics are checked against the projected/bound identity,
including the first binding of an empty legacy machine_id. Two regression tests
verify rejection without endpoint/snapshot mutation and rollback on status failure.

The previously failing settings_test test inherited DJANGO_SETTINGS_MODULE from
the parent test process, making setdefault select the wrong module. The subprocess
environment now explicitly selects the module the test actually intends to test;
the assertion and production settings are unchanged. Local focused verification:
134 tests, 133 PASS, 1 PostgreSQL-only SKIP, no failures.

New GitHub Actions checks execute the agents and remote enrollment/workflow suites
on both SQLite and isolated PostgreSQL 17, matching the production major version.
The PostgreSQL job explicitly reruns concurrency and read-only audit tests, with
no SQLite substitution. Both checks must pass before the authorized merge/deploy.

The separate station-service diagnostic plan is in
`docs/incident-0262-service-recovery-review.md`. It does not authorize recovery.
