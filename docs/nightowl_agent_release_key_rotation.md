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

## Stable Rule

Stable releases must be signed, must reference a known active key, and cannot use `legacy_unsigned`. Signature bypass is allowed only for explicit manual development bootstrap of pre-RC6 agents.
