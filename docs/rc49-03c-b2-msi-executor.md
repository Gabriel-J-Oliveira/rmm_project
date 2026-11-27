# RC49-03C-B2 — Executor MSI Windows

## Scope

This increment executes only authenticated, typed MSI operations received as the existing `install_software` AgentJob. Schema v2 is handled by the new executor; the legacy v1 `{package_id}` catalog path and its `softwareInstallEnabled` gate remain unchanged. EXE execution is not supported.

The backend dispatch gate `NIGHTOWL_SOFTWARE_OPERATION_DISPATCH_ENABLED` remains `False` by default. No package was installed during implementation or automated tests.

## Contracts

- The v2 job envelope remains the B1 schema: `schema_version`, `operation_id`, `operation`, `request_id`, `package_uuid`, `artifact_sha256`, `artifact_revision`, `profile_revision`, and `profile_sha256`.
- Before an MSI operation, the agent requests `/api/agent/software-operations/jobs/{job_uuid}/profile/` with the agent Bearer credential. The response is private/no-store, has no credentials or storage key, is limited to the exact dispatched job and endpoint, and is revalidated against current server authorization and package/profile approval.
- The profile response contains the immutable execution profile, ProductCode, expected version, operation, artifact size/hash/revision, profile revision/hash, operation ID, and machine ID. The agent compares every snapshot field and recomputes the canonical profile SHA-256 using the same sorted compact JSON format as Django.
- The existing `/artifact/` endpoint streams the MSI with the same agent authentication and job scoping. The agent checks the response hash/revision/length headers and independently counts and hashes streamed bytes.
- Completed ACKs use the B1 v2 envelope and require a post-operation ProductCode/version proof. Uninstall is successful only when Windows Installer reports the ProductCode absent. Exit code `3010` is reported as `reboot_pending`; the agent never reboots automatically.

## Windows execution policy

New local config fields are intentionally opt-in and default closed:

- `softwareMsiOperationsEnabled`: `false` by default.
- `softwareMsiTrustedPublisherSha256`: empty by default; requires one or more exact 64-hex SHA-256 hashes of trusted publisher certificates.
- `softwareMsiMaxArtifactBytes`: defaults to 1 GiB and cannot exceed 1 GiB.

Capabilities `software_msi_install:1`, `software_msi_update:1`, and `software_msi_uninstall:1` are announced only when the local executor switch is on, the artifact bound is valid, at least one valid publisher pin is present, and `install_software` is in the locally allowed job types. Missing or malformed policy blocks execution and does not advertise the capabilities. This policy is distinct from the legacy v1 catalog flag.

The agent requires Windows Authenticode chain validation plus a signer certificate SHA-256 match against the local pin list. MSI arguments are fixed by operation (`/i <staged MSI> /qn /norestart ALLUSERS=1` or `/x {ProductCode} /qn /norestart`); no command shell, arbitrary command, MSI property, transform, user-supplied path, or reboot command is accepted.

The agent downloads to a per-job directory below its protected ProgramData State directory, rejects reparse points, creates files exclusively, and removes only the directory it created. The existing durable JobStore/JobJournal records dispatch and outcome. A persisted `running` job recovered after process restart is finalized as interrupted by the existing coordinator and is not blindly executed again. A leftover per-job staging directory is treated as an unresolved outcome and requires review.

The server uses an 1800-second upper bound for the v2 MSI job timeout; the agent clamps the received value to the same maximum. Server job expiry still limits dispatch/profile authorization. Package transfer is streamed in 64 KiB chunks; staging consumes disk space up to the configured artifact maximum.

## Compatibility and rollout boundary

- No Django migration is introduced. B1 operation rows created before this increment, which have no `size_bytes` in their immutable JSON snapshot, remain readable using the current package size only after the existing server checks confirm the same artifact hash and revision. New rows snapshot the size.
- The `install_software` job type and protocol v2 are reused; no second job runner, queue, or transport was added.
- Old agents do not announce the versioned MSI capabilities, so B1 remains blocked from dispatching them.
- Local tests use synthetic bytes, fake Authenticode/MSI state, fake `msiexec` delegates, and a fake HTTP transport. These are contract tests, not evidence of a real Windows software installation.
- A new signed Windows agent release is required before the server can use these capabilities. Publication, local trust configuration, dispatch enablement, endpoint updates, and real MSI homologation are separate approvals.

## Tests added

The Windows tests cover authenticated job-scoped profile/artifact HTTP requests, bounded streaming and digest checks, backend-compatible profile hashing, install/update/uninstall argument construction, signature and local-policy failure, installed-state proof, reboot-pending handling, timeout/shutdown classification, safe staging cleanup, and refusal to replay a job with an unresolved prior staging directory. Django tests cover profile scoping, private response headers, queue-state rejection, redaction, and strict v1/v2 internal payload compatibility.
