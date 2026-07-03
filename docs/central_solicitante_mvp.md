# Central de Chamados do Solicitante - MVP

## Rotas

- `/meus-chamados/`: central simples do usuario autenticado.
- `/meus-chamados/<numero>/`: consulta do chamado do usuario autenticado.
- `/meus-chamados/abrir/`: abertura de chamado pelo solicitante.
- `/meus-chamados/<numero>/responder/`: resposta publica do solicitante.
- `/meus-chamados/<numero>/reabrir/`: reabertura de chamado resolvido.

As rotas antigas `/portal/chamados/` continuam existindo como base autenticada/compatibilidade, mas o fluxo padrao do solicitante fica em `/meus-chamados/`.

## Regra de acesso

Nesta fase, a regra e simples:

- Usuarios `is_staff` ou `is_superuser` acessam a area tecnica.
- Usuarios configurados em `NIGHTOWL_TECHNICAL_USERNAMES` tambem acessam a area tecnica, mesmo quando necessario manter uma allowlist temporaria.
- Usuarios autenticados comuns sao redirecionados para `/meus-chamados/` ao tentar acessar areas internas tecnicas.
- A lista e o detalhe do solicitante filtram chamados por `request.user.email`.
- Se o usuario nao tiver e-mail cadastrado, a central mostra estado vazio/aviso e nao exibe chamados de terceiros.

A allowlist temporaria pode ser ajustada via:

```env
NIGHTOWL_TECHNICAL_USERNAMES=gabriel.oliveira
```

## Area tecnica x area solicitante

A area tecnica continua em `/tickets/` e mantem Central, detalhes tecnicos, configuracoes, manutencao, RMM e GMUD conforme permissoes do MVP.

A area do solicitante mostra apenas:

- chamados do e-mail do usuario logado;
- descricao original;
- status, categoria, equipe/responsavel e datas principais;
- comentarios publicos;
- anexos publicos;
- solucao quando resolvido.

Na central `/meus-chamados/`, a lista usa um preview lateral. Ao selecionar um chamado, o preview mostra somente dados publicos do atendimento e permite comentar apenas quando o chamado esta aberto/em atendimento/aguardando solicitante. Chamados resolvidos, encerrados ou cancelados ficam em modo somente leitura no preview.

## Drawer de abertura

O drawer do solicitante permite:

- titulo;
- descricao;
- categoria;
- impacto;
- endpoint/dispositivo opcional;
- anexos publicos.

O backend sempre define:

- `requester_email` a partir de `request.user.email`;
- `requester_name` a partir do nome completo ou username;
- `source=portal`;
- comentarios/anexos como publicos.

Nao sao exibidos no modo solicitante:

- criar e assumir;
- registro avancado;
- fila interna;
- SLA interno detalhado;
- responsavel;
- acoes administrativas.

Depois da criacao, o usuario retorna para `/meus-chamados/?selected=<numero>` e o novo chamado fica selecionado na previa lateral.

## Dados ocultos

O portal nao exibe:

- comentarios internos;
- auditoria tecnica;
- logs RMM;
- acoes remotas;
- resolver/escalar/assumir;
- relacoes internas.

## Limitacoes

- Nao ha token publico nesta fase.
- Nao ha RBAC avancado por grupo AD nesta fase.
- A permissao tecnica usa `is_staff`/`is_superuser` e uma allowlist simples.
- O usuario precisa ter e-mail preenchido para ver seus chamados.

## Proximos passos

- Token publico seguro para consulta externa.
- RBAC por grupos AD.
- Experiencia de anexos mais rica no portal.
- Regras formais de transicao de status para respostas/reaberturas.
