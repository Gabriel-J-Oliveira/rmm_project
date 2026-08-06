# NightOwl Agent Release Key Rotation

NightOwl Agent releases are signed with RSA-PSS + SHA-256. The private key must stay on the publishing workstation or in a protected secret store. It must never be committed to Git, uploaded to the public downloads directory, or printed in logs.

## Files

- Private signing key: referenced by `NIGHTOWL_RELEASE_SIGNING_KEY` or `-SigningKeyPath`.
- Signing key id: referenced by `NIGHTOWL_RELEASE_SIGNING_KEY_ID` or `-SigningKeyId`.
- Optional trusted public key set: `NIGHTOWL_RELEASE_TRUSTED_KEYS_JSON` or `-TrustedPublicKeysPath`.
- Public key bundle inside the agent ZIP: `release-public-keys.json`.
- Manifest signature: `release-manifest.sig`, Base64 encoded RSA-PSS/SHA-256 over the exact bytes of `release-manifest.json`.

## Export Public Keys

Generate the public key bundle outside the repository from the private XML key:

```powershell
$env:NIGHTOWL_RELEASE_SIGNING_KEY="C:\secure\nightowl-release-private.xml"
$env:NIGHTOWL_RELEASE_SIGNING_KEY_ID="nightowl-release-2026-01"

powershell.exe -ExecutionPolicy Bypass `
  -File ".\scripts\Export-NightOwlReleasePublicKeys.ps1"
```

Default output:

```text
%USERPROFILE%\.nightowl\release-public-keys.json
```

The script exports only the public RSA XML, verifies it against the private key with RSA-PSS/SHA-256, rejects private RSA parameters in the output, and writes UTF-8 without BOM.

Normal release publication is handled by `scripts\Publish-NightOwlAgentRelease.ps1` using the local publisher configuration described in `docs/nightowl_agent_release_publishing.md`. Key generation and transition bundles are rotation-only operations; do not run them for every RC.

## Generate A New Key Pair

Create the next RSA 3072 signing key pair outside the repository:

```powershell
powershell.exe -ExecutionPolicy Bypass `
  -File ".\scripts\New-NightOwlReleaseSigningKey.ps1"
```

Default outputs:

```text
%USERPROFILE%\.nightowl\release-signing\nightowl-release-2026-02-private.xml
%USERPROFILE%\.nightowl\release-public-keys.json
```

The script uses `key_id=nightowl-release-2026-02`, writes UTF-8 without BOM, validates the pair with RSA-PSS/SHA-256, and restricts the private key ACL to the current Windows user and `SYSTEM`.

It refuses to overwrite either file unless `-Force` is supplied:

```powershell
powershell.exe -ExecutionPolicy Bypass `
  -File ".\scripts\New-NightOwlReleaseSigningKey.ps1" `
  -Force
```

## Rotation

1. Generate the new private/public RSA key pair outside the repository.
2. Build one overlap release signed by the current trusted key and pass a trusted key set containing both old and new public keys.
3. Install the overlap release on pilot endpoints and confirm the updater trusts both keys.
4. Start signing later releases with the new key id.
5. Keep the old public key trusted until all endpoints that might need rollback or update from old builds are migrated.
6. Revoke the old key in Django only after the overlap window:

```powershell
python manage.py revoke_agent_release_key --key-id nightowl-release-2026-01 --reason "Rotacao concluida"
```

Revoked keys block new release eligibility and `verify_agent_release`. Existing installed versions remain audit records; revocation does not delete artifacts.

## Secure Bootstrap For RC6

RC6 can trust a new public key only if the new bundle is signed by a key it already trusts. For the current rotation, the bundle containing `nightowl-release-2026-01` and `nightowl-release-2026-02` must be signed by the private key for `nightowl-release-2026-01`.

Generate a signed bundle when the old private key is available:

```powershell
$env:NIGHTOWL_RELEASE_ROTATION_SIGNING_KEY="C:\secure\nightowl-release-2026-01-private.xml"

powershell.exe -ExecutionPolicy Bypass `
  -File ".\scripts\New-NightOwlReleasePublicKeysBundle.ps1" `
  -ExistingPublicKeysPath "C:\path\release-public-keys-2026-01.json" `
  -NewPublicKeysPath "$env:USERPROFILE\.nightowl\release-public-keys.json"
