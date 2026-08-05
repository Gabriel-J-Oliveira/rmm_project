# NightOwl Agent Release Key Rotation

NightOwl Agent releases are signed with RSA-PSS + SHA-256. The private key must stay on the publishing workstation or in a protected secret store. It must never be committed to Git, uploaded to the public downloads directory, or printed in logs.

## Files

- Private signing key: referenced by `NIGHTOWL_RELEASE_SIGNING_KEY` or `-SigningKeyPath`.
- Signing key id: referenced by `NIGHTOWL_RELEASE_SIGNING_KEY_ID` or `-SigningKeyId`.
- Optional trusted public key set: `NIGHTOWL_RELEASE_TRUSTED_KEYS_JSON` or `-TrustedPublicKeysPath`.
- Public key bundle inside the agent ZIP: `release-public-keys.json`.
- Manifest signature: `release-manifest.sig`, Base64 encoded RSA-PSS/SHA-256 over the exact bytes of `release-manifest.json`.

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
