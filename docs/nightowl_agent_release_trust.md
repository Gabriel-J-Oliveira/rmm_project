# NightOwl Agent Release Trust

## Modelo de confianca

O agente usa dois niveis de chave:

- raiz de trust bundle: assina `release-public-keys.json`;
- chaves de release: assinam `release-manifest.json` de cada pacote do agente.

A chave privada raiz fica fora do repositorio e fora dos endpoints. O agente conhece apenas chaves publicas raiz por `release-trust-roots.json`, instalado junto aos binarios ou embutido em release futura.

## Arquivos publicados

Os bundles sao publicados em:

```text
/downloads/nightowl-agent/trust/bundles/<bundle_version>/
  release-public-keys.json
  release-public-keys.sig
  release-public-keys.meta.json
```

Tambem ha copias estaveis em:

```text
/downloads/nightowl-agent/trust/release-public-keys.json
/downloads/nightowl-agent/trust/release-public-keys.sig
/downloads/nightowl-agent/trust/release-public-keys.meta.json
```

O agente deve instalar localmente em:

```text
C:\ProgramData\NightOwl\Trust\
  release-public-keys.json
  release-public-keys.sig
  release-public-keys.meta.json
  state.json
  Backups\
  Downloads\
```

O arquivo legado em `C:\ProgramData\NightOwl\AgentDotNet\release-public-keys.json` e migrado uma vez para `Trust`, preservando backup e mantendo o legado para compatibilidade.

## Job explicito

O job `update_trusted_release_keys` recebe:

```json
{
  "metadata_url": "https://.../trust/bundles/2/release-public-keys.meta.json",
  "bundle_url": "https://.../trust/bundles/2/release-public-keys.json",
  "signature_url": "https://.../trust/bundles/2/release-public-keys.sig",
  "expected_root_key_id": "nightowl-trust-root-2026-01",
  "expected_bundle_version": 2,
  "expected_sha256": "<sha256>"
}
```

Fluxo no endpoint:

```text
received -> downloading_metadata -> validating_metadata -> downloading_bundle
-> downloading_signature -> validating_hashes -> validating_root_signature
-> validating_bundle_policy -> creating_backup -> installing_bundle
-> validating_installed_bundle -> completed
```

Falhas nao substituem o bundle atual.

## Validacoes

O agente bloqueia:

- raiz desconhecida ou revogada;
- assinatura RSA-PSS/SHA-256 invalida;
- hash divergente;
- `bundle_version` menor que o instalado;
- mesma versao com conteudo diferente;
- bundle expirado;
- `key_id` duplicado;
- algoritmo desconhecido;
- parametros privados RSA;
- regressao de chave revogada para ativa;
- bundle sem chave ativa.

## Comandos

Gerar uma raiz de laboratorio fora do repositorio:

```powershell
powershell.exe -ExecutionPolicy Bypass `
  -File .\scripts\New-NightOwlReleaseTrustRoot.ps1 `
  -RootKeyId "nightowl-trust-root-lab-2026-01" `
  -PrivateKeyPath "$env:USERPROFILE\.nightowl\trust-root\nightowl-trust-root-lab-2026-01-private.xml" `
  -PublicRootsPath "$env:USERPROFILE\.nightowl\release-trust-roots.json"
```

Validar o arquivo publico de roots:

```powershell
powershell.exe -ExecutionPolicy Bypass -File .\scripts\New-NightOwlReleaseTrustRoot.ps1 -SelfTest
```

SelfTest:

```powershell
powershell.exe -ExecutionPolicy Bypass -File .\scripts\New-NightOwlReleaseTrustRoot.ps1 -SelfTest
powershell.exe -ExecutionPolicy Bypass -File .\scripts\Build-NightOwlReleaseTrustBundle.ps1 -SelfTest
powershell.exe -ExecutionPolicy Bypass -File .\scripts\Test-NightOwlReleaseTrustBundle.ps1 -SelfTest
powershell.exe -ExecutionPolicy Bypass -File .\scripts\Publish-NightOwlReleaseTrustBundle.ps1 -SelfTest
```

DryRun local:

```powershell
powershell.exe -ExecutionPolicy Bypass -File .\scripts\Publish-NightOwlReleaseTrustBundle.ps1 `
  -BundleVersion 2 `
  -DryRun
```

Validar pacote de uma futura release sem publicar:

```powershell
$env:NIGHTOWL_RELEASE_TRUST_ROOTS_JSON="$env:USERPROFILE\.nightowl\release-trust-roots.json"

powershell.exe -ExecutionPolicy Bypass `
  -File .\scripts\Publish-NightOwlAgentRelease.ps1 `
  -Version "0.1.1.0-rc9" `
  -Channel development `
  -DryRun
```

Variaveis esperadas no runner:

```text
NIGHTOWL_TRUST_ROOT_SIGNING_KEY_PATH
NIGHTOWL_TRUST_ROOT_KEY_ID
NIGHTOWL_RELEASE_PUBLIC_KEYS_PATH
NIGHTOWL_RELEASE_TRUST_ROOTS_JSON
NIGHTOWL_RELEASE_SSH_TARGET
NIGHTOWL_RELEASE_REMOTE_ROOT
NIGHTOWL_RELEASE_PUBLIC_BASE_URL
NIGHTOWL_RELEASE_DJANGO_ROOT
```

## Producao

Antes de usar em pilot/stable, a release do agente precisa conter o trust anchor publico raiz em `release-trust-roots.json`. O bootstrap manual de laboratorio continua existindo, mas deve permanecer restrito a development.
