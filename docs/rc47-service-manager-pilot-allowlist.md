# Gerenciador de Serviços — allowlist do piloto

`NIGHTOWL_SERVICE_COLLECTION_ENABLED` é a autorização global do recurso. Ela não seleciona endpoints. A consulta só é autorizada quando o UUID do endpoint também consta em `NIGHTOWL_SERVICE_COLLECTION_ALLOWED_ENDPOINT_IDS`.

## Configuração

O padrão da allowlist é vazio e nega todas as consultas. Um valor malformado impede o Django de iniciar, em vez de ignorar a entrada ou abrir o acesso. A lista aceita UUIDs completos separados por vírgula; hostname, IP, prefixo ou correspondência parcial não são aceitos.

Negar tudo:

```dotenv
NIGHTOWL_SERVICE_COLLECTION_ENABLED=false
NIGHTOWL_SERVICE_COLLECTION_ALLOWED_ENDPOINT_IDS=
```

Exemplo com um UUID sintético (não é um endpoint real):

```dotenv
NIGHTOWL_SERVICE_COLLECTION_ENABLED=true
NIGHTOWL_SERVICE_COLLECTION_ALLOWED_ENDPOINT_IDS=00000000-0000-4000-8000-000000000001
```

Antes de habilitar, confirme que o UUID identifica o registro correto e que a lista contém apenas endpoints aprovados. O operador deve ainda ter as permissões técnicas, e o endpoint precisa estar ativo, online, com identidade única e heartbeat recente anunciando `collect_services_paged:2`. A consulta continua limitada ao protocolo paginado somente leitura.

## Revogação e jobs já criados

Remover o UUID (ou esvaziar a lista) e recarregar/reiniciar todos os processos Django que atendem a API e o pull do agente. Isso bloqueia novas consultas. Um `collect_services` ainda `queued` será cancelado e auditado no próximo pull, sem ser enviado. Um job que já foi enviado ao agente (`sent`) ou iniciado (`running`) não pode ser recolhido pelo servidor; a revogação não desfaz trabalho já entregue. Verifique o estado do job antes de considerar a autorização revogada operacionalmente concluída.

Não há allowlist por hostname. Registros com hostname igual e UUIDs distintos são decisões independentes; uma ambiguidade de identidade conhecida deve bloquear a inclusão até reconciliação humana comprovada.

Não habilitar esta configuração em produção como parte do deploy do backend. A ativação do piloto requer autorização operacional separada.
