# BOOT-02 — instalação silenciosa

O comando de deployment manual continua obtendo o bootstrap por HTTPS e usando
token individual temporário. O bootstrap suprime progresso e saída do
instalador filho, inclusive quando uma release anterior ignora a variável de
silêncio; erros são resumidos com código e caminho do log. Preserva 0/1/2 em
`$global:NightOwlDeploymentBootstrapExitCode` sem encerrar o host PowerShell.
O PowerShell 5.1 é tratado para que stderr redirecionado não esconda o código
real do processo. O bootstrap registra eventos em `%TEMP%\NightOwlDeployment`,
com rotação de 1 MiB e uma cópia, e redige valores sensíveis. O instalador
atual reconhece `NIGHTOWL_INSTALLER_SILENT=1`, mantém seu log limitado e
redige tokens conhecidos nos logs e no relatório. O atalho de instalação
saudável, cache, trust, assinatura, staging e confirmação não foram removidos.

O wrapper WinRM continua exigindo SID/AD, release e preflight, duas validações
da release e ausência de instalação, e roda filho sem janela e não interativo.
Passa a variável de silêncio; versões anteriores continuam compatíveis, pois a
saída do filho já era drenada. A interface de lotes projeta estados de
instalação, pendência, operação confirmada, reparo, falha e resultado incerto
sem substituir os estados técnicos; não chama de instalada uma operação sem
prova do serviço. WinRM indisponível orienta caminho manual ou GPO autorizada.
O comando legado direto é identificado como tal na interface.

## Bloqueio GPO/SYSTEM

**BOOT-02 não está concluído nos três métodos.** O bootstrap de deployment
exige `AgentDeploymentToken` individual; o backend aceita esse token apenas
enquanto ativo, não expirado e não usado. Após enrollment ele não pode ser
reutilizado para obter metadata no próximo startup. O enrollment automático por
domínio/computador existente pertence ao instalador canônico e não fornece ao
bootstrap um token de deployment reutilizável nem o contrato de callback por
tentativa. Não existe neste repositório uma camada GPO que obtenha uma nova
credencial individual de forma autorizada e repetível. Distribuir o token
individual em SYSVOL ou criar um novo mecanismo de enrollment violaria os
limites desta tarefa. Portanto nenhum wrapper GPO foi apresentado como solução,
nenhuma GPO foi aplicada e o comportamento 0/1/2 em SYSTEM/GPO não foi
homologado. Os testes 0/1/2 exercitam o bootstrap com fixtures, não SYSTEM real.

Testes focados passaram para silêncio, no-op, falha e preservação de exit code,
rotação e redação de logs, contratos de trust/cache/assinatura/rollback,
identidade e heartbeat, sem instalação em endpoints. WinRM e serviços foram
simulados; não houve execução em domínio, GPO ou servidor corporativo. Para
homologação dos caminhos implementados também são necessários release assinada
com o instalador atualizado, deploy autorizado do backend e laboratório Windows
com WinRM/AD. Até lá, releases anteriores continuam usando redirecionamento no
bootstrap e não recebem os novos eventos de silêncio no instalador.
