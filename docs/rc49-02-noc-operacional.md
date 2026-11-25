# RC49-02 — NOC operacional e políticas de alertas

**Decisão funcional aprovada em 25/11/2026.** Responsável: Codex 2. Preservar o NOC existente, sua identidade visual escura verde/roxa e os componentes úteis; não reconstruir a tela do zero. RC49 permanece em desenvolvimento até conclusão do roadmap; PRs não implicam deploy nem publicação de agente. Integrar documentação da PR #67 e referenciar esta especificação no roadmap e deploy-RC49.md, conciliando a diretriz de continuidade do Codex 1.

## 02A — Políticas de Alertas

Criar seção em Políticas, separada de Políticas de Execução. Reutilizar modelos, avaliação e alertas existentes quando possível, sem motor/coletor paralelo. Regras predefinidas editáveis pelo administrador, persistidas e auditadas. Interface aprovada: toggle Ativa, seletor de métrica, limite, duração mínima, criticidade (Informativo/Atenção/Crítico), limite de recuperação, flags de notificação visual e sonora e resumo legível atualizado conforme os campos. Campos adaptados ao tipo de métrica, unidades explícitas, validação de coerência e escopo configurável por endpoint/categoria (servidores e desktops), reutilizando grupos existentes quando aplicável. Não depender de integração AD futura. Defaults são dados editáveis, não constantes de negócio.

| Métrica | Atenção inicial | Crítico inicial |
| --- | --- | --- |
| CPU | 85% por 15 min | 95% por 30 min |
| RAM | 85% por 15 min | 95% por 30 min |
| Disco ocupado | 85% | 95% |
| Offline | 24 h | 72 h |
| Uptime | 15 dias | 30 dias |
| Antivírus | Configurável | Configurável |
| Falhas de jobs | Configurável | Configurável |

Exemplo aprovado de formulário: RAM 90% por 15 min, atenção, recuperação 80%, visual ligado, som desligado. Exemplo não substitui os defaults da tabela. Definir e documentar recuperação/histerese, cobertura mínima, frescor e resolução de sobreposição de regras; parâmetros adicionais devem ser editáveis onde fizer sentido. Não classificar amostras ausentes/antigas como críticas nem inferir recuperação por falta de dados. Preservar regras e autorizações existentes durante migração; evitar enxurrada de notificações históricas. Não confundir configuração de alerta com habilitação de recursos operacionais.

### Contrato de configuração entregue pela 02A

As migrations 0055 e 0056 criam regras editáveis com chave estável, revisão e escopo por categoria ou UUID de endpoint. Cada edição registra o operador e os valores anteriores e novos. As regras são inicialmente inativas, com visual ligado e som desligado. Antivírus e falhas de jobs começam com os campos de disparo em branco, pois a decisão prevê configuração pelo administrador. Categorias AD e grupos de distribuição não são usados como escopo de alertas.

Para a integração na 02B, valores numéricos disparam em ou acima do limite e recuperam em ou abaixo do limite de recuperação. Enquanto o valor estiver entre ambos, preservar o estado. Antivírus dispara por condição confirmada e recupera com proteção ativa confirmada. Amostras ausentes, antigas ou com cobertura insuficiente não disparam nem recuperam. Entre regras sobrepostas para o mesmo endpoint, métrica e recurso, prevalece a maior criticidade, seguida do escopo mais específico, depois do maior limite numérico quando comparável e da chave estável para empate. Uma evidência pode corresponder a várias regras, mas deve produzir uma só ocorrência ativa por endpoint, métrica e recurso.

Os valores iniciais de recuperação para CPU, RAM e disco ficam cinco pontos percentuais abaixo do disparo. Uptime recupera um dia abaixo e offline com comunicação confirmada. CPU e RAM iniciam com cobertura de 80% e idade máxima de 5 min. Disco, uptime e antivírus usam 24 h. Esses valores são editáveis. Falhas de jobs exigem janela, idade máxima e limites explícitos. Uma nova configuração deve avaliar a partir da ativação e evitar notificar o histórico. A 02B deve consumir essas regras pelo avaliador já chamado por `run_maintenance_tasks`. As ocorrências permanecem em `EndpointAlert` e `AlertEvent` para consumo posterior pela fila, modo TV e central de notificações.

