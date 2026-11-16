# RC47 — Administração auditada do grant ConPTY

## Escopo e guardas

`manage.py terminal_conpty_grant` administra somente o piloto fixado no código: endpoint UUID `c97045f2-35ff-48f1-afa5-095173fbb3dd`, machine ID `1eca4af2-4950-4a2d-9b9f-fa7010a62464` e beneficiário `gabriel.oliveira`. O comando não aceita hostname, IP, curingas ou lote. Toda chamada exige execução em Linux como root via `sudo`, com `SUDO_USER`/`SUDO_UID` coerentes e usuário invocador na configuração `NIGHTOWL_TERMINAL_GRANT_ADMIN_USERS`. Essa configuração é vazia por padrão.

O default é somente leitura/prévia. `grant` ou `revoke` só alteram estado com `--apply`, motivo, referência de aprovação e confirmação repetida do UUID. A validade aceita é de 1 a 60 minutos. O operador NightOwl precisa estar ativo, ser técnico e possuir `agents.open_terminalsession`; endpoint, UUID e machine ID são revalidados sob lock. Um grant com `allow_admin_auth=true` causa bloqueio para revisão; o comando não altera esse campo. Nenhuma flag do terminal é alterada.

## Comandos

Os exemplos são modelos para uma janela futura autorizada. Substituir `CHG-...` pela referência real aprovada. Não executar na produção sem aprovação operacional específica.

```bash
sudo /opt/nightowl/venv/bin/python /opt/nightowl/manage.py terminal_conpty_grant \
  --action status \
  --endpoint-id c97045f2-35ff-48f1-afa5-095173fbb3dd \
  --machine-id 1eca4af2-4950-4a2d-9b9f-fa7010a62464 \
  --operator gabriel.oliveira
```

O comando requer que o operador Unix esteja em `NIGHTOWL_TERMINAL_GRANT_ADMIN_USERS`. Configure essa allowlist em mudança operacional separada e aprovada; não coloque nomes em código nem habilite `admin_auth`.

Prévia de concessão/renovação (não altera o banco):

```bash
sudo /opt/nightowl/venv/bin/python /opt/nightowl/manage.py terminal_conpty_grant \
  --action grant --endpoint-id c97045f2-35ff-48f1-afa5-095173fbb3dd \
  --machine-id 1eca4af2-4950-4a2d-9b9f-fa7010a62464 \
  --operator gabriel.oliveira --ttl-minutes 30 \
  --reason 'Homologação ConPTY aprovada' --approval-ref 'CHG-IDENTIFICADOR'
```

Aplicação real exige os mesmos argumentos, mais `--apply --confirm-endpoint c97045f2-35ff-48f1-afa5-095173fbb3dd`. Revogação usa `--action revoke`, motivo e referência, e também exige `--apply` e `--confirm-endpoint`. Revogar grant inexistente é rejeitado. O comando não inicia o relay e não solicita sessão.

Concessão/renovação após aprovação explícita:

```bash
sudo /opt/nightowl/venv/bin/python /opt/nightowl/manage.py terminal_conpty_grant \
  --action grant --endpoint-id c97045f2-35ff-48f1-afa5-095173fbb3dd \
  --machine-id 1eca4af2-4950-4a2d-9b9f-fa7010a62464 \
  --operator gabriel.oliveira --ttl-minutes 30 \
  --reason 'Homologação ConPTY aprovada' --approval-ref 'CHG-IDENTIFICADOR' \
  --apply --confirm-endpoint c97045f2-35ff-48f1-afa5-095173fbb3dd
```

Revogação após aprovação explícita:

```bash
sudo /opt/nightowl/venv/bin/python /opt/nightowl/manage.py terminal_conpty_grant \
  --action revoke --endpoint-id c97045f2-35ff-48f1-afa5-095173fbb3dd \
  --machine-id 1eca4af2-4950-4a2d-9b9f-fa7010a62464 \
  --operator gabriel.oliveira --reason 'Fim da homologação' \
  --approval-ref 'CHG-IDENTIFICADOR' --apply \
  --confirm-endpoint c97045f2-35ff-48f1-afa5-095173fbb3dd
```

## Auditoria e revogação

Cada mudança cria `AuditEvent` transacional `terminal.grant_granted`, `terminal.grant_renewed` ou `terminal.grant_revoked`, com executor Unix/UID, beneficiário, UUID/machine ID, validade, motivo e referência de aprovação. Falha de auditoria aborta a transação. Tickets, credenciais, comandos e saída do terminal não fazem parte do evento. Motivos/referências com padrões de segredos ou caracteres de controle são rejeitados.

Concessão/renovação trava primeiro o endpoint e depois o grant. A criação de sessão existente também trava o endpoint antes de consultar autorização, serializando a admissão com revogação. Revogar desabilita somente `enabled` e `allow_conpty`, grava o evento e fecha sessões ConPTY ativas daquele operador/endpoint através de `close_session`; histórico não é apagado. O relay já revalida a sessão a cada ciclo de controle (~1 s), termina os dois peers WSS ao detectar a revogação, e o agente Windows existente descarta o objeto ConPTY/process tree no fechamento. Não há garantia de interromper uma sessão fora do relay saudável; validar o fechamento observado na homologação.

## Implantação e homologação

Não há migration. Implantar código somente após revisão/merge normal e janela autorizada. Antes de conceder: confirmar o UUID e machine ID pelo backend, usuário ativo/permissão, allowlist do executor aprovada, estado do grant, `allow_admin_auth=false`, capabilities recentes e usuário interativo/Tray no piloto. Revisar a prévia e obter aprovação registrada; aplicar grant de duração mínima necessária.

Testar uma sessão assistida com consentimento visível no Tray, identidade ConPTY não-SYSTEM, fluxo básico de entrada/saída/redimensionamento, revogação durante sessão, fechamento de ambos os peers e impossibilidade de reconectar. Confirmar processo encerrado no cliente, grant revogado/expirado, trilha de auditoria completa e ausência de conteúdo sensível nos logs. Ao terminar, revogar pelo mesmo comando; parar o relay apenas conforme a janela/estado operacional previamente aprovado. Não habilitar `admin_auth`.

Rollback funcional: revogar o grant pelo fluxo auditado. Rollback de código é independente e não requer migration ou remoção de histórico; manter eventos de auditoria e sessões históricas. Esta PR não executa concessão real, não inicia serviço, não altera flags/infraestrutura e não atualiza agente Windows.
