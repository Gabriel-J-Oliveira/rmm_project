# NightOwl Operational Homologation

## Validation approach

Technical implementation and operational behavior must not depend on browser automation. Prefer direct, non-invasive validation through authenticated APIs, Django management commands, SSH, PowerShell, service state, sanitized logs, database queries, and automated tests.

Use the browser when the behavior itself is visual or requires a human interaction that has no equivalent technical contract, such as approving a visible Windows Tray consent prompt. If browser automation fails, continue independent technical checks; do not classify the tool limitation as a NightOwl defect.

## Security boundaries

- Preserve the product's authentication, authorization, CSRF, identity, allowlist, grant, expiry, and consent controls. Never bypass them to make a test pass.
- Scope operational checks by endpoint UUID and independently verify its machine ID. Do not identify a target using hostname or IP alone.
- Use supported management commands and audited APIs for permission, grant, job, and service changes. Do not edit database rows directly or forge execution identity.
- Keep tests limited to the explicitly authorized endpoint and a specifically approved, non-critical resource. Do not broaden a pilot to fleet-wide settings.
- Do not expose tokens, credentials, private keys, terminal content, or unnecessary personal data in logs or evidence.

## Evidence and reporting

For each acceptance criterion, report one of:

- **Technically validated:** supported by an executed test, API result, command output, service state, or correlated audit/job record.
- **Visual/manual pending:** requires an authenticated human interaction or visual inspection that was not performed.
- **Blocked by policy or environment:** a real authorization, configuration, identity, network, or dependency gate prevented the test; state its exact reason and do not bypass it.
- **Product defect:** reproducible failure in NightOwl after required authorization and prerequisites are satisfied. Distinguish this from automation-tool failures.

Prefer sanitized evidence with stable identifiers, timestamps, status, ACK, and outcome. Do not claim end-to-end success based only on job creation or HTTP 200; verify execution and the resulting state.

## Human-in-the-loop scenarios

Ask the operator only for the specific interaction that cannot be completed technically. Examples include confirming the correct interactive Windows session, approving consent in the Tray, or visually checking a layout. Continue all independent API, database, service, and log validation while waiting. Stop and clean up temporary grants or relay state according to the approved procedure if the operator is unavailable or the test fails.