## 02B — Motor de alertas e NOC

Reutilizar heartbeat, inventário, telemetria persistida, alertas, jobs/ACK e scheduler existente. Prioridade determinística, motivo verificável, duração sustentada, deduplicação por identidade do endpoint/regra/recurso, recuperação baseada em evidência, reconhecimento e silêncio independentes da resolução. Escopo e RBAC em todas as leituras e ações; dados atuais, desatualizados e desconhecidos distintos. Corrigir saúde geral 0% indevida e discos com '-% livre'; ausência não é zero. Contadores distinguem ocorrências e endpoints distintos, não inventar índice de saúde. Consultas eficientes e paginadas/limitadas; sem N+1. Atualizar por API assíncrona sem reload, sem sobreposição de requisições, preservando foco, scroll, filtros e menu de ações; intervalos 5/10/30/60 s, padrão 10 s, atualização manual e indicador de última leitura/erro. Duração/relógio local a cada segundo. Não exigir novo agente para mudanças de interface.

## 02C — Refinamento visual e ações

Manter cabeçalho/indicadores úteis e os quatro cards Críticos agora, Endpoints offline, Segurança, Disco e capacidade (lado a lado em desktop amplo). Substituir Atividade recente resumida por Todos os alertas acima desses cards, abaixo dos indicadores. Últimos 15 alertas/ocorrências por horário de abertura, com horário, criticidade, hostname, descrição, duração ativa (ou duração final), estado ativo/reconhecido/resolvido e ações rápidas; reconhecimento é independente do estado de recuperação. Preservar incidentes abertos nos cards temáticos mesmo se antigos. Remover hover do container/card, manter hover/foco acessível nos alertas clicáveis e ações individuais.

Ações reais: Reconhecer (auditar ciência), Abrir endpoint (contexto), Criar chamado (usar Ticket existente quando autorizado/disponível, sem duplicar chamados), Silenciar (prazo, escopo da ocorrência; não resolver), Verificar (job existente adequado à evidência, mostrar resultado real/pendência, respeitar offline). Não simular sucesso; caso não haja contrato disponível, manter indicação explícita de indisponibilidade com motivo, sem desabilitar ações já homologadas. Não ampliar integração patrimonial com chamados nem criar novo executor.

## 02D — Modo TV

Fullscreen API iniciada pelo operador, ocultar sidebar/navbar convencional, layout responsivo 16:9 e outras resoluções, texto legível, usar espaço disponível, manter atualização e som. ESC/botão restauram layout, tratar fullscreen indisponível com modo de apresentação na página. Preservar estado e não recarregar página.

## 02E — Central de notificações

Sino global na navbar, últimas 10 notificações autorizadas, contador de não lidas, leitura individual e marcar todas lidas, link ao contexto. Reutilizar toasts no canto inferior direito. Notificações e preferências persistentes por operador conforme necessário; respeitar escopo/RBAC, deduplicar entre polls e abas quando possível, não emitir som para histórico inicial ou ocorrência silenciada. Sons discretos e audíveis, opções desligado/somente críticos/todos habilitados pela política; interação inicial para desbloquear áudio do navegador e estado claro quando indisponível. Modo TV mantém alertas visuais e sonoros autorizados. Sem áudio repetitivo a cada atualização.

## Execução e aceite

Cinco incrementos/PRs 02A → 02B → 02C → 02D → 02E, dependências explícitas, branch atualizada, testes focados Django/JS/PostgreSQL conforme necessário e revisão humana da interface. Começar por 02A e apresentar PR para revisão; não publicar ou implantar intermediários. Preservar funcionalidades autorizadas após upgrade/downgrade/reboot; sem reintroduzir gates redundantes; manter RBAC/autenticação/auditoria. Validar persistência e edição de parâmetros, precedência e histerese, amostras ausentes/antigas, deduplicação, escopo, desempenho em volume representativo, ações reais, atualização sem reload, modo TV, notificações/sons sem repetição. Sem mudanças nas telas Alertas/Atividades/Tarefas/Eventos além das integrações indispensáveis; revisão dessas telas postergada. Manter pendências de homologação da 01B. Não aplicar migrations em produção nem alterar frota durante desenvolvimento.
