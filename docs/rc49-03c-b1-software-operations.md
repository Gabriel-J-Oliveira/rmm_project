# RC49-03C-B1 — Backend de operações de software

## Escopo

Este incremento adiciona contratos de servidor para operações tipadas MSI sobre o `AgentJob` existente. Não implementa executor Windows, não executa instalação real e não ativa o despacho. A configuração `NIGHTOWL_SOFTWARE_OPERATION_DISPATCH_ENABLED` continua `False` por padrão.

EXE permanece armazenável e configurável na Central de Software, mas o contrato B1 nega materialização até existir executor e política próprios. O protocolo legado `install_software` v1 permanece validado e com a capability legada `install_software`.

## Materialização

O operador envia `POST /api/software/packages/{package_uuid}/operations/{endpoint_uuid}/` com JSON estrito:

```json
{"request_id":"<UUID>","operation":"install"}
```

Operações aceitas pelo contrato: `install`, `update` e `uninstall`, desde que o perfil MSI aprovado tenha essa operação configurada. A materialização exige usuário técnico ativo e `agents.execute_softwarepackageoperation`, endpoint online com identidade UUID/machine_id única, capability recente, pacote e perfil aprovados para o hash/revisão atuais, integridade do artefato e ausência de conflito de jobs. `NIGHTOWL_SOFTWARE_OPERATION_DISPATCH_ENABLED` controla o despacho, não a persistência auditada: com a flag `False`, a API pode materializar o job idempotente, mas o pull o mantém `queued` e não o entrega ao agente. Jobs têm expiração limitada; antes de qualquer pull posterior, todo o estado é revalidado.

A transação grava `AgentJob`, `SoftwarePackageOperation` com snapshot imutável e evento de auditoria. O `request_id` é globalmente único; replay da mesma intenção retorna o mesmo registro/job, e reutilização em outra intenção é conflito. Substituição e exclusão do pacote ficam bloqueadas enquanto houver job ativo; operações históricas impedem exclusão definitiva do cadastro.

O payload do job usa `schema_version: 2` e contém somente IDs, operação, hash e revisões. Não contém URL, caminho local, credencial nem comando. Com despacho habilitado, o pull revalida identidade, permissão do operador, aprovação, perfil, hash do arquivo, capability e conflitos; se qualquer autorização ou requisito for revogado, cancela e audita. Um agente sem `X-NightOwl-Job-Protocol: 2` não recebe o envelope v2.

## Distribuição privada

O agente baixa `GET /api/agent/software-operations/jobs/{job_uuid}/artifact/` com o token existente no cabeçalho `Authorization: Bearer`. A rota confere que o job pertence à máquina autenticada, que está em estado elegível e que o snapshot continua válido. O arquivo é transmitido como `FileResponse`, sem URL pública ou token em query string, com `Cache-Control: private, no-store` e auditoria.

## ACK v2

Um `completed` só é aceito pelo ACK existente quando o envelope identifica job/operação/pacote/hash/revisões e traz `phase: verified`, código 0 ou 3010 e evidência MSI ProductCode/versão coerente com o perfil. 3010 exige `reboot_pending: true`; o servidor não agenda reboot. Resultados sem evidência, truncados, incompatíveis ou com identidade divergente são rejeitados. A chave de idempotência e os recibos existentes preservam o replay de ACK.

## Capability exigida

| Operação | Capability futura do executor |
| --- | --- |
| Instalar MSI | `software_msi_install:1` |
| Atualizar MSI | `software_msi_update:1` |
| Desinstalar MSI | `software_msi_uninstall:1` |

Essas capabilities devem aparecer somente em heartbeat recente de agente que implemente o contrato B2. `software_install_profile:1` de RC49-03C-A indica disponibilidade de perfil, não é suficiente para executar operações.

## Migration e testes

A migration `0062_rc49_software_package_operations` depende da `0061_rc49_software_execution_profiles` e cria apenas a trilha de operação e seus vínculos. Ela ainda não foi aplicada operacionalmente.

Os testes cobrem flag padrão desligada com job mantido na fila sem despacho, materialização/replay, snapshot imutável, bloqueio por revogação, download privado e cruzado, CSRF, ACK com evidência, compatibilidade v1 e concorrência no PostgreSQL. Não há teste que execute MSI nem alteração no agente Windows.

## Requisitos para RC49-03C-B2

- Nova release Windows assinada; agentes existentes devem permanecer sem as capabilities acima.
- Download autenticado por bearer no cabeçalho, streaming para staging protegido e verificação local do tamanho, SHA-256, revisão e assinatura segundo política explícita.
- Invocação fixa de `msiexec.exe` sem shell e sem argumentos arbitrários; timeout e journal local por operação.
- Recuperação após interrupção por consulta do ProductCode e versão, sem repetir execução cegamente.
- ACK de conclusão baseado em estado instalado observado; 3010 reporta reboot pendente e nunca reinicia sozinho.
- Definir antes da homologação a política Authenticode e confirmar a identidade/privilégios do serviço Windows.

Até B2 e homologação, manter o despacho desabilitado. Nenhuma operação real ou publicação de agente foi realizada por B1.
