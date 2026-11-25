# NightOwl RC49-01A — Registros no Endpoint e patrimônio provisório

**Decisão de produto:** aprovada para iniciar em 24/11/2026 pelo responsável.
**Status:** escopo funcional definido para o **Codex 2 implementar, testar e abrir PR**. Não autoriza merge/deploy de RC49 em produção.
**Branch:** `feature/rc49-01a-asset-lifecycle`.
**Ponto de partida:** `main` de 24/11/2026, commit documental `72428f2bf3bb7ec5c06037799be9e90ea44fe006`; Codex 1 trabalha em paralelo no hotfix RC48 e poderá avançar a main.
**Visão de produto:** [nightowl-visao-produto-roadmap-rc49.md](nightowl-visao-produto-roadmap-rc49.md).
**RC49 inicial:** [deploy-RC49.md](deploy-RC49.md).

## 1. Decisões confirmadas (substituem a proposta anterior da seção RC49-01A do roadmap)

1. **Não criar uma tela ou item de menu "Ativos".** O ponto de interação patrimonial será uma **aba "Registros" dentro da tela atual de detalhes do Endpoint**.
2. Essa aba deve apresentar **histórico cronológico real e formulário para inclusão de novos registros**, com exemplos de tipos: observação, problema, formatação, manutenção, upgrade e troca de responsável. Não exigir modelagem completa de manutenção/cessão nesta primeira fatia.
3. **Não vincular chamados nesta RC.** Integração com Tickets, tarefas e histórico automático enriquecido é assunto para a próxima RC/fase, após negociação.
4. Cada equipamento físico receberá uma **identidade patrimonial permanente interna** (UUID gerado pelo NightOwl) independente do UUID do endpoint/agente.
5. Ter também um **código de patrimônio visível, único, pesquisável e provisório**. Não fixar a nomenclatura final: há **duas empresas**, e o responsável decidirá prefixos, formatos e divisão ao final desta etapa da RC49.
6. O **serial do equipamento coletado do BIOS** será a principal evidência técnica para reconhecer o mesmo hardware. Fabricante/modelo/serial da placa-mãe podem auxiliar na revisão de conflitos; hostname, IP, usuário logado ou machine ID **não** são prova de identidade física.
7. Relação **Patrimônio ↔ Serial ↔ Agente** precisa ser única onde aplicável: um código de patrimônio não se repete; um serial **válido normalizado** não pode representar dois patrimônios ativos sem conflito explícito; um endpoint não pode pertencer a dois patrimônios ativos simultaneamente.
8. Diferentes instalações do agente podem pertencer ao mesmo patrimônio **historicamente**, permitindo formatação/reinstalação sem perder os registros.
9. **Buscar por patrimônio no filtro padrão da lista de Endpoints**, preservando o funcionamento da tabela, cards, mapa de saúde, paginação e filtros atuais.
10. Exclusão/remoção de endpoints está **fora deste incremento** e será discutida separadamente com Codex 1. Não modificar semântica de delete/purge agora.

## 2. Distinção entre as identidades

| Identidade | Função | Persistência | Regra |
| --- | --- | --- | --- |
| UUID patrimonial | Chave técnica real da identidade física | Permanente | Não muda em formatação/transferência/renomeação |
| Código patrimonial provisório | Apelido exibido ao técnico e pesquisável | Persistido e único, substituível após decisão final | Exemplo visual **PROV-000001**, sem representar patrimônio corporativo definitivo |
| Serial BIOS válido | Evidência de identidade física | Observado e normalizado | Único para vínculo automático, mas pode ser ausente, inválido ou alterar após troca de placa |
| UUID `AgentMachine` / machine ID | Identidade operacional do endpoint | Pode mudar em reinstalação | Não é chave patrimonial |
| Hostname / domínio / IP | Localização e apresentação | Podem mudar | Nunca usar isoladamente para unir históricos |

