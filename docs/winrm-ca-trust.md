# WinRM CA Trust

NightOwl uses a dedicated public CA bundle for WinRM HTTPS:
`WINRM_CA_TRUST_PATH=/etc/nightowl/trust/control-DC01-CA-current.pem`.
The bundle contains only the renewed `control-DC01-CA` certificate. Its SHA1
fingerprint is `0FB43130DE8CD5DDB99BE63D1B2C16107CBD4D60` and it expires
on 2028-10-04 at 12:13:15 UTC. The file is owned by root and readable by the
NightOwl service; private keys must never be stored under `/etc/nightowl/trust`.

The preflight checks that the configured file is absolute, readable, PEM,
contains public CA certificates with `CA:TRUE`, and is currently valid.
Failures return `WINRM_CA_TRUST_INVALID`. The shared pywinrm factory passes
`ca_trust_path` and `server_cert_validation="validate"` explicitly with
HTTPS/5986 and NTLM. It never falls back to certifi, the system CA bundle,
HTTP, or disabled certificate validation. Redirects remain blocked.

The AD CS renewal reused the previous CA key and subject. The old and renewed
certificates may be present in the Debian global CA store, which can make
global chain selection ambiguous. NightOwl does not use that store for WinRM.
Its cleanup requires a separate review:
`GLOBAL_CA_DUPLICATE_RENEWAL_REQUIRES_SEPARATE_REVIEW`.

Trust deployment does not run real WinRM, remote installation, or enrollment.
It does not change release signing keys or automatic update policy.
