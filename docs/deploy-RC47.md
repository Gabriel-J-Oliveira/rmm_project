# NightOwl RMM — RC47: planejamento consolidado e preparação de publicação

> **Estado: documento de planejamento, não autorização de implementação ou deploy.**
> Consolidado em 05/11/2026 com base nas decisões e discussões do projeto. Alterações futuras devem atualizar este mesmo documento. Diferenciar decisões acordadas, hipóteses e pendências de validação.

**Repositório:** Gabriel-J-Oliveira/rmm_project  
**Base consultada:** main em cbd72f544a6287a0952ba64bfc5df884c5e85f3c.  
**Escopo:** simplificação da arquitetura de coletas; inventário de hardware; telemetria contínua e retenção; última análise persistida; agendamentos/políticas; ajustes pontuais de interface; validação e rollout da RC47.  
**Fora do escopo deste registro:** nenhuma alteração operacional executada; nenhum código, release, flag de frota ou ambiente modificado por este planejamento.

## 1. Resumo executivo — decisões de produto

1. **Um agente e uma origem de coleta:** o NightOwl Agent Windows .NET continua sendo o motor único de obtenção de dados. Coletores especializados podem ter cadências diferentes; não criar outra rotina de coleta por causa da tela Capacidade ou de um novo painel.
2. **Separar os propósitos:** a atual tela **Capacidade & Planejamento** passa a se chamar **Inventário de Hardware** e deve focar no parque físico e no planejamento de upgrades. A análise de uso de CPU, RAM, disco e rede pertence ao **Monitoramento** do endpoint e aos consumidores técnicos pertinentes.
3. **Amostragem de 10 segundos:** manter o modelo `continuous` como arquitetura-alvo para todo o ambiente, com consolidação, filas duráveis e persistência limitada. Não confundir **coletar a cada 10 s** com **guardar todos os pontos a cada 10 s indefinidamente**.
4. **Última análise disponível sem visita manual:** selecionar periodicamente uma janela de aproximadamente **60 s** da telemetria existente em cada endpoint, com meta de no máximo **15 dias** entre análises bem-sucedidas. Máquinas recém-instaladas devem receber uma primeira análise automática.
5. **Retenção econômica:** manter **a última análise detalhada** e **um histórico estatístico compacto**. Ao substituir a última análise, incorporar suas estatísticas ao histórico sem perder quantidade, datas, mínimos, máximos e qualidade. A média isolada não é suficiente.
6. **Sem coletor/scheduler paralelo:** aproveitar os coletores do agente e o motor existente de automações. Qualquer captura automática deve ter identidade e auditoria próprias e **não** se passar por sessão interativa do operador.
7. **Políticas centralizadas:** na RC47, a tela **Políticas** será o local de administração de execuções padronizadas, únicas e recorrentes; a página do endpoint poderá listar políticas aplicadas e oferecer execução manual autorizada.
8. **Dois ajustes visuais pontuais:** integrar os seletores de **Ethernet/interface** e **Disco** do monitoramento ao tema NightOwl e **remover o efeito hover dos cards grandes**, sem redesenhar toda a tela agora.
9. **Preservação de segurança e compatibilidade:** migração de `legacy` para `continuous` não é automática; exige validação de capacidade, fila antiga, versões anteriores e nova versão Windows assinada.

## 2. Estado conhecido da RC46 e dependências para RC47

- Backend Django/PostgreSQL no Linux, agente Windows .NET assinado e canais de release separados.
- A RC46 `0.1.1.0-rc46` publicada permanece **imutável**; não sobrescrever artefatos assinados.
- A VM de laboratório RC46 identificada por UUID `c97045f2-35ff-48f1-afa5-095173fbb3dd`, machine_id final `10a62464`, IP `192.168.104.65` foi verificada com `telemetryMode=continuous`, `telemetryEnabled=True` e serviço `NightOwlAgentDotNet=Running`. Existe endpoint homônimo distinto; nunca operar somente por hostname.
- O monitoramento ao vivo **recebeu amostras reais** na VM (CPU, memória, disco e rede); erro anterior de criação de sessão decorria de permissão do diretório privado de cache Linux, corrigida no laboratório.
- PRs #12 e #13 foram integradas na main, com HEAD `cbd72f544a6287a0952ba64bfc5df884c5e85f3c`; apresentam formulário, diagnóstico, eixos, valores, tooltips e estados Encerrar/Congelar/Limpar. Homologar visualmente os novos estados na VM após deploy; não confundir teste sintético com teste real.
- A documentação [rc46-telemetry-mode-policy.md](rc46-telemetry-mode-policy.md) mostra uma divergência **intencionalmente não corrigida**: o objeto .NET novo define `continuous`, mas o instalador canônico RC46 grava `legacy` em instalações novas; atualizações preservam configurações anteriores.
- A fila v1 `State/telemetry-buffer.json` não é drenada pelo pipeline contínuo. Na VM foi mencionada uma amostra antiga pendente; verificar entrega/reconciliação antes de migração permanente, sem apagar nem presumir perda.
- **A RC46 ainda possui frentes de homologação próprias** (ações operacionais/scripts aprovados, diagnóstico WinRM, terminal, updater/repair e outras validações de piloto). A RC47 não deve ser iniciada como desculpa para pular os gates de segurança da RC46.

