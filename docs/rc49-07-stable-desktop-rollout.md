# RC49-07: rollout Stable para desktops

O perfil Stable é opt-in (`NIGHTOWL_STABLE_DESKTOP_ROLLOUT_ENABLED`, falso por padrão). Ele depende dos gates existentes de orquestração, execução automática e de um agendamento operacional dos comandos existentes de processamento, reconciliação e governança.

Classifique desktops físicos Windows 10/11 gerenciados no grupo `stable-desktops`. Modelo/fabricante não informados, servidores, VMs reconhecidas e grupos protegidos (incluindo piloto e laboratório) ficam fora. Não use hostname para classificar. O perfil respeita canal, política, janela, versões, assinatura e grupos; não altera essas configurações.

Publicar, promover ou despausar uma release Stable a 100%, com as permissões de release e campanha, congela os alvos elegíveis em uma campanha e inicia sua primeira onda. A concorrência da onda (`NIGHTOWL_STABLE_DESKTOP_ROLLOUT_BATCH_SIZE`, padrão 25) é o limite de lote, não o percentual de cobertura. Alvos offline no snapshot aguardam heartbeat sem ocupar vaga. Novas máquinas inscritas depois do snapshot não são silenciosamente adicionadas. Uma release sem alvos elegíveis não abre campanha.

Use a API atual de campanha para progresso, motivos, pausa, retomada e cancelamento. Progresso corresponde a resultados reconciliados, não a jobs apenas criados. Pausa impede novo despacho, mas não cancela execução já entregue; jobs em fila conservam a validade existente. Cancelar não recria campanha automaticamente. Perfil, identidade, versão, assinatura e política são verificados no despacho. O fluxo direto de atualização automática Stable fica bloqueado enquanto o perfil está ativo; atualização manual explícita permanece independente.

**Escopo desta entrega:** campanhas com conjunto fixo de alvos. A cobertura contínua dos desktops cadastrados ou tornados elegíveis após a publicação é um incremento complementar. O retorno de um alvo offline já incluído na campanha não depende desse incremento.

## Atualização de desenvolvimento 07B — 27/11/2026

A seção inicial acima documenta a **PR #75 (07A)** com alvos fixos. Não interpretar a afirmação histórica de ausência de catch-up como situação geral do código em desenvolvimento.

- **07B-1 integrada, PR #79:** `AgentRolloutCoverageRound` e migration `agents.0059_stable_coverage_round` preservam o cohort/snapshot original, sem reabrir campanhas concluídas ou abortadas.
- **07B-2 em PR #83, sem merge:** branch `codex/rc49-07b2-stable-catchup`, HEAD `175acca35b10d11f4294dc66b93cdb688be5274a`, [CI 5/5](https://github.com/Gabriel-J-Oliveira/rmm_project/actions/runs/46008929843) no HEAD antigo. Adiciona detecção recorrente de recém-elegíveis/novos desktops, rodadas com campanhas executoras complementares, locks, checks de elegibilidade, proteção de pausa/cancelamento/release substituída. **Não implementa retries generalizados.** Exigir nova validação depois de sincronizar.
- **Migration pendente:** a branch #83 introduziu `0062_stable_coverage_execution_campaign`, enquanto a main já contém `0062_rc49_software_package_operations` pela #82. Se a migration do catch-up não foi aplicada a DB persistente, renumerar para `0063_stable_coverage_execution_campaign` e depender da `0062` integrada após merge normal da main; caso contrário, reconciliar sem modificar histórico aplicado. Conferir `agents/views.py`, comum às PRs #82/#83/#84, e todo o CI.
- **07B-3/4 futuros, negociados por incremento:** classificar erros transitórios, retries limitados/backoff, `already_current` comprovado, reconciliação com recibo+versão+saúde, operação e observabilidade de cobertura por campanha/rodada; sem reativar campanha abortada ou duplicar job.
- Stable continua opt-in com `NIGHTOWL_STABLE_DESKTOP_ROLLOUT_ENABLED=False` por padrão, restrito a desktops físicos Windows elegíveis no `stable-desktops`. 100% significa cobertura ao longo do tempo dos **elegíveis**, não instalação simultânea nem inclusão de VMs/servidores. Despacho, publicação em massa e mudanças em endpoints ainda não autorizados por este documento.

[Handoff central](rc49-handoff-contexto-20261127.md) | [Codex 1](rc49-codex1-handoff-20261127.md).
