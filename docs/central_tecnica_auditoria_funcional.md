# Auditoria funcional da Central Tecnica

## Escopo validado

- Central Tecnica em `/tickets/central/`.
- Lista compacta de chamados, selecao de linha, preview lateral, filtros rapidos, Mais filtros, busca e acoes do preview.
- A Central do Solicitante nao foi alterada nesta fase.

## Comportamentos corrigidos

- O botao `X` do preview agora limpa a selecao do chamado, remove o destaque da linha e retorna o painel para o estado vazio.
- A acao de ocultar o painel ficou separada no botao de painel lateral.
- Ao aplicar filtros que removem o chamado selecionado da lista, a selecao e o preview sao limpos.
- Ao abrir a Central diretamente com `?ticket=...`, o preview passa a ser renderizado pelo mesmo JS das acoes reais, evitando formulario antigo/fake.

## Dados e filtros

- A tabela usa os tickets reais renderizados pelo backend.
- A busca backend cobre numero, titulo, solicitante, e-mail, setor, categoria, hostname do endpoint e nome de endpoint.
- Filtros rapidos visiveis: Fila geral, Atribuidos a mim, Sem responsavel, Criticos, Mais filtros e Limpar.
- Mais filtros concentra Status, Prioridade, Responsavel/Tecnico, Cliente, Setor, Categoria, Origem, Fila, SLA, Caracteristicas e RMM.
- Os filtros aplicados atualizam a lista, a contagem e os chips visuais. A URL tambem e atualizada para permitir recarregar a mesma visao.

## Acoes conectadas ao backend

- Assumir atendimento usa `POST /tickets/<numero>/api/update/` com `field=assigned_to`.
- Alterar status usa `POST /tickets/<numero>/api/update/` com `field=status`.
- Alterar prioridade usa `POST /tickets/<numero>/api/update/` com `field=priority`.
- Comentario rapido usa `POST /tickets/<numero>/api/comments/` e respeita visibilidade interna/publica.
- Anexo no preview usa `POST /tickets/<numero>/api/attachments/` e respeita visibilidade interna/publica.
- Auditoria e notificacoes continuam sendo registradas pelos services existentes quando as views ja possuem essa regra.

## Acoes com fallback seguro

- Abrir dispositivo leva para a lista/inventario de endpoints quando ha endpoint vinculado.
- Abrir no RMM informa que o contexto detalhado ainda sera conectado e leva para endpoints quando ha vinculo.
- Acesso remoto nao executa comando real nesta fase e mostra feedback explicito de indisponibilidade.
- Vincular endpoint pelo preview ainda e uma acao futura e mostra feedback informativo.
- Mesclagem de relacionados ainda usa fluxo visual/local da Central.

## Seguranca e permissao

- A protecao de rota permanece centralizada no middleware de autenticacao/autorizacao.
- Usuarios comuns continuam redirecionados para `/meus-chamados/`.
- Acoes reais usam `request.user` por meio de `_request_actor`.
- Comentarios internos continuam com `visibility=internal` e nao aparecem no portal do solicitante.

## Limitacoes atuais

- Filtros sao aplicados imediatamente no cliente sobre a lista renderizada, mas a URL gerada tambem permite recarregar com filtro backend.
- KPIs do topo nao sao recalculados dinamicamente apos acoes locais; a lista, preview e contagem de resultados sao atualizados.
- Acesso remoto, vinculo de endpoint, contexto RMM profundo e mesclagem real ficam para fases futuras.

## Proximos passos recomendados

- Transformar filtros da Central em requisicoes HTMX/fetch para atualizar tambem KPIs sem reload.
- Criar detalhe de endpoint direto no preview quando houver rota consolidada por UUID.
- Implementar mesclagem real de chamados relacionados.
- Implementar comandos remotos somente quando a camada RMM estiver pronta e auditavel.