## 3. Inventário de Hardware (antiga Capacidade)

### Objetivo e informações a preservar

A visão global de hardware deve continuar mostrando a frota, identificações, fabricantes, modelos, sistemas operacionais, CPU físico (modelo, núcleos, clock), RAM instalada/módulos/slots, placa-mãe, discos físicos/volumes, bateria quando existente, idade/garantia se disponíveis, ocupação de disco, compatibilidade e oportunidades de upgrade. Mostrar origem, data e qualidade de inventário quando necessário.

**Não planejar um novo frontend completo nesta etapa.** A prioridade é remover redundâncias e simplificar as fontes e o processamento.

### Retirar da camada de Inventário de Hardware

- P95 e outros percentis de CPU/RAM, dispersão CPU × RAM, maiores consumidores, pressão sustentada, ranking/classificação baseada em utilização, séries históricas de desempenho e cobertura/qualidade de amostras de telemetria quando só servirem ao painel.
- Consultas SQL e projeções derivadas exclusivamente dessas visualizações, **somente após mapear consumidores e testes**.
- Evitar mostrar métricas de desempenho ali apenas porque os dados já estão disponíveis.

### O que a análise do código já mostrou

- **Capacidade NÃO realiza uma segunda coleta no endpoint.** O arquivo `dashboard/capacity_views.py` lê `InventorySnapshot` e `EndpointPerformanceSample` produzidos pelo agente e calcula análises próprias no servidor.
- A redundância atual é sobretudo de **interpretação, consultas, classificações e interface**, e não de um segundo coletor instalado.
- O backend usa funções de `agents/telemetry_analytics.py`, `agents/telemetry_diagnostics.py` e `agents/telemetry_continuous.py` para cálculos P95, evidência e pressão.
- Antes de remover funções, mapear referências em Alertas, histórico, APIs, dashboards, relatórios OBZ e testes. **Não apagar alertas de pressão sustentada nem quebrar suas regras por remover a tela.**
- Renomear rota, menu, breadcrumbs, títulos e nomenclatura da antiga Capacidade com compatibilidade de links e permissões; decidir se endpoints/API legados precisam de alias. Isso é ajuste funcional mínimo, não redesenho geral.
- Investigar e corrigir projeções: o Resumo de Telemetria pode ter CPU/RAM mas mostrar disco e rede como “Não informado” porque procura campos planos, enquanto o v2 guarda métricas detalhadas em `metric_summaries`. Não criar nova coleta para resolver um problema de mapeamento.

## 4. Motor único de coleta no agente .NET: auditoria de concorrência e redundância

### Estrutura conhecida

- `NightOwl.Agent.Windows/Collectors/WindowsInventoryCollector.cs`: inventário físico e sistema, seções de coleta por plano.
- `NightOwl.Agent.Windows/Collectors/InventoryCadence.cs`: básico diário, hardware detalhado semanal, volumes a cada hora, rede diária e demais seções conforme intervalo configurado.
- `NightOwl.Agent.Windows/Collectors/TelemetryCollector.cs`: coleta nativa de desempenho.
- `NightOwl.Agent.Windows/Collectors/ContinuousNativeReader.cs`: usa o coletor nativo e acrescenta métricas por disco e adaptador de rede, amostragem contínua e processos com cadência mais lenta.
- `NightOwl.Agent.Windows/Services/TelemetryPipeline.cs`: decide qual modo de telemetria executar; `legacy` e `continuous` **não devem executar simultaneamente no mesmo agente**.
- `NightOwl.Agent.Windows/Services/ContinuousTelemetryPipeline.cs`: amostras em torno de 10 s, janelas de 60 s, upload periódico, fila v2 persistente e buffer efêmero para sessões ao vivo.
- `NightOwl.Agent.Windows/Worker.cs`: coordena heartbeat, inventário, telemetria e jobs.

