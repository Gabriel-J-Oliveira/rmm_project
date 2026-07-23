# NightOwl Agent Windows 0.1.1.0-rc1

Release candidata para piloto controlado. Esta versao deve nascer no canal `development`, com rollout pausado e percentual `0`.

## Mudancas

- Consolida agente Windows, Tray, Updater e Diagnostics em um pacote unico.
- Mantem updater externo com download HTTPS, checksum, staging, backup, health check e rollback.
- Mantem jobs remotos com idempotencia, timeout, concorrencia e fila persistente de resultados pendentes.
- Mantem lifecycle local de install, repair, reinstall, uninstall e purge.
- Adiciona diagnostico local sanitizado e diagnostico operacional no painel.
- Adiciona distribuicao controlada por canal, grupos, janela de manutencao, version pinning e rollout deterministico.

## Compatibilidade

- Endpoints antigos continuam usando heartbeat, jobs e results existentes.
- Endpoints sem `update_channel` usam `stable`.
- `version.json` publico continua disponivel durante a transicao.
- O agente nao informa livremente o canal; o backend e a fonte de verdade.
- `minimum_updater_version` deve bloquear endpoints com updater incompatível.

## Procedimento de update

1. Gerar release com `scripts\Build-NightOwlAgentRelease.ps1`.
2. Publicar artefatos no servidor apenas em `downloads/agent/windows/releases/0.1.1.0-rc1/`.
3. Importar a release no backend com `import_agent_release`.
4. Manter `rollout_paused=true` e `rollout_percentage=0`.
5. Validar em endpoint de desenvolvimento.
6. Promover para `pilot` somente apos evidencia manual.

## Procedimento de rollback

- Rollback tecnico do agente continua sob responsabilidade do `NightOwl.Agent.Updater.exe`.
- Rollback do backend deve ser tratado com backup de banco, reversao do codigo e validacao de migrations.
- Release revogada nao e entregue para novos endpoints.
- Jobs pendentes de uma release revogada devem ser cancelados pelo backend.
- Nao ha downgrade automatico nesta etapa.

## Riscos conhecidos

- Testes Integration contra backend real ainda nao foram executados.
- Testes WindowsVm com instalacao/update/rollback reais ainda nao foram executados.
- Rollback e lifecycle ainda precisam de validacao em endpoint Windows descartavel.
- A arvore Git contem historico de `bin/obj` rastreado; `.gitignore` foi reforcado, mas a limpeza rastreada deve ser revisada separadamente.
- A release candidata nao deve ser promovida para `pilot` sem evidencia de endpoint real.

## Fora do escopo da RC

- Novas coletas.
- Novos jobs.
- Deploy de software.
- Scripts remotos arbitrarios.
- Reboot remoto livre.
- Automacoes de rollout sem aprovacao manual.
