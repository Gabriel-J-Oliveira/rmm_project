# NightOwl Agent release CI runner

Etapa 0A moves release build and publication away from a personal developer workstation. The supported target is a Windows self-hosted runner that can run PowerShell non-interactively.

## Architecture

The runner executes:

1. `scripts\Test-NightOwlReleaseRunner.ps1`
2. `scripts\Publish-NightOwlAgentRelease.ps1 -Ci`

The private signing key stays outside the repository and outside the workspace. The publisher reads it from a protected path and never copies it into artifacts.

## Required runner software

- Windows Server or Windows workstation suitable for a self-hosted runner.
- PowerShell 5.1 or PowerShell 7.
- .NET SDK 8.x.
- Git for Windows.
- OpenSSH Client with `ssh.exe` and `scp.exe`.
- SSH key access to the Ubuntu host alias used by the publisher.

Use this helper to validate prerequisites and optionally prepare directories:

```powershell
powershell.exe -ExecutionPolicy Bypass `
  -File ".\scripts\Install-NightOwlReleaseRunnerPrerequisites.ps1" `
  -ConfigDirectory "C:\ProgramData\NightOwl\ReleasePublisher" `
  -SigningKeyDirectory "C:\ProgramData\NightOwl\ReleaseSigning" `
  -WriteTemplateConfig
```

To apply restrictive ACLs, run as administrator and pass the runner service account:

```powershell
powershell.exe -ExecutionPolicy Bypass `
  -File ".\scripts\Install-NightOwlReleaseRunnerPrerequisites.ps1" `
  -RunnerAccount "DOMAIN\nightowl-release-runner$" `
  -ApplyAcl
```

The script uses `SYSTEM` SID `S-1-5-18` and does not depend on localized Windows group names.

## Environment variables

The CI-friendly names are:

```text
NIGHTOWL_RELEASE_SIGNING_KEY_PATH
NIGHTOWL_RELEASE_SIGNING_KEY_ID
NIGHTOWL_RELEASE_PUBLIC_KEYS_PATH
NIGHTOWL_RELEASE_SSH_TARGET
NIGHTOWL_RELEASE_REMOTE_ROOT
NIGHTOWL_RELEASE_PUBLIC_BASE_URL
NIGHTOWL_RELEASE_DJANGO_ROOT
```

`NIGHTOWL_RELEASE_DJANGO_ROOT` and `NIGHTOWL_RELEASE_REMOTE_ROOT` normally point to the same project directory, for example `/opt/nightowl`.

Precedence is:

1. explicit script parameter;
2. environment variable;
3. local `release-publisher.json`;
4. non-sensitive defaults.

Legacy environment variable names remain supported for local fallback, but new runner configuration should use the names above.

## Private key handling

Store the private XML key in a protected path such as:

```text
C:\ProgramData\NightOwl\ReleaseSigning\nightowl-release-2026-02-private.xml
```

The ACL should allow only:

- the self-hosted runner service account;
- `SYSTEM`.

Do not copy the private key into the Git checkout, workflow artifacts, logs, downloads directory or user profile dumps.

## Validate the runner without publishing

```powershell
powershell.exe -ExecutionPolicy Bypass `
  -File ".\scripts\Test-NightOwlReleaseRunner.ps1" `
  -Version "0.1.1.0-rc9" `
  -Channel development `
  -Ci
```

This validates tools, key access, key/bundle compatibility, RSA-PSS signing, SSH, remote directories, Python virtualenv and required Django commands. It does not publish a release.

## ValidateOnly vs DryRun

`ValidateOnly` validates environment, Git state, configuration, key material, public key bundle and connectivity. It does not build, upload or import.

```powershell
powershell.exe -ExecutionPolicy Bypass `
  -File ".\scripts\Publish-NightOwlAgentRelease.ps1" `
  -Version "0.1.1.0-rc9" `
  -Channel development `
  -Ci `
  -ValidateOnly
```

`DryRun` performs the local release flow, including tests, build, manifest generation, RSA-PSS signature and local artifact validation. It does not execute SSH, SCP, public URL validation, Django import or remote mutations.

```powershell
powershell.exe -ExecutionPolicy Bypass `
  -File ".\scripts\Publish-NightOwlAgentRelease.ps1" `
  -Version "0.1.1.0-rc9" `
  -Channel development `
  -Ci `
  -DryRun
```

Expected final message:

```text
DRY RUN CONCLUIDO. Nenhum arquivo foi enviado e nenhuma release foi alterada.
```

## GitHub Actions

The workflow is:

```text
.github/workflows/nightowl-agent-release.yml
```

It is manual-only through `workflow_dispatch` and uses a Windows self-hosted runner. Inputs:

- `version`
- `channel`
- `validate_only`
- `dry_run`
- `skip_tests`

The workflow uses concurrency group `nightowl-agent-release`, so two releases are not published simultaneously from Actions.

## Recovery

If upload fails, the publisher removes its remote temporary upload directory.

If the target version already exists with identical ZIP, manifest and signature, the operation is idempotent.

If the target version exists with different content, publication fails with `RELEASE_IMMUTABILITY_VIOLATION`. Use a new version instead of overwriting published content.

If upload succeeded but import failed, rerun:

```powershell
powershell.exe -ExecutionPolicy Bypass `
  -File ".\scripts\Publish-NightOwlAgentRelease.ps1" `
  -Version "0.1.1.0-rc9" `
  -Channel development `
  -Ci `
  -ResumeImport
```

## Future key rotation

Key generation and trust bundle rotation remain separate from normal release publication. Etapa 0B adds trust bundle build, validation, publication and endpoint synchronization; the runner uses pre-provisioned private keys and public bundles from protected paths.

## Trust bundle publishing variables

Etapa 0B adds automated trust bundle publishing. The runner may also define:

```text
NIGHTOWL_TRUST_ROOT_SIGNING_KEY_PATH
NIGHTOWL_TRUST_ROOT_KEY_ID
NIGHTOWL_RELEASE_PUBLIC_KEYS_PATH
NIGHTOWL_RELEASE_TRUST_ROOTS_JSON
```

Validate without publishing:

```powershell
powershell.exe -ExecutionPolicy Bypass -File .\scripts\Publish-NightOwlReleaseTrustBundle.ps1 -SelfTest
powershell.exe -ExecutionPolicy Bypass -File .\scripts\Publish-NightOwlReleaseTrustBundle.ps1 -BundleVersion 2 -DryRun
```

The trust root private key must remain outside the repository and workspace, protected with ACLs for the runner service account and SYSTEM only. `NIGHTOWL_RELEASE_TRUST_ROOTS_JSON` points to the public `release-trust-roots.json` copied into future agent ZIPs; it must contain only public root material.
