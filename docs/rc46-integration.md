# RC46 — INT-01: consolidação e handoff

## Origem e integração

A branch `integration/rc46` foi criada a partir de
`feature/rc46-agent-cleanup-closure` no commit
`06feb752457c13b24d71f369e090c4334939d515`. A referência local
`feature/rc46-agent-core` em `6fe53cac4f064013fa6993b8c4c56041450d26b9` é
ancestral de `49b984a451b58c748f3298885e81da13b16524db`; este é o HEAD
informado do outro computador e coincide com o remoto consultado. `49b984a`
já é ancestral da branch de fechamento. Não há commits exclusivos dessa fonte,
merge, cherry-pick ou conflitos a resolver. Nenhum bundle foi necessário.
O HEAD final da integração é o commit que contém este documento; o código tem
como base o commit de fechamento acima.

## Componentes e limites

O histórico consolidado contém heartbeat leve e inventário independente,
telemetria nativa e fila persistente, jobs/automações, scripts e ações
administrativas, terminal ConPTY/WSS administrativo, Tray e updater. Também
contém Fast Install manual e WinRM, cache, no-op, assinatura, repair, rollback,
recuperação WinRM e diagnóstico. A presença no histórico e as validações abaixo
não constituem homologação operacional. Preservam-se a publicação convencional
self-contained e a decisão NO-GO para instalação GPO/SYSTEM.

O relay do terminal continua limitado a um processo/event loop ASGI: o gate de
processo único permanece obrigatório, sem relay multiprocesso homologado. Falta
homologar autenticação administrativa real e encerramento dos processos em
Windows. O monitoramento WinRM verifica HTTPS/5986 no IPv4 confirmado e pode
não refletir todos os caminhos de instalação/recuperação. Pré-requisitos de
certificado, DNS, autenticação e firewall dependem do ambiente. Detalhes em
[terminal](rc46-dec04d.md), [WinRM](rc46-fi03.md) e [Fast Install](rc46-boot02.md).

## Validação desta consolidação

- SDK .NET 8.0.427: build `--no-restore` dos projetos Windows Service, Tray,
  Updater, Uninstaller e Diagnostics, todos com sucesso.
- Django: 32 testes focados de controle de terminal, autenticação administrativa,
  contrato de instalação e controle de jobs aprovados; dados e serviços simulados.
- Parser Windows PowerShell 5.1: bootstrap de deployment e instalador canônico
  sem erros de sintaxe. Não representa execução real do instalador.
- `git diff --check`: executado antes do commit documental.

Não houve integração efetiva de código, instalação real ou testes completos.
Os documentos previamente não rastreados `docs/rc46-boot01.md` e
`docs/rc46-fast-install-v2-proposal.md` foram preservados fora do commit.

## Checklist mínimo de laboratório

- Preparar VM Windows isolada e release assinada com trust autorizado; validar
  instalação manual e WinRM, serviço, Tray e heartbeat novo do endpoint correto.
- Validar identidade/configuração, cache/no-op, update, repair, rollback e
  recuperação após falhas; confirmar estados pendentes sem falso sucesso.
- Homologar DNS, certificado HTTPS WinRM, autenticação e diagnóstico, além da
  recuperação opcional; não modificar GPO corporativa.
- Com conta administrativa de laboratório autorizada, validar identidade real,
  ConPTY, WSS, encerramento e filhos, respeitando o gate de processo único.
- Validar coleta e entrega de inventário, telemetria, jobs e ações aprovadas;
  acompanhar recursos e comportamento após desconexão.

Não iniciar piloto ou publicar release apenas com esta validação.
