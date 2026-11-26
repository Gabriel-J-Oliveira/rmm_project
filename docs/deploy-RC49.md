# NightOwl RC49 — planejamento de produto, contratos e roadmap

**Estado:** planejamento registrado em 24/11/2026, em discussão. **Não é autorização de implementação ou deploy.** As decisões já tomadas estão separadas de propostas e questões ainda abertas.

**Base técnica consultada:** RC48 consolidada no SHA `3afda56812f27f1a9bff47a0ac474326a24a1202` (PRs #54–#58). O agente RC48 assinado e backend foram publicados em 24/11, conforme handoff operacional informado pelo responsável. Este documento não altera código, releases, flags ou endpoints.

**Referências:** [deploy-RC48.md](deploy-RC48.md), [RC46 — automações](rc46-04b-automations.md), [RC47 — serviços fase B](rc47-windows-service-manager-phase-b.md), [autenticação/inventário AD](ad_auth_inventory.md).

## 1. Visão e filosofia da RC49

Transformar o NightOwl de monitoramento e execução manual de jobs em uma plataforma prática para administração e **manutenção automatizada de desktops Windows**, reutilizando o backend Django, o protocolo de jobs v2/ACK, a fila persistida, os agendamentos e a identidade dos endpoints.

Diretrizes acordadas:

1. **Não reiniciar a homologação de funcionalidades antigas a cada release.** Preservar configurações operacionais aprovadas; validar o delta funcional e executar apenas smoke de regressão para recursos existentes.
2. **Separar funções do agente das telas.** Fechar contratos e capacidades Windows quando necessário; telas podem evoluir sem recompilar/republicar agente por mera mudança de apresentação.
3. **Uma única infraestrutura de jobs e agendas.** Não criar scheduler, fila, executor Windows ou transporte paralelos sem necessidade comprovada.
4. **Operação objetiva, com evidência real.** Defeitos reproduzidos são corrigidos; não multiplicar ambientes temporários e backups para cada mudança pequena. Backups adicionais são pertinentes para alterações relevantes de dados/schema.
5. **Identidade e permissões permanecem reais.** A rapidez operacional não implica aceitar hostname como identidade, executar scripts arbitrários ou retirar autenticação/auditoria.

## Regra obrigatória — continuidade operacional entre releases

Upgrade, downgrade, atualização do backend, reinicialização ou reinstalação **não pode desabilitar uma funcionalidade que já foi homologada e autorizada**. Esta regra vale para RC49 e para os próximos ciclos.

- Tratar `.env` e demais configurações persistentes como estado operacional: promoção de código não as substitui, normaliza nem redefine para defaults. Não copiar `.env` de um checkout/release, gerar configuração nova por default, nem habilitar permissões por fallback.
- Antes/depois de cada promoção ou restart, comparar os valores efetivos do Django e o estado das unidades systemd com o inventário de capacidades aprovadas. Validar escopo por UUID, operador, ação e capability; não basta comparar variáveis textuais.
- Preservar o estado de inicialização de serviços separadamente do estado de execução temporária. O relay assistido pode ser parado após uma sessão de homologação sem ser habilitado permanentemente; não converter isso em autorização para iniciar automaticamente um serviço que estava intencionalmente desabilitado.
- Nunca transformar continuidade em ampliação de privilégios: `admin_auth`, grants, permissões RBAC, allowlists e scopes permanecem sujeitos às autorizações próprias. Um grant expirado/revogado não é renovado automaticamente. A feature continua instalada e configurada, mas indisponível até autorização válida.
- Quando a compatibilidade do agente impedir uma função, reportar a capability/motivo exatos. Não declarar suporte no heartbeat, relaxar RBAC, criar grants, ativar flags administrativas ou alterar a política para ocultar incompatibilidade.
- O checklist de deploy deve registrar o estado anterior e posterior sem segredos: flags aprovadas, escopos, grants (modo/expiração, não credenciais), capabilities recentes, unidades e estado enabled/active, além de uma comparação que falhe se houver regressão não explicada. Mudanças intencionais de segurança exigem aprovação e registro separados.

**Gate de continuidade:** NO-GO se uma função homologada deixar de estar disponível por diferença de configuração, permissão, grant, serviço ou capability não explicada. Corrigir a causa persistente, adicionar um teste de regressão ao componente responsável e repetir o gate antes de promover. Não aceitar “reativar manualmente após cada RC” como procedimento operacional.

## 2. Decisões confirmadas pelo responsável

| Tema | Decisão confirmada | Consequência técnica |
| --- | --- | --- |
| Gerenciador de Serviços | **Concluir o agente 100% quanto ao contrato de serviços na RC49**; o desenho da interface será definido posteriormente, mas **ainda na RC49**. | Inventariar e versionar todo o contrato Windows antes de fechar o incremento do agente; evitar nova publicação causada apenas por uma tela. |
| Canal Development | Canal de testes e homologação em VMs e endpoints explicitamente escolhidos. | Não migrar VMs de laboratório para Stable de forma automática. |
| Canal Stable | Ao publicar uma nova versão **Stable**, desejar atualização automática de **100% dos desktops Windows gerenciados e elegíveis**. | Cobertura integral como alvo final, inclusive desktops temporariamente offline quando retornarem. Não confundir 100% de cobertura com despacho simultâneo para toda a frota. |
| Escopo da atualização Stable | Desktops Windows; VMs de teste e servidores não pertencem ao conjunto-alvo. | Classificação de dispositivo e exclusões explícitas; não confiar exclusivamente em hostname. |
| Políticas de manutenção | Evoluir a área existente de Políticas de Execução para executar operações como Windows Update controlado e limpeza periódica de disco. | Reaproveitar automações e AgentJob existentes; implementar novos jobs tipados e condições. |
| Regras de software | Preferir Bitdefender para bloqueio/permissão de aplicativos. | Não priorizar duplicar controles de aplicações no NightOwl; preservar o módulo atual até decisão própria de navegação/migração. |
| Reboot em políticas | Ter uma opção chamada **“Se necessário reiniciar, como será feito?”**, com comportamento configurável **por política**. | Decidir por política entre reinício automático em janela, solicitação ao usuário ou autorização técnica. |
| Escopo AD | Desejável vincular políticas a **grupos de segurança de computadores do AD**, além de todos, domínios, OUs e endpoints específicos. | Avaliar vínculo LDAP de contas de computador e identificadores estáveis; não confundir grupo do usuário com grupo da máquina. |
| Demais iniciativas | Manter configuração operacional persistente, terminal administrativo, rollout, scheduler de análises, estabilidade e proposta de contas locais. | Definir ordem e esforço após fechar os contratos de políticas. |

**Ainda não decidido:** opções finais e parâmetros padrão das políticas, semântica precisa de reinicialização/notificação, regras de precedência, catálogo final de limpeza, limites de carga, recorrência de sincronização AD e inclusão de contas locais no escopo obrigatório da RC49.

## 3. Estado real reaproveitável da RC48/RC47

### 3.1 Já disponível no repositório

- `AgentJob`, jobs v2, pull/start/result/ACK, identidades `AgentMachine`, updater assinado e canais.
- `AutomationDefinition`, `AutomationOccurrence`, scheduler de jobs, health, agendamento imediato/único/diário/semanal/mensal/intervalo e opções de endpoint offline (`wait`, `skip`, `expire`). Os alvos atuais são snapshots de endpoints individuais por UUID, com limite de 50 por definição.
- Políticas de Execução em `templates/dashboard/job_automations.html`: UI de agendas, seleção explícita de endpoints e histórico de ocorrências.
- Políticas de Software em `templates/dashboard/software_policies.html`: experiência visual de criação/edição, tipos de regra, escopo (todos, prefixo/trecho de hostname, domínio, endpoints específicos), exceções, violações e auditoria.
- Agente Windows com `windows_update_scan` **somente para consulta**, e não instalação de updates.
- Consulta paginada de serviços `collect_services_paged:2` e ações de serviço restritas; capacidades e permissões ainda dependem de configuração e heartbeat.
- Estruturas `ADGroup`, `ADUser`, `ADGroupMembership`, LDAP e descoberta de computadores; a descoberta existente projeta SID, FQDN, DN e OU de contas de computador.
- Scheduler de análise persistida de telemetria com saúde/circuit breaker (separado do scheduler de políticas); sua ativação e primeiro ciclo continuam decisões operacionais.

### 3.2 Limitações atuais que **não** devem ser confundidas com recursos prontos

- `windows_update_scan` **não instala nem reinicia** o Windows.
- Políticas de Execução existentes ainda **não** executam limpeza de disco ou Windows Update instalado.
- O scheduler atual materializa jobs para alvos explicitamente congelados, não resolve automaticamente membros de grupos de computadores AD.
- `ADGroupMembership` representa usuários/grupos, não associações completas de **contas de computador** a grupos de segurança.
- O rollout atual tem políticas manual/notify/automatic/maintenance e gates; publicar Stable não significa que toda a frota atualizará sem adaptação.
- Contratos de contas locais existentes bloqueiam execução real de ações de credencial até completar mecanismo adequado.

## 4. RC49-A — Gerenciador de Serviços: contrato Windows completo antes da UI

**Decisão de escopo:** o responsável descreverá a interface depois, **dentro da RC49**. O objetivo imediato é não precisar publicar outro agente apenas para implementar essa interface.

### Contrato alvo do agente

1. Enumeração paginada, estável e com identificação por nome interno de serviço.
2. Nome amigável, descrição quando disponível, estado, tipo de inicialização, identidade/conta de execução quando apropriado e flags de capacidade.
3. Detalhes do serviço e dependências diretas/dependentes, com indicação dos estados relevantes.
4. Ações tipadas e auditáveis: consultar, iniciar, parar, reiniciar; avaliar **alteração do tipo de inicialização** como requisito a decidir antes de congelar contrato.
5. Resultado confirmado após a ação: estado anterior/final, código de falha, duração e correlação do job.
6. Tratamento de permissões, serviços protegidos, dependências e conflitos; não aceitar shell arbitrário.
7. Capabilities versionadas, compatibilidade retroativa e documentação de schema, paginação, timeouts e limites.
8. Operação individual via UUID/machine ID, sem seleção ambígua por hostname.

### Critério de fechamento do agente

- Contrato v1/v2 documentado, casos completos no .NET, testes de serviço simulado e real em VM Windows, telemetria de erros e confirmação pelo heartbeat.
- A interface não precisa ter desenho final nesta fase; **deve ser possível construir a UI prevista sem uma nova alteração do contrato do agente**.
- Antes de congelar o agente, fechar explicitamente as decisões de propriedades editáveis e tipos de ação suportados. Não prometer cobertura de operações futuras ainda desconhecidas.

## 5. RC49-B — Stable com atualização para 100% dos desktops

### Comportamento de produto decidido

- `development`: atualização de VM de teste / endpoint selecionado, principalmente manual.
- `stable`: toda release Stable assinada e publicada deve ser direcionada automaticamente a **todos os desktops Windows gerenciados elegíveis**, inclusive máquinas offline quando retornarem.
- A cobertura desejada é 100%, **sem simultaneidade obrigatória**. O backend pode despachar em lotes técnicos, com limites de concorrência e retomada automática até atingir cobertura.
- Servidores Windows, máquinas não Windows e VMs de laboratório devem ficar fora da elegibilidade Stable automaticamente, com possibilidade de exclusão explícita.
- Permitir ações de pausar/cancelar campanhas, rastrear resultados e diagnosticar endpoints não atualizados.

### Requisitos técnicos

1. Critério de classificação confiável: `Windows desktop` vs `Windows Server` vs `VM teste`, combinando inventário com categoria/atribuição administrativa explícita; não inferir apenas por nome ou IP.
2. Definir `stable eligible` pela identidade do agente, canal, capacidade do updater, release válida, versão, estado de instalação e política aplicável.
3. Publicação Stable inicia ciclo de cobertura; jobs com idempotência e reconciliação, evitando duplicatas e repetição de update entregue.
4. Caso offline: reagendar quando houver retorno e janela apta, sem considerar o endpoint concluído enquanto não reportar versão/saúde.
5. Retries limitados com backoff, falhas de assinatura e incompatibilidade destacadas; fornecer caminho de bootstrap quando updater for muito antigo.
6. Indicadores: elegíveis, atualizados, pendentes, offline, bloqueados, falharam, rollback e tempo desde publicação.
7. Pós-update: versão real, heartbeat recente e saúde mínima; não declarar sucesso apenas pela criação do job.
8. Preservar rollback do agente e tratamento de release revogada/superseded.
9. Ajustar políticas atuais `manual`/`notify_only`/`automatic`/`maintenance_window` com regra clara de precedência para Stable; a estratégia de migração das máquinas hoje em manual precisa ser documentada.
10. Explicitar se `stable 100%` é regra mandatória sem opt-out ou se aceita exceções administrativas (exceções de classificação e operacionais já indicadas acima).

**Não é objetivo** tornar 100% da frota simultânea nem incluir automaticamente VMs/servidores.

## 6. RC49-C — Políticas de manutenção e execução (principal novo incremento)

### 6.1 Modelo de política

Uma política reúne:

1. **Identidade:** nome, descrição, responsável, revisão, ativa/pausada.
2. **Ação tipada:** Windows Update, limpeza de disco e futuras rotinas aprovadas; catálogo versionado sem PowerShell livre.
3. **Alvos:** todos os desktops, domínio, OU, grupo AD de computadores, endpoints específicos e exclusões.
4. **Agenda:** execução imediata, única, diária, semanal, mensal ou intervalo, com fuso; janela de manutenção opcional.
5. **Condições locais:** métricas atuais e pré-requisitos (armazenamento, CPU, memória, energia, uso ativo, rede, pendência de reboot etc.).
6. **Tratamento de offline/atraso:** aguardar até a próxima janela, ignorar, expirar ou adiar; sem executar carga atrasada de uma vez.
7. **Reinicialização:** campo obrigatório quando a ação puder requisitar reboot.
8. **Resultado:** não iniciado, aguardando condição, executando, concluído, concluído com reinício pendente, falhou, expirou, ignorado, rollback/resultado incerto quando aplicável.
9. **Auditoria e observabilidade:** operador, revisão da política, alvos resolvidos, motivo de decisão, job, horários, retorno e métricas antes/depois.

### 6.2 Campo de produto decidido: reinicialização

**Rótulo na interface:** `Se necessário reiniciar, como será feito?`

**Opções de comportamento propostas para disponibilizar por política:**

- **Reiniciar automaticamente na janela definida:** nunca reiniciar fora da janela; verificar usuário ativo e as regras da política no momento da ação.
- **Notificar usuário e permitir escolha/adiamento:** apresentar solicitação clara, prazo e regras de adiamento definidos na própria política.
- **Exigir autorização técnica para reiniciar:** instalar/executar a manutenção, registrar `reboot_pending` e aguardar autorização explícita do operador.
- **Não reiniciar automaticamente:** registrar pendência para intervenção posterior (opção candidata, útil para casos sem janela).

Essas opções são **propostas de parâmetros**, não defaults aprovados. O responsável confirmou o campo e a seleção por política, mas não fixou todos os seus valores e limites.

Para todas as opções: verificar o estado de reboot pendente, prevenir duplo reinício, guardar o job/ocorrência através do reboot, auditar o resultado pós-boot e respeitar o estado real da sessão/energia. Não considerar instalação de update sinônimo de instalação + reinício concluídos.

### 6.3 Política exemplo 1 — Windows Update controlado

**Exemplo ilustrativo, não configuração padrão homologada:**

| Campo | Exemplo |
| --- | --- |
| Nome | Atualização Windows — Desktops |
| Alvos | Grupo de computadores AD `GG-NightOwl-Desktops` |
| Frequência | Semanal, quarta-feira às 20h |
| Classes | Atualizações de segurança e qualidade; drivers e upgrades de versão separados |
| Disco livre mínimo | 15 GB |
| Energia | Exigir alimentação AC em notebooks |
| Usuário ativo | Permitir instalação, reinício conforme a opção específica |
| Offline | Esperar próxima janela elegível |
| Reboot | “Se necessário reiniciar, como será feito?”: opção selecionada nesta política |

Fluxo proposto:
1. Descobrir origem/políticas Windows Update (Windows Update/WSUS/gestão existente) e compatibilidade; não sobrescrever GPO.
2. Consultar atualizações elegíveis e registrar identificadores/KB, categorias, aplicabilidade e status.
3. Revalidar métricas atuais e pré-requisitos **no agente** na hora da execução; dados antigos no dashboard não autorizam intervenção.
4. Baixar e instalar por API suportada pelo Windows; impor timeout, progresso e limites de simultaneidade.
5. Registrar atualização aplicada/falha e necessidade de reinício.
6. Se necessário, aplicar o comportamento de reboot configurado; após boot, reconciliar e confirmar resultado.

**A implementar:** novos jobs tipados de busca/instalação/progresso/reboot/reconciliação; `windows_update_scan` atual é apenas leitura. Decidir política de drivers, upgrades de versão, atualização automática pré-existente e falhas parciais.

### 6.4 Política exemplo 2 — Limpeza inteligente de disco

**Exemplo ilustrativo, não padrão:**

| Campo | Exemplo |
| --- | --- |
| Nome | Limpeza automática do C: |
| Alvos | Todos os desktops físicos |
| Avaliação | Diária, 12h |
| Gatilho | Disco C: com ocupação acima de 85% |
| Escopo | Categorias aprovadas de arquivos temporários e componentes de limpeza suportados |
| Exceções | Arquivos pessoais, caches de aplicações não aprovadas e caminhos do sistema protegidos |
| Resultado | Espaço antes/depois, bytes recuperados, itens ignorados, duração e falhas |

Fluxo proposto:
1. Medir disco e conferir gatilho, idade dos dados e espaço.
2. Classificar candidatos usando categorias permitidas; oferecer estimativa/preview quando viável.
3. Revalidar restrições no agente e executar operação tipada.
4. Medir novamente e apresentar `bytes reclaimed`, status e erros parciais.
5. Evitar execução redundante em curto intervalo (cooldown).

**A decidir:** categorias permitidas, tempo mínimo de arquivos temporários, tratamento da Lixeira, limites por execução e necessidade de reboot. Sem deleção genérica por caminho arbitrário enviado pelo backend.

### 6.5 Outras políticas candidatas (não aprovadas para desenvolvimento ainda)

- Reinício agendado controlado.
- Limpeza/manutenção de caches específicos.
- Recuperação de serviço elegível com condição de parada/falha.
- Verificação e reparo de componentes Windows.
- Rotinas de manutenção de software aprovadas.
- Coleta ampliada de diagnósticos ao disparar um alerta de desempenho.

Nenhuma candidata passa automaticamente ao escopo da RC49. Discutir valor, contrato Windows, métrica de entrada e efeito antes da implementação.

## 7. RC49-D — Escopo por AD, grupos de computadores e OUs

**Objetivo desejado:** política seleciona `todos`, `domínio`, `OU`, `grupo de segurança AD de computadores` ou `endpoints específicos`. Também permite exclusões explícitas.

### Estado atual

- LDAP e descoberta de computadores já existem. `access_inventory/services/ad_computer_discovery.py` lê DN, SID, FQDN, nome e OU.
- `access_inventory.models.ADGroup` existe, mas `ADGroupMembership` atual aceita usuário ou grupo aninhado, **não vínculo direto com conta de computador**.
- `SoftwarePolicy` filtra todos, domínio, prefixo/trecho de hostname e endpoints específicos; esse filtro visual pode ser reaproveitado, mas **não deve ser confundido com escopo AD por computador já implementado**.
- O motor de agendamento atual congela os UUIDs dos alvos no momento da definição; grupos dinâmicos requerem nova etapa de resolução.

### Modelo proposto

1. Sincronizar **contas de computadores e sua associação com grupos AD**, usando identificadores estáveis como SID e DN. Registrar origem e momento da sincronização.
2. Resolver membership direta e, se habilitado, aninhada; não usar o grupo do usuário logado como atalho para decidir o grupo do computador.
3. Associar computador AD ao endpoint NightOwl preferencialmente por identidade estável e evidência do inventário, nunca apenas hostname homônimo.
4. Resolver dinamicamente membros elegíveis perto da materialização do job. Guardar **snapshot imutável** (identidade, política/revisão, grupo/OU consultado, timestamp) para auditoria.
5. Na próxima rodada, máquinas que ingressaram no grupo ficam elegíveis; máquinas que saíram deixam de receber **novos** jobs. Jobs já iniciados seguem sua semântica de execução e cancelamento real.
6. Não materializar para identidades ambíguas, AD indisponível além da tolerância definida, objetos desabilitados ou endpoints não gerenciados.
7. Permitir prévia: `N elegíveis, N offline, N excluídos, N sem agente, N com identidade ambígua`.
8. Definir políticas de cache/TTL, frequência de sincronização, precedência de múltiplos grupos e efeito de reatribuição/exclusão; **ainda aberto**.

**Escalabilidade:** o limite atual de 50 endpoints por definição pode ser suficiente para testes, não para políticas globais. Expandir materialização em lotes com limites e idempotência sem trocar o protocolo de jobs nem criar múltiplos schedulers.

## 8. RC49-E — Configuração operacional persistente

Objetivo: nenhuma release nova deve exigir habilitar manualmente a mesma função em diversos pontos, se ela já estava aprovada.

- Perfil operacional persistido por ambiente, função, escopo e grupo de operadores, exibido em administração.
- Projetar flags atuais, permissões, grants e capabilities como um estado coerente; exibir motivo real de indisponibilidade.
- Mudança de configuração aplicada à máquina pelo fluxo de configuração apropriado; heartbeat declara a **capacidade efetiva**, nunca falsa.
- Padrão de novas instalações e migração preserva as configurações aprovadas, sem escalar permissões ou ativar capacidades ainda não disponíveis no binário.
- Manter distinção entre terminal assistido, administrativo, consulta de serviços e ações de alteração.
- Evitar reabrir debates de flags em toda RC; não retirar identidades, trilha de auditoria ou expiração de autorizações quando necessárias.

**Aceite:** uma RC futura atualiza binários sem desabilitar funções já autorizadas ou perder preferências, e o painel informa a causa precisa quando uma capability está ausente.

## 9. RC49-F — Terminal assistido e administrativo

- Preservar terminal ConPTY assistido e consentimento atual.
- Completar fluxo de `admin_auth` com autorização explícita, identidade privilegiada verificada, credenciais protegidas e sem senha exposta em browser/logs.
- Melhorar reconexão, estado/expiração, terminal órfão, fechamento, redimensionamento e retorno de erro legível.
- Investigar issue #19 (`Get-Date` em ConPTY com token restrito) com reprodução concreta.
- Manter gates de autorização por usuário/endpoint/mode; nenhuma elevação implícita por conta de `is_staff`.

**Aceite:** sessões assistidas e administrativas criadas, usadas e encerradas sem orphan; evento auditado e privilégio observado corresponde ao modo.

## 10. RC49-G — Scheduler de análises persistidas de telemetria

Reutilizar RC47/RC48; **não confundir com o motor de políticas de execução**.

- Confirmar invocador efetivo, cadência, capacidade, limites de banco e health.
- Definir primeira captura e distribuição inicial; histórico indicava `unconfigured`.
- Ativar, se decidido, em janela autorizada, observando throughput, circuit breaker, falhas e retry.
- Exibir análises com qualidade/proveniência, sem inventar métricas ausentes ou misturar legacy/v2.
- Tendências avançadas e recomendações inteligentes são evolução adicional, não parte já implementada do scheduler.

## 11. RC49-H — Contas locais e confiabilidade

**Contas locais — candidato opcional à RC49 / RC50:**

- Consultar, criar e habilitar/desabilitar contas locais elegíveis.
- Completar entrega protegida de segredos, escopo, autorização, confirmação do SID e auditoria antes de permitir troca de senha.
- Não misturar gerenciamento de contas AD neste primeiro incremento.

**Estabilidade contínua:**

- Resolver falhas reproduzíveis Chrome/CDP e Repair Runner; um CI verde não comprova causa.
- Corrigir problemas reais de terminal e jobs sem revalidar toda a aplicação a cada ajuste.
- Garantir inicialização e recuperação do relay ASGI após reboot, com processo único.
- Melhorar mensagens de falha, status pendente/resultado incerto e idempotência.

## 12. Estrutura de execução reutilizada

```text
Política (ação tipada + alvos + agenda + condições + reboot)
  -> Resolução dinâmica de escopo (NightOwl / AD computadores)
  -> Scheduler atual (ocorrências persistidas e revisão)
  -> Reavaliação de elegibilidade + materialização AgentJob
  -> Pull/start v2 -> agente Windows revalida condições atuais
  -> Execução nativa controlada / progresso
  -> Resultado + ACK -> histórico e indicadores
  -> Se aplicável: reboot pendente -> confirmação pós-boot
```

Separar claramente:
- **Política**: regra desejada e seu contexto.
- **Ocorrência**: uma janela agendada para a política.
- **Job**: execução em um endpoint específico.
- **Resultado**: fato observado pelo agente, não sucesso inferido do despacho.

Antes da execução: condições vindas do backend ajudam a selecionar; condições voláteis (espaço livre, energia, usuário, reboot pendente) devem ser revalidadas **localmente**. Condições falsas geram estado `skipped/deferred` com motivo, não execução forçada nem sucesso.

## 13. Proposta de interface de Políticas de Execução (direção, não mock definitivo)

Reutilizar os padrões visuais de Políticas de Software, mas focar em **manutenção e execução**.

Seções candidatas:
1. **Identidade:** nome, descrição, responsável, ativa/pausada.
2. **O que fazer:** catálogo de ações (Windows Update, limpeza, outros aprovados).
3. **Onde aplicar:** todos os desktops, domínio, OU, grupo AD de computadores, endpoint; inclusões/exclusões.
4. **Quando:** horário, frequência, fuso e janela.
5. **Somente se:** condições e métricas, com operadores `>=`, `<=`, `=`, limites de idade/cobertura.
6. **Se necessário reiniciar, como será feito?** Opções por política do item 6.2.
7. **Falhas/offline:** retry, validade, próxima janela, limite de concorrência.
8. **Prévia e histórico:** alvos concretos, motivos de exclusão, ocorrências, jobs, resultado e métricas antes/depois.

**Regra:** o usuário configura uma política legível, não edita JSON/cron ou passa comandos PowerShell arbitrários para executar nos desktops.

## 14. Roadmap proposto, sujeito a refinamento

| Ordem sugerida | Frente | Entrega incremental |
| --- | --- | --- |
| 1 | Fechamento RC48 | Concluir desbloqueio/homologação funcional dos recursos já integrados; não carregar regressões incertas como recursos novos. |
| 2 | Contratos do agente RC49 | Completar contrato de serviços e definir novos jobs nativos para Windows Update e limpeza **antes** de congelar versão do agente. |
| 3 | Motor de políticas | Estender automações existentes com condições, janela, revisão, estados e política de reboot. |
| 4 | Integração AD | Grupos de computadores, OUs, associação e prévia de alvos; materialização dinâmica em lotes. |
| 5 | Stable integral | Seleção automática de todos os desktops elegíveis, reconciliação, retries, visibilidade de falhas e atualização de retorno offline. |
| 6 | UI de políticas | Compor a experiência da tela com os filtros existentes e fluxo novo de configuração por política. |
| 7 | UI de serviços | Definir com o responsável, **ainda na RC49**; consumir contrato de agente previamente congelado. |
| 8 | Operação e acabamento | Configuração persistente, ConPTY/admin, scheduler de análises, estabilidade e testes reais. |
| Opcional | Contas locais | Se não houver capacidade, tratar em RC50 sem comprometer a entrega principal. |

Essas são **frentes de planejamento**, não PRs já autorizadas, nem um compromisso de implementar todas simultaneamente.

## 15. Critérios de aceite da RC49

- O agente da RC49 possui contrato de serviços completo **dentro do escopo acordado**, com API/capabilities versionadas; interface definida e entregue ainda na RC49.
- A publicação de release no canal Stable direciona atualização automática para 100% dos desktops Windows gerenciados elegíveis, sem incluir servidores/VMs de laboratório e sem confundir pendente com sucesso.
- Política Windows Update executa instalação real em ambiente Windows de teste, respeita condições, fonte de update, estados e regra de reboot por política.
- Política de limpeza executa apenas categorias aprovadas, calcula espaço antes/depois e audita resultados.
- Políticas suportam alvos explícitos e, após implementação/validação, grupo de **computadores** AD e OU com vínculo de identidade confiável.
- Backend e agente revalidam condições e resultados; jobs e ocorrências são idempotentes e observáveis.
- Recursos antigos homologados continuam disponíveis após a atualização; smoke de regressão curto, sem repetir toda a homologação.
- Nenhum ponto é marcado como concluído só porque o CI passou: funcionalidades novas devem ser demonstradas em VM/desktop real quando aplicáveis.

## 16. Decisões abertas para a próxima conversa

1. Quais **classes** de Windows Update o NightOwl instalará (qualidade, segurança, drivers, feature updates)? O que fazer se houver WSUS/GPO?
2. Quais são exatamente as opções do campo **“Se necessário reiniciar, como será feito?”**? Prazo máximo de adiamento, interação com usuário, janela padrão e prioridade de regras?
3. Quais **categorias de limpeza** são elegíveis, incluindo Lixeira, caches e temporários de perfis? Qual idade mínima e quais exclusões?
4. Quais condições serão oferecidas no construtor de políticas v1 (disco, energia, CPU, memória, sessão ativa, conexão, tempo desde última execução)?
5. Como lidar com **políticas conflitantes**, múltiplos grupos/OU e exceções por endpoint?
6. Qual frequência de sincronização AD, TTL tolerado e política de execução se LDAP não responder?
7. O canal Stable permite exceção temporária para desktop com incompatibilidade ou pausa operacional? Como se resolve manual/notify/maintenance já configurado?
8. Alteração do **tipo de inicialização** e propriedades de serviços entram no contrato de agente RC49?
9. Quais componentes de terminal administrativo e contas locais são obrigatórios para encerrar RC49 versus candidatos a RC50?
10. Como separar a navegação entre **Políticas de Software** (hoje existentes) e **Políticas de Execução**, sem duplicar controles do Bitdefender?

**Próximo passo:** continuar a discussão dos requisitos e fechá-los no documento antes de iniciar o recorte técnico em PRs. Nenhum deploy, release, migration ou atualização de frota é autorizado por este arquivo.

## Estado de desenvolvimento RC49-01B — 25/11/2026

RC49-01B integrada à `main` em dois merge commits normais, na ordem dependente:

- PR #64, identidade patrimonial, merge `d70870990f26f02054b6c4d5d56eec1de1911223`.
- PR #65, remoção verificada, merge `3264af7e5401489d134b9006a10fcdf90b07e0f9`.
- `main` após a integração `3264af7e5401489d134b9006a10fcdf90b07e0f9`.

Migrations da 01B: 0052 (identidade SMBIOS), 0053 (auditoria durável de remoção) e 0054 (digest do receipt e proteção das relações de rollout). Aplicação operacional continua sujeita ao plano normal de deploy e validação do banco. Nenhuma migration foi aplicada como parte desta integração.

Os sete gates de cada PR passaram. A suíte Windows da #65 passou na segunda tentativa do mesmo SHA. Uma falha anterior `launch_failed` na sonda Chrome/CDP foi observada e não tem causa confirmada, portanto permanece como risco de estabilidade do CI. A homologação da limpeza real do agente requer VM Windows descartável autorizada e pacote compatível assinado.

Pendências antes de declarar RC49 pronta para distribuição incluem ensaio do inventário SMBIOS em hardware físico, substituição de placa-mãe com evidência, ciclo completo de remoção e reinstalação, falhas parciais/offline e retomada, além da revisão do processo de prévia e eventual processamento inicial da população existente. Não iniciar alteração em massa na frota nesta etapa.

## Atualização aprovada em 25/11/2026 — RC49-01B

A próxima fase passa a ser **identidade patrimonial automática e ciclo de vida dos endpoints**. A decisão vigente está em [RC49-01B — Identidade e ciclo de vida](rc49-01b-identidade-ciclo-de-vida.md) e prevalece sobre as restrições históricas de cadastro exclusivamente manual e remoção fora do escopo da 01A.

- UUID SMBIOS como evidência principal, serial complementar e UUID patrimonial interno imutável; cadastro/vínculo automático de hardware físico confiável, conflitos tratados por exceção, botão Validar vínculo com coleta real, sem edição livre de identificadores.
- Unificar uninstall e purge em Remover agente, com confirmação administrativa, limpeza confirmada, exclusão definitiva dos dados operacionais e preservação dos registros patrimoniais. Offline/falha permanece pendente até confirmação. Auditoria independente do endpoint registra administrador, horários, identidades, resultado e último usuário observado do computador removido, com origem/data quando disponíveis; sem tela nova.
- Registros dentro de Endpoints; NOC continua no roadmap; revisão de Alertas, Atividades, Tarefas e Eventos fica para depois. Códigos oficiais por empresa, AD e integração de chamados postergados.
- Codex 2 implementa em branch/PR própria, sem deploy, coordenando mudanças de agente/instalador com Codex 1. Critérios e salvaguardas detalhados no documento 01B.

## Atualização aprovada em 25/11/2026 — RC49-02 NOC operacional

Escopo funcional aprovado pelo responsável em [RC49-02 — NOC operacional e políticas de alertas](rc49-02-noc-operacional.md). Codex 2 inicia 02A (Políticas de Alertas parametrizáveis), seguindo com 02B (motor e atualização assíncrona), 02C (últimos 15 alertas, cards e ações reais), 02D (modo TV) e 02E (central global com últimas 10 notificações e sons). Preservar visual existente, eliminar reload e hover do container, utilizar sinais/contratos existentes e respeitar continuidade operacional. Defaults editáveis, sem novas constantes de negócio. Desenvolvimento por PRs, sem deploy/publicação intermediária; RC49 permanece em desenvolvimento. A PR #67 registra o encerramento de desenvolvimento da 01B e deve ser conciliada sem substituir esta decisão aprovada por sua proposta anterior.

## Estado integrado da RC49-02 — 26/11/2026

O desenvolvimento dos cinco incrementos 02A–02E foi integrado por merge commits normais. SHA consolidado da `main`: `db01bda5493d16631375126590a88459b66cecc9`.

| Incremento | PR | Merge commit | CI do HEAD integrado |
| --- | --- | --- | --- |
| 02A — Políticas de Alertas | #69 | `e7f432ea0c8711e43be93fc868f60b453459c942` | Aprovado anteriormente |
| 02B — Motor e atualização assíncrona | #70 | `bf7f7317bda151e4ead775c9248c44437760178b` | Aprovado anteriormente |
| 02C — Refinamento visual e ações | #73 | `f945f8da7dab41fe27cbbd09ba1172b490729b60` | 5/5 jobs PASS, run 45831220501 |
| 02D — Modo TV | #74 | `5a08094d20241bbfdeb4761598f57f7bf7f05eb7` | 5/5 jobs PASS, run 45831263988 |
| 02E — Central de Notificações | #76 | `db01bda5493d16631375126590a88459b66cecc9` | 5/5 jobs PASS, run 45831339385 |

Os gates finais de 02C–02E cobriram regressões SQLite/PostgreSQL, navegador isolado, contrato de deployment Windows e Windows .NET/terminal E2E. Capturas desktop/mobile foram anexadas como artefatos nas PRs. A migration introduzida pela 02E é `dashboard.0006_noc_notification_preferences`, dependente de `agents.0056_rc49_seed_alert_policies` e `dashboard.0005_remoteinstallbatchitem_target_ad_sid`. Nenhuma migration foi aplicada, e nenhum deploy ou publicação de agente ocorreu nesta integração.

### Pendências antes da implantação

- Homologar visual e operacionalmente o NOC consolidado em ambiente autorizado, incluindo atualização assíncrona, ações, sons, leitura de notificações e Modo TV em desktop/mobile.
- Planejar e aplicar `dashboard.0006_noc_notification_preferences` somente durante deploy aprovado e conforme o procedimento normal de proteção do banco.
- Preservar o estado operacional atual das flags e das políticas; integração não habilita nova coleta, altera rollout ou autoriza jobs.
- A intermitência anterior de terminal no runner Windows permanece sem causa confirmada (`fresh_session`/`browser_condition_timeout`, com `database is locked`). Os gates posteriores passaram; não considerar isso prova de resolução.

A RC49 permanece em desenvolvimento até o fechamento do roadmap. Este registro não autoriza deploy, migration operacional, alteração de flags, publicação ou atualização de endpoints.

# RC49-03A — Central de Software (desenvolvimento)

A 03A substitui os dados simulados da Central de Software por inventário real de `InventorySnapshot.installed_software` e metadados de instaladores consultados por API somente leitura. Cria a migration aditiva `agents.0058_softwarepackagerecord`, após `0057_admin_terminal_access_grant`; não armazena binários, não cria jobs, não executa operações remotas e não habilita flags.

Antes de qualquer publicação desta migration, revisar os testes SQLite/PostgreSQL e aplicar a cadeia pelo procedimento aprovado de deploy. O botão de upload continua desabilitado e não há API de upload. Nenhuma migration foi aplicada em produção nesta etapa.

Referência funcional: [RC49-03A — Central de Software](rc49-03a-central-software.md). A homologação visual de desktop/mobile utilizará somente fixtures em banco isolado; a publicação do artefato de capturas pelo CI não representa homologação operacional.