```

Apply the signed bundle locally on the endpoint as Administrator:

```powershell
powershell.exe -ExecutionPolicy Bypass `
  -File ".\scripts\Apply-NightOwlReleasePublicKeysBootstrap.ps1" `
  -BundlePath "$env:USERPROFILE\.nightowl\release-public-keys-2026-01-2026-02.json" `
  -SignaturePath "$env:USERPROFILE\.nightowl\release-public-keys-2026-01-2026-02.sig"
```

Rollback to the preserved backup:

```powershell
powershell.exe -ExecutionPolicy Bypass `
  -File ".\scripts\Apply-NightOwlReleasePublicKeysBootstrap.ps1" `
  -Rollback `
  -BackupPath "C:\ProgramData\NightOwl\AgentDotNet\release-public-keys.json.backup-YYYYMMDDHHMMSS"
```

The signed bootstrap validates SHA-256 when provided, RSA-PSS/SHA-256 with `nightowl-release-2026-01`, schema, unique `key_id`, allowed algorithm, absence of private RSA parameters, atomic write, backup, ACL and structured JSONL logging.

## Laboratory Bootstrap Only

If the private key for `nightowl-release-2026-01` no longer exists, there is no cryptographically valid way for RC6 to authenticate `nightowl-release-2026-02` before installing RC7. Do not invent a bypass for production.

For a disposable development endpoint such as TAXCEL, an administrator may apply the bundle locally with an explicit test marker:

```powershell
powershell.exe -ExecutionPolicy Bypass `
  -File ".\scripts\New-NightOwlReleasePublicKeysBundle.ps1" `
  -ExistingPublicKeysPath "C:\ProgramData\NightOwl\AgentDotNet\release-public-keys.json" `
  -NewPublicKeysPath "$env:USERPROFILE\.nightowl\release-public-keys.json" `
  -AllowUnsignedTestBundle

powershell.exe -ExecutionPolicy Bypass `
  -File ".\scripts\Apply-NightOwlReleasePublicKeysBootstrap.ps1" `
  -BundlePath "$env:USERPROFILE\.nightowl\release-public-keys-2026-01-2026-02.json" `
  -TestBootstrapUntrusted `
  -ConfirmTestBootstrap TEST_BOOTSTRAP_UNTRUSTED
```

This writes `TEST_BOOTSTRAP_UNTRUSTED` to `C:\ProgramData\NightOwl\Logs\agent-key-bootstrap.jsonl`. It must remain limited to development/laboratory use, must never be triggered by the backend, and must not be allowed for pilot or stable rollout.

## Stable Rule

Stable releases must be signed, must reference a known active key, and cannot use `legacy_unsigned`. Signature bypass is allowed only for explicit manual development bootstrap of pre-RC6 agents.

## Etapa 0B Trust Bundle

The operational replacement for manual endpoint key copies is a signed trust bundle:

- release keys continue signing `release-manifest.json`;
- a separate root key signs `release-public-keys.json`;
- the agent accepts a new release key only after validating the trust bundle signature with an already trusted root;
- bundles are installed under `C:\ProgramData\NightOwl\Trust`;
- the legacy `C:\ProgramData\NightOwl\AgentDotNet\release-public-keys.json` is migrated once and preserved for compatibility.

Build and validate without publishing:

```powershell
powershell.exe -ExecutionPolicy Bypass -File .\scripts\Build-NightOwlReleaseTrustBundle.ps1 -SelfTest
powershell.exe -ExecutionPolicy Bypass -File .\scripts\Test-NightOwlReleaseTrustBundle.ps1 -SelfTest
powershell.exe -ExecutionPolicy Bypass -File .\scripts\Publish-NightOwlReleaseTrustBundle.ps1 -SelfTest
powershell.exe -ExecutionPolicy Bypass -File .\scripts\Publish-NightOwlReleaseTrustBundle.ps1 -BundleVersion 2 -DryRun
```

The root private key must remain outside the repository and outside the endpoint. The file `release-trust-roots.json` contains only public root keys and must be distributed with a future agent release before pilot/stable trust-bundle synchronization is enabled.
