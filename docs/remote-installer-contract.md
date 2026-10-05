# Remote installer contract

The canonical source is `NightOwl.Agent.Windows/scripts/Install-NightOwlAgentDotNet.ps1`.
The legacy publication pipeline copies this script to `downloads/agent/windows`.
`publish_remote_installer --destination /opt/nightowl/downloads/agent/windows`
previews a script-only refresh; `--apply` publishes the versioned source and updates
only its hash/size in the existing `checksums.json`. ZIP, version.json and signed
release directories are untouched. Signed layouts are deliberately rejected.

Remote installation validates actual public bytes and the installer checksum
against this source before WinRM. Redirects are rejected. The remote wrapper
downloads without redirects, checks the pinned SHA256 and PowerShell AST, and
starts the child without interactive input. Automatic domain enrollment remains
available; a missing manual-validation token fails closed in NonInteractive mode.

Only wrapper STARTED/FINISHED/NOT_STARTED frames are accepted; installer stdout
and stderr are drained to a null stream. STARTED means Process.Start succeeded.
Timestamps are backend receipt times, not independently measured Windows times.
Finished exit code is the real child code, distinct from wrapper failures and
service validation. Missing frames/transport loss cannot prove no execution.

New jobs have a bounded 32-entry stage history, a separate outcome and retry
classification. A failed contract or proven pre-execution failure is retry-safe;
any ambiguous dispatch, nonzero installer exit, existing installation, missing
enrollment or missing heartbeat requires operator investigation. No automatic
retry is performed. The status GET is read-only and does not reconcile old jobs.
The additive diagnostics migration does not rewrite historical job facts.

Publishing the installer is NOT promoting an agent release. The canonical
installer requires a signed package and separately provisioned signing trust.
The legacy stable 0.1.0.7 download root does not contain a signed manifest;
its package must not be silently replaced, re-signed or treated as trusted.
An operator must resolve package selection/provisioning and any prior unknown
installation before retrying. This change does not authorize a real retry.