### Auditoria obrigatória antes de mexer

1. Traçar produtor → transformação → armazenamento → consumidor para cada campo (inventário físico, capacidade/uso, disco, rede, sistema, heartbeat).
2. Verificar leituras repetidas de Windows APIs, CIM/WMI/PowerShell, discos/volumes, normalização de hardware, tempos de lock e execução paralela com jobs. Identificar duplicação **com evidência**, não por semelhança de nomes.
3. Garantir que telemetria leve a cada 10 s não dispara inventário pesado; inventário não interrompe heartbeat, jobs nem a coleta sem proteção de concorrência.
4. Verificar isolamento do `TelemetryCollector`, estado entre amostras, primeiro delta inválido, retorno após suspensão e falhas parciais. Preservar latências e velocidades por instância, DNS/rede por adaptador no inventário, e metadados de qualidade.
5. Garantir que a remoção de análises da Capacidade **não** desligue o pipeline de telemetria, histórico necessário ou alertas.
6. Preservar idempotência, ACK, filas duráveis, retries, limites de recursos, rotação de logs, reparo, updater, segurança e diagnósticos.

**Resultado esperado:** matriz de coletores e consumidores, duplicações comprovadas, plano de remoção mínimo e testes de regressão. Nenhuma coleta nova para a antiga Capacidade.

## 5. Telemetria contínua: objetivo e política de adoção

- **Diretriz adotada:** preservar a amostragem leve de 10 segundos para a frota, se homologada em hardware e conectividade representativos.
- No pipeline contínuo, o agente consolida em janelas de 60 s; o backend atualmente pode compactar para janelas de 5 min e 1 h, com defaults de 7, 30 e 90 dias, respectivamente, definidos em `config/settings.py`.
- Coleta local e retenção central são decisões **separadas**. É possível coletar a cada 10 s sem persistir cada ponto bruto.
- A meta de reduzir armazenamento **ainda é hipótese**: trocar `legacy` por `continuous` em toda a frota sem rever retenção pode aumentar a escrita. Medir banco, rede, CPU/RAM do agente, disco, custo por upload, qualidade e recuperação offline.
- Não comparar ou fundir incorretamente médias de pontos `legacy` com distribuições/janelas de v2. Não calcular média simples de percentis.
- Respeitar a fila v1 antiga antes de encerrar seu suporte. Planejar drenagem ou reconciliação sem perda nem eliminação silenciosa.
- Habilitação gradual, com piloto, métrica de aceite e rollback. Alterar padrão do instalador/migration exige **nova release Windows assinada**; não alterar o pacote RC46 já publicado.

## 6. Última análise persistida e captura automática sem motor paralelo

### Comportamento desejado

- Cada endpoint elegível terá uma **última análise detalhada persistida** e, separadamente, estatísticas históricas compactas.
- Periodicidade-alvo: **uma janela de 60 segundos a cada 15 dias por computador**, distribuída ao longo dos dias/horários para evitar simultaneidade e janelas sistematicamente ociosas.
- Máquina recém-instalada deve receber primeira análise automática após entrar em operação; decidir se é imediata ou após estabilização (sugestão anterior: 24–48 h, **não aprovada**).
- **Reutilizar os pontos existentes da amostragem de 10 s**. Não fazer uma segunda leitura para construir a janela e não iniciar uma sessão interativa em nome de operador fictício.
- Garantir janela válida: número mínimo de amostras, timestamps, qualidade, lacunas e idempotência; se a máquina estiver offline, reprogramar sem tempestade de retries.
- Guardar a última captura completa com timestamps, métricas por adaptador/disco, qualidade e identificação da versão do agente. Só substituir a última análise depois de persistir validamente a nova.
- Ao substituir uma análise, atualizar o **histórico estatístico compacto** e descartar pontos brutos antigos conforme retenção. Guardar contagem de análises, soma/peso para média, mínimos/máximos, datas e qualidade; preservar distribuição quando percentis forem necessários.
- **Não armazenar só uma média geral**: oculta picos e situações excepcionais. Não comparar como equivalentes janelas de qualidade diferente.
- O resumo da última captura pode ser exibido no Monitoramento sem precisar de sessão ao vivo manual. A sessão manual continua efêmera e em memória do navegador; não deve substituir automaticamente a captura periódica. Salvar sessão manual seria ação explícita e futura, com critério de qualidade.
- Evitar regressão das regras atuais de alerta e auditoria. Se o histórico for reduzido, medir o impacto em análises de tendências e diagnóstico de pressão sustentada.