**Atenção ao termo "mockado":** o usuário quer visualizar e testar a experiência com **nomenclatura temporária**, e não uma tela baseada em `MockNightowlApi`. Gerar e persistir códigos provisionais verdadeiramente únicos apenas quando um patrimônio for registrado, sem distribuição automática para todo o parque. O prefixo `PROV-` é sugestão técnica substituível; deve ser declarado claramente como provisório em telas e dados. Não codificar nomes reais ou formatos oficiais das duas empresas, nem deduzir empresa por domínio/hostname.

O código visível poderá ser renomeado/migrado quando o usuário decidir os padrões; o UUID patrimonial e o histórico não podem mudar. Preferir modelagem que aceite futuramente empresa/organização responsável sem quebrar relações, porém **não implementar regras de empresa nesta fase**.

## 3. Modelo mínimo para a implementação

Priorizar escopo enxuto. Nomes exatos e app Django podem seguir as convenções existentes:

- **Identidade física** (`PhysicalAsset` ou `AssetIdentity`): UUID, código patrimonial provisório único, status provisório, serial normalizado quando válido, fabricante/modelo como evidência, timestamps, autor/revisão. Permitir serial ausente/inválido sem falsificar unicidade.
- **Vínculo histórico** (`AssetAgentLink`): FK para ativo e `AgentMachine`, início/fim, origem, evidência, autor e motivo; vínculo ativo único por endpoint e, na primeira versão para estação única, no máximo um endpoint atual por ativo (duplicidades históricas preservadas).
- **Registro patrimonial** (`AssetRecord`): FK para ativo, tipo, descrição, data do acontecimento, data da criação, autor, responsável anterior/novo quando fizer sentido como campos simples, e outros dados mínimos necessários à UI.

Não são necessários, neste primeiro recorte, modelos e telas completas de `AssetAssignment` ou `AssetMaintenance`. Caso uma regra já aprovada dependa disso, explicar antes de ampliar escopo.

As migrações devem ser **aditivas**, sem mexer na chave, no token, no ciclo de vida ou na identidade `AgentMachine`. Validar invariantes e unicidade também no banco quando tecnicamente possível; normalizar códigos de patrimônio e seriais (casefold/trim/ruído) e tratar corrida de criação com transação/constraint. Evitar atribuição automática com serial genérico (`default string`, `to be filled by O.E.M.`, `unknown` etc.); reutilizar `agents.identity.meaningful_serial` ou equivalente já existente.

## 4. Fluxo da aba Registros

Dentro de `templates/dashboard/endpoint_detail.html`, acrescentar tab `records` com o mesmo mecanismo de navegação das abas existentes, sem duplicar a seção "Atividade" e sem criar um menu "Ativos".

Ao abrir:

- Se há vínculo confirmado: mostrar **Patrimônio provisório: PROV-...**, fabricante/modelo/serial, estado de identidade e **histórico de registros** (data, tipo, descrição, autor, ordem cronológica).
- Se não há vínculo: mostrar dados de inventário disponíveis; oferecer **"Criar patrimônio provisório"** ou **"Vincular patrimônio existente"**, com prévia e confirmação.
- Se houver serial válido e único já reconhecido: sugerir patrimônio correspondente; confirmar associação se a relação com agente atual ainda não estiver estabelecida.
- Se houver conflito (serial repetido, inconsistência de fabricante/modelo, outro endpoint ativo, serial ausente/genérico): exibir situação clara e **não mesclar** registros silenciosamente.
- Quando o equipamento tiver patrimônio confirmado: permitir incluir registro do tipo escolhido, descrição e data. Mostrar autor e momento de criação. Não criar registro de "formatação" automaticamente a partir do agente.
- Apresentar estado vazio útil; desabilitar escrita para quem tem somente permissão de leitura; respeitar CSRF e autenticação.

**MVP:** linha do tempo de registros **manuais**, inclusive transferências e manutenção como eventos descritivos. Formulários especializados de manutenção, workflow de devolução e histórico de responsáveis em tabelas próprias pertencem a etapas seguintes, salvo decisão explícita posterior.

O cadastro/edição deve usar rotas e views reais; nunca apenas mutar DOM/mock. Usar as estruturas CSS/JS e padrões de UX existentes para dar coesão.

## 5. Busca padrão em Endpoints

