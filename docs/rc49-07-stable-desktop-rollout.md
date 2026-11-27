# RC49-07: rollout Stable para desktops

O perfil Stable é opt-in (`NIGHTOWL_STABLE_DESKTOP_ROLLOUT_ENABLED`, falso por padrão). Ele depende dos gates existentes de orquestração, execução automática e de um agendamento operacional dos comandos existentes de processamento, reconciliação e governança.

Classifique desktops físicos Windows 10/11 gerenciados no grupo `stable-desktops`. Modelo/fabricante não informados, servidores, VMs reconhecidas e grupos protegidos (incluindo piloto e laboratório) ficam fora. Não use hostname para classificar. O perfil respeita canal, política, janela, versões, assinatura e grupos; não altera essas configurações.

Publicar, promover ou despausar uma release Stable a 100%, com as permissões de release e campanha, congela os alvos elegíveis em uma campanha e inicia sua primeira onda. A concorrência da onda (`NIGHTOWL_STABLE_DESKTOP_ROLLOUT_BATCH_SIZE`, padrão 25) é o limite de lote, não o percentual de cobertura. Alvos offline no snapshot aguardam heartbeat sem ocupar vaga. Novas máquinas inscritas depois do snapshot não são silenciosamente adicionadas. Uma release sem alvos elegíveis não abre campanha.

Use a API atual de campanha para progresso, motivos, pausa, retomada e cancelamento. Progresso corresponde a resultados reconciliados, não a jobs apenas criados. Pausa impede novo despacho, mas não cancela execução já entregue; jobs em fila conservam a validade existente. Cancelar não recria campanha automaticamente. Perfil, identidade, versão, assinatura e política são verificados no despacho. O fluxo direto de atualização automática Stable fica bloqueado enquanto o perfil está ativo; atualização manual explícita permanece independente.

**Cobertura complementar (07B-2):** o ciclo existente de `process_agent_rollouts --run-once`
descobre desktops novos ou recém-elegíveis depois que a campanha Stable original aprovada
termina. Não reabre nem modifica o snapshot original. Cada rodada registra observações
novas ou alteradas; quando há alvos a atualizar, a rodada aponta para uma campanha
executora distinta da mesma release. Exclusões temporárias são reconsideradas, e
`already_current` exige um heartbeat recente e não conta como instalação executada.

O catch-up exige a release Stable mais recente por versão, assinada, publicada,
despausada e com rollout de 100%. Uma release mais nova pausada impede fallback.
Campanha ativa (inclusive pausada) impede nova campanha; campanha abortada veta
novas rodadas automáticas para a release. Despacho e entrega das complementares
revalidam a release aplicável e os controles existentes. Jobs já entregues continuam
sujeitos à reconciliação normal, sem cancelamento retroativo.

Os alvos anteriormente aprovados não voltam a entrar em campanhas complementares,
e jobs ativos impedem seleção pelo avaliador existente. Um alvo offline já
incluído na campanha original continua sob despacho normal; novos alvos offline
aguardam heartbeat antes da seleção complementar. No 07B-2 não havia retry
automático. Se a publicação não teve desktops elegíveis, a campanha-âncora vazia
é criada e aprovada/auditada no mesmo fluxo de publicação Stable, desde que o
publicador também esteja autorizado a gerir campanhas. O aprovador deve continuar
autorizado a criar e alterar campanhas.
Quando a campanha original foi aprovada para grupos específicos, as rodadas
herdam esse mesmo recorte.

## RC49-07B-3 — retries e reconciliação

Execução Stable registra cada AgentJob como uma tentativa imutável no ledger
`AgentRolloutAttempt`; o vínculo histórico `AgentRolloutTarget.agent_job` não é
reescrito. A evidência retryável atual é o erro explícito do updater
`UPDATE_DOWNLOAD_FAILED` em resultado final recebido; falhas genéricas de rede
não provam que a execução não começou e não são reenviadas automaticamente.
O limite é uma tentativa inicial e três adicionais, com delays configuráveis em
`NIGHTOWL_STABLE_ROLLOUT_RETRY_DELAYS_SECONDS` (padrão 900, 3600 e 14400
segundos). Timeout, ACK perdido, binding/identidade inválida, conflito de
recibo, assinatura inválida e estado incerto não recebem retry cego e ficam
aguardando reconciliação. Rollback exige intervenção; falha permanente e
esgotamento permanecem falhas terminais sujeitas à política existente de
auto-pause.

Enquanto aguarda backoff ou recuperação comprovada de espaço, o target permanece
não terminal, a wave não pode concluir, e métricas expõem separadamente o histórico
das tentativas e a falha terminal. O runner só reabilita retry de campanha Stable
que continua `running`; pausas manuais/segurança e abortos bloqueiam o despacho.
Cada retry cria outro job/ledger attempt sob os locks do runner/campanha/target;
revalidação normal de release, endpoint, política e lote continua obrigatória.

Se uma release Stable for publicada sem desktops elegíveis, `start_on_publication`
cria uma campanha-âncora vazia, completed e aprovada/auditada pelo publicador com
permissão de campanhas. Não há wave nem despacho; endpoints elegíveis posteriores
entram por rodadas complementares sob essa autorização. Abortos nunca são
recriados; campanhas não concluídas bloqueiam rodadas complementares.
**Escopo desta entrega:** campanhas com conjunto fixo de alvos. A cobertura contínua dos desktops cadastrados ou tornados elegíveis após a publicação é um incremento complementar. O retorno de um alvo offline já incluído na campanha não depende desse incremento.

## Atualização de desenvolvimento 07B — 27/11/2026

A seção inicial acima documenta a **PR #75 (07A)** com alvos fixos. Não interpretar a afirmação histórica de ausência de catch-up como situação geral do código em desenvolvimento.

- **07B-1 integrada, PR #79:** `AgentRolloutCoverageRound` e migration `agents.0059_stable_coverage_round` preservam o cohort/snapshot original, sem reabrir campanhas concluídas ou abortadas.
- **07B-2 integrada, PR #83:** merge `97a5f61518edf9d686f21dc84a23488f7dcb4770`, migration 0063.
- **07B-3 em desenvolvimento:** ledger imutável de jobs/tentativas, retry conservador 15m/1h/4h, limite de três retries; ambiguidades não são reenviadas e a governança continua aplicada. Requer CI final antes de revisão.
- **07B-4 futura:** interface/observabilidade agregada das rodadas, tentativas e exclusões.
- Stable continua opt-in com `NIGHTOWL_STABLE_DESKTOP_ROLLOUT_ENABLED=False` por padrão, restrito a desktops físicos Windows elegíveis no `stable-desktops`. 100% significa cobertura ao longo do tempo dos **elegíveis**, não instalação simultânea nem inclusão de VMs/servidores. Despacho, publicação em massa e mudanças em endpoints ainda não autorizados por este documento.

[Handoff central](rc49-handoff-contexto-20261127.md) | [Codex 1](rc49-codex1-handoff-20261127.md).