### Infraestrutura existente a aproveitar

- `EndpointPerformanceSample` e `InventorySnapshot` no backend; `TelemetryLiveSession` com sessão efêmera para o operador; cache privado cross-worker; `agents/telemetry_continuous.py` para janela v2 e compactação.
- O fluxo ao vivo consulta leases e transmite pontos temporários; **não** usá-lo para o scheduler sem desenho de segurança específico.
- `AutomationDefinition`, `AutomationOccurrence`, `AutomationSchedulerHealth` e mecanismos existentes de disparo/repetição devem ser avaliados antes de qualquer novo agendador.
- Determinar se a seleção de janela de 60 s ocorrerá no backend a partir dos dados necessários ou se o agente precisará sinalizar/reter temporariamente os seis pontos brutos para salvar apenas a janela. **Não há certeza de que os seis pontos individuais estejam atualmente persistidos no banco**.

## 7. Políticas e jobs — compromisso reservado para RC47

- Políticas centraliza templates de execuções padronizadas, atribuições por endpoint/grupo, jobs únicos/recorrentes, próximos disparos e histórico.
- A página do endpoint lista políticas atribuídas/aplicadas com estado e histórico, permitindo **executar manualmente** uma política existente **com permissão**. Não transformar o endpoint no editor principal do agendamento.
- Aproveitar o motor de automações já presente; auditar scheduler, recorrência, offline, concurrency locks, idempotência, autorização, quarentena e ACK.
- Não tratar scripts arbitrários como políticas livres: manter allowlists, scripts aprovados, grants e registros de auditoria.
- Validar dependências de eventuais jobs de configuração de telemetria e o modo efetivo do agente.

## 8. Frontend: apenas ajustes pontuais já solicitados

1. Os **seletores Ethernet/interface e Disco** do Monitoramento ao vivo estão visualmente fora do tema. Aplicar o mesmo padrão de `select`, borda, fundo, tipografia, foco e espaçamento do NightOwl. Testar desktop e mobile, contraste e opções longas. Não modificar a seleção funcional de interface/disco.
2. **Remover o efeito hover dos cards grandes** nas telas envolvidas (principalmente cards/painéis de Monitoramento e Inventário), mantendo hover/foco acessíveis em botões, links e itens que sejam realmente clicáveis. Auditar classes existentes antes de introduzir override global.
3. Não redesenhar agora o restante do frontend de Inventário: somente remover análises de desempenho, renomear a tela e adequar referências necessárias.

## 9. Regras de segurança, dados e compatibilidade

- Inventário e telemetria devem usar **machine_id/UUID persistente**, não hostname isoladamente.
- Preservar estados `legacy` durante atualização até migração autorizada; novas instalações hoje usam `legacy` pelo instalador RC46.
- Não expandir flags globalmente no ambiente real; não publicar artefato Windows sem nova assinatura, checksum, manifestos, rollback e testes de piloto.
- Não perder amostras antigas, não apagar históricos antes de medição/backup e de decisão de retenção.
- Dados desconhecidos permanecem desconhecidos, não zero. Evitar rótulos equivocados como bytes cumulativos vs B/s e disco físico vs volume lógico.
- Limites de retenção, permissões e confidencialidade dos dados; medir escala e impedir coleta pesada simultânea em toda a frota.
- Manter os testes Windows .NET, Python/Django/PostgreSQL, JavaScript e testes reais em VM; não afirmar homologação só por checks sintéticos.

## 10. Plano de execução proposto — NÃO iniciado

| Etapa | Entrega | Gate |
| --- | --- | --- |
| RC47-00 | Auditoria read-only de coletores, locks, cadências, consumidores e armazenamento | Matriz sem suposições e plano de compatibilidade |
| RC47-01 | Limpar/renomear Capacidade para Inventário de Hardware, preservando hardware e alertas | UI e APIs sem regressões; nenhuma coleta duplicada |
| RC47-02 | Canonicalizar as projeções da telemetria v2, reduzir duplicação de análises e definir retenção medida | Disco/rede corretamente interpretados e retenção defensável |
| RC47-03 | Criar última análise detalhada por endpoint, atualização estatística e seleção programada da janela de 60 s / 15 dias | Sem segundo coletor; offline/novos endpoints e qualidade testados |
| RC47-04 | Reorganizar Políticas e exibir políticas atribuídas/executáveis manualmente no endpoint | Scheduler existente reaproveitado, autorização e ACK validados |
| RC47-05 | Ajustes pontuais de selects Ethernet/Disco e hover dos cards grandes | Tema, responsividade, foco acessível e sem mudança funcional |
| RC47-06 | Homologar `continuous` em grupo pequeno, fila legacy e custos; decidir alteração do instalador | Piloto mensurado, rollback, pacote Windows assinado se necessário |