- Manter a pesquisa `q` atual e adicionar **código patrimonial provisório** ao conjunto de termos pesquisáveis.
- Ajustar placeholder para incluir "patrimônio".
- Pesquisa deve acontecer no **backend antes da paginação**, com resultado coerente nos três modos: tabela, cards e mapa de saúde.
- Se possível, mostrar código patrimonial discretamente nas linhas/cards sem aumentar demais as colunas ou prejudicar a densidade visual. Em endpoints sem patrimônio, mostrar ausência de vínculo sem inventar código.
- Não criar filtro de empresa, nomenclatura definitiva ou outro controle extra nesta fase.
- Evitar N+1: pré-carregar mapeamento dos vínculos ativos/códigos e aplicar às linhas. A busca não pode depender exclusivamente de JS no navegador.

Pontos técnicos já existentes: `dashboard/views.py:endpoint_list`, `row_matches_query`, `build_endpoint_row`, `templates/dashboard/endpoint_list.html`, `static/js/endpoint_list.js` e parciais de tabela/cards/health.

## 6. Associação automática e exceções

- O sistema pode **sugerir** vínculo a partir de serial válido e consistente.
- **Não** gerar patrimônios para todos os endpoints automaticamente; primeiro registro/cadastro é explícito.
- **Não** vincular automaticamente por hostname.
- Para serial inconfundível e evidência consistente, permitir confirmação com um clique. Não tornar associação irreversível.
- Se o mesmo patrimônio reaparecer em novo agente após formatação, o histórico é recuperável porque a FK dos registros aponta para o UUID patrimonial; encerrar o vínculo antigo e estabelecer o novo com trilha de auditoria e confirmação apropriada.
- Conflitos devem ser resolvidos pelo operador; endpoints duplicados/sem serial devem poder registrar patrimônio novo manualmente, sem criar falso serial.
- Nunca excluir nem reescrever dados históricos do endpoint antigo para "unificar" identidades.

## 7. Testes e critérios de conclusão

Testes backend e frontend focados:

1. Patrimônio provisório único gerado sob concorrência; normalização e busca case-insensitive.
2. Dois ativos não podem ter o mesmo serial válido normalizado; seriais inválidos/vazios não causam colisão falsa.
3. Um endpoint não pode ter simultaneamente dois vínculos ativos; transição de vínculo preserva histórico.
4. Nova instalação do agente com mesmo serial válido encontra o patrimônio para sugestão, preservando registros anteriores.
5. Mudanças de hostname/IP/usuário não trocam o ativo.
6. CRUD mínimo de registros (criação e leitura; edição/exclusão de registro somente se implementadas com trilha de auditoria), autor e data.
7. Aba Registros abre/carrega corretamente e não interfere nas abas atuais, inclusive em tela mobile.
8. Busca por patrimônio `q` funciona nos modos tabela/cards/health, antes da paginação e nas respostas AJAX, sem afetar filtros atuais.
9. Permissões, CSRF, escopo do endpoint, serial conflitante e dados ausentes.
10. Nenhuma alteração funcional do agente Windows, updater, coleta, jobs ou comportamento de delete/purge.

Entregar branch com código, migrações geradas/testadas, testes passando, commit, push, PR e relatório objetivo. **Não fazer deploy de RC49 nem merge direto na main**: aguardar revisão do responsável. Não criar staging/backup/checkpoint para esta fase; teste em banco de desenvolvimento.

## 8. Escopo postergado

- Nomenclatura oficial e segregação das **duas empresas**, etiquetas e QR Code.
- Integração de registros com chamados e tarefas (próxima RC).
- Gestão patrimonial completa em menu próprio (não desejada pelo responsável).
- Fluxos especializados de transferência/posse, manutenção externa, fornecedores/custos/garantia e anexos.
- Autodetecção de formatação a partir do inventário.
- Mudança da política de exclusão/purge de endpoints, a discutir com Codex 1.
- NOC, software deployment, políticas, AD em grupos, impressoras, terminal, serviços Windows e Stable: frentes independentes documentadas no roadmap.

**Próxima negociação:** após a demonstração funcional de RC49-01A, decidir a nomenclatura por empresa e os próximos recursos de Registros; não presumir os formatos oficiais.
