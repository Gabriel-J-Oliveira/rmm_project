# NightOwl Agent release publishing

This document describes the normal release path after Etapa 4.

## One-time local configuration

Create SSH key access once:

```powershell
powershell.exe -ExecutionPolicy Bypass `
  -File ".\scripts\Initialize-NightOwlReleasePublishing.ps1" `
  -RemoteHost "192.168.106.51" `
  -RemoteUser "root"
```

Create or rotate a signing key only when needed. This is not part of every release:

```powershell
powershell.exe -ExecutionPolicy Bypass `
  -File ".\scripts\New-NightOwlReleaseSigningKey.ps1" `
  -SelfTest
```

Save publisher configuration outside the repository. The file stores only paths and identifiers, never key material:

```powershell
powershell.exe -ExecutionPolicy Bypass `
  -File ".\scripts\Publish-NightOwlAgentRelease.ps1" `
  -Version "0.1.1.0-rc8" `
  -Channel development `
  -SigningKeyPath "$env:USERPROFILE\.nightowl\release-signing\nightowl-release-2026-02-private.xml" `
  -SigningKeyId "nightowl-release-2026-02" `
  -TrustedPublicKeysPath "$env:USERPROFILE\.nightowl\release-public-keys.json" `
  -SaveConfig `
  -DryRun
```

The persisted configuration is:

```text
%USERPROFILE%\.nightowl\release-publisher.json
```

Explicit parameters and environment variables override this file.

## Normal release

```powershell
powershell.exe -ExecutionPolicy Bypass `
  -File ".\scripts\Publish-NightOwlAgentRelease.ps1" `
  -Version "0.1.1.0-rc8" `
  -Channel development
```

The command validates git state, version format, signing key, public key bundle, SSH, local artifacts, remote hashes, public URLs, Django import and `verify_agent_release`.

The release is imported paused with rollout `0%`. It does not create update jobs.

## Validation and recovery

Validate already generated local artifacts:

```powershell
powershell.exe -ExecutionPolicy Bypass `
  -File ".\scripts\Publish-NightOwlAgentRelease.ps1" `
  -Version "0.1.1.0-rc8" `
  -Channel development `
  -ValidateOnly
```

Resume only the Django import and verification after a previous successful upload:

```powershell
powershell.exe -ExecutionPolicy Bypass `
  -File ".\scripts\Publish-NightOwlAgentRelease.ps1" `
  -Version "0.1.1.0-rc8" `
  -Channel development `
  -ResumeImport
```

`-DryRun` skips network mutations. `-SkipTests` is reserved for laboratory use and keeps build/test responsibility with the operator.

## Immutability

Published release content is immutable. Re-running the publisher for the same version with identical artifacts is idempotent. A different ZIP, manifest or signature for an existing version fails with `RELEASE_IMMUTABILITY_VIOLATION`.

Use a new version, for example RC8 -> RC9, to correct release contents.

## Key rotation

Key generation is separate from publishing. To rotate safely, generate a new key, build a transition bundle signed by the previous trusted key, apply it to pilot endpoints, and only then publish releases signed by the new key.

If the previous private key is unavailable, only a local administrator may use the explicitly marked laboratory bootstrap path. That path is not acceptable for pilot or stable production rollout.

## Panel actions

After publishing, use the administrative panel to:

- publish or keep the imported paused release;
- release rollout;
- promote to pilot or stable;
- pause or resume;
- revoke or supersede;
- monitor endpoint health and job history.

No automatic endpoint update is created by the publisher.