A ordem é provisória; pode ser reorganizada conforme dependências. Não iniciar PRs, scripts, merges, migração de banco ou deploy por causa deste documento.

## 11. Critérios de aceite e métricas a medir

- **Unicidade:** todas as informações exibidas provêm do NightOwl Agent e de projeções dos dados recebidos; nenhuma segunda coleta oculta por dashboard.
- **Inventário:** hardware completo e válido, origem/atualização claras, sem classificação de desempenho.
- **Monitoramento:** sessões ao vivo preservadas, último snapshot automático consultável sem abrir sessão, agregação histórica correta.
- **Custo:** comparar antes/depois do piloto em CPU e RAM do agente, duração de coleta, rede enviada por dia, I/O e disco local, crescimento do PostgreSQL, lacunas, perda por offline e estabilidade dos jobs.
- **Agendamento:** distribuição de 15 dias por endpoint, primeira análise das novas máquinas, limites de concorrência, retry offline e nenhuma captura duplicada.
- **Compatibilidade:** agente RC46/`legacy` preservado, filas v1 conciliadas, proteção de grants, rollback e sem regressão em alertas.
- **Visual:** selects Ethernet/Disco no tema, sem hover indevido nos cards grandes, sem quebra de navegação ou acessibilidade.

## 12. Perguntas ainda abertas

- Qual política final de retenção substitui ou ajusta os defaults 7/30/90 dias sem eliminar evidência útil para alertas e investigação?
- Os seis pontos de 10 s da janela de 60 s precisam ser gravados pelo agente ou podem ser capturados de maneira segura no backend?
- A primeira captura é imediata ou ocorre após estabilização de 24–48 horas?
- Quais métricas de qualidade tornam uma janela válida para substituir a anterior?
- Qual horário/janela de expediente representa melhor o uso sem enviesar os resultados?
- Em qual release e após quais números do piloto `continuous` passa a ser o padrão no instalador?
- Quais cálculos de `telemetry_diagnostics.py` são exclusivos da antiga Capacidade e quais servem a Alertas/Monitoramento?
- Qual redução real de armazenamento o esquema consolidado fornece? **Não declarar economia antes da medição.**

## 13. Referências de implementação

- [RC46 — política de telemetryMode](rc46-telemetry-mode-policy.md)
- [RC46 — monitoramento ao vivo](rc46-monitoramento-live.md)
- [RC46 — telemetria contínua](rc46-03-continuous-telemetry.md)
- [RC46 — inventário detalhado](rc46-02-hardware-inventory.md)
- [RC46 — jobs/automações](rc46-04b-automations.md)
- [Inventário e cadência do agente](../NightOwl.Agent.Windows/Collectors/InventoryCadence.cs)
- [Coletor de inventário](../NightOwl.Agent.Windows/Collectors/WindowsInventoryCollector.cs)
- [Leitor nativo de telemetria](../NightOwl.Agent.Windows/Collectors/ContinuousNativeReader.cs)
- [Pipeline contínuo](../NightOwl.Agent.Windows/Services/ContinuousTelemetryPipeline.cs)
- [Seleção do modo legacy/continuous](../NightOwl.Agent.Windows/Services/TelemetryPipeline.cs)
- [Projeções da tela Capacidade](../dashboard/capacity_views.py)
- [Template de Capacidade](../templates/dashboard/capacity.html)
- [Telemetria contínua no backend](../agents/telemetry_continuous.py)
- [Sessão ao vivo](../agents/telemetry_live.py)
- [Modelo de desempenho e sessões](../agents/models.py)
- [PR #12](https://github.com/Gabriel-J-Oliveira/rmm_project/pull/12) e [PR #13](https://github.com/Gabriel-J-Oliveira/rmm_project/pull/13)

---

**Regra de governança:** este arquivo preserva o contexto e serve de referência para a RC47. Uma etapa só é autorizada quando o responsável solicitar explicitamente sua implementação, revisão e eventual deploy.
