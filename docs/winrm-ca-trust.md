# WinRM CA Trust

Production configures the Debian public CA bundle explicitly for WinRM HTTPS:
`WINRM_CA_TRUST_PATH=/etc/ssl/certs/ca-certificates.crt`.
The renewed `control-DC01-CA` SHA1 fingerprint is
`0FB43130DE8CD5DDB99BE63D1B2C16107CBD4D60`; it expires on
2028-10-04 at 12:13:15 UTC. Both corporate CA generations remain in the system
store during transition. Private keys must never be included in a CA bundle.
The previous dedicated bundle remains on disk for operational rollback;
the application default is unchanged, so production must set the explicit path.

The preflight checks that the configured file is absolute, readable, PEM,
contains public CA certificates with `CA:TRUE`, and loads into Python/OpenSSL.
An unrelated expired root does not invalidate the whole bundle. The TLS
connection still validates the server certificate, chosen chain, validity,
and hostname; accepting a loadable bundle does not bypass those checks.
Failures return `WINRM_CA_TRUST_INVALID`. The shared pywinrm factory passes
`ca_trust_path` and `server_cert_validation="validate"` explicitly with
HTTPS/5986 and NTLM. It never falls back to certifi, an alternative CA bundle,
HTTP, or disabled certificate validation. Redirects remain blocked.

The AD CS renewal reused the previous CA key and subject. The old and renewed
certificates may be present in the Debian global CA store, which can make
global chain selection ambiguous. Validate the target handshake with the
configured system bundle before activation. Store cleanup requires a separate review:
`GLOBAL_CA_DUPLICATE_RENEWAL_REQUIRES_SEPARATE_REVIEW`.

Trust deployment does not run real WinRM, remote installation, or enrollment.
It does not change release signing keys or automatic update policy.
