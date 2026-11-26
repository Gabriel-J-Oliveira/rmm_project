# RC49-01B — Homologação da remoção verificada

## Contrato

O painel reusa o modal de credenciais da instalação. Para a exclusão definitiva, a sessão atual e a conta reapresentada precisam da autorização de purge. A confirmação inclui o hostname exibido, o UUID do endpoint na rota e o Machine ID atual. A tela oferece uma única ação. O modal conserva a desinstalação local para operadores autorizados e a limpeza local legada para agentes que ainda não anunciam o receipt verificado. As opções locais preservam o cadastro operacional anterior.

O pedido cria um job `uninstall_agent` sobre o mecanismo existente. Agentes offline podem receber o job ao voltar dentro de 24 horas, desde que já tenham anunciado suporte no último heartbeat conhecido. A entrega exige heartbeat recente da mesma identidade e versão, protocolo v2 e ausência de execução incompatível. O contrato requer o desinstalador independente 0.1.0.8 ou superior e HTTPS válido.

O desinstalador remove a instalação e os diretórios conhecidos do NightOwl. Confere serviço, tarefa Tray, processos do produto, arquivos, logs e estado local. Um caminho de instalação fora da raiz NightOwl, reparse point, divergência de configuração ou limpeza incompleta impede a confirmação. O processo independente conserva apenas os binários do runner e, enquanto necessário, o handoff protegido com a credencial limitada ao receipt da operação. A credencial geral do agente não faz parte do handoff.

O receipt específico do job vai para a rota existente de resultados. O backend valida o token limitado, o endpoint, o estado e a evidência de limpeza. O mesmo receipt pode ser confirmado de novo se a resposta tiver se perdido. Se a máquina reiniciar ou o processo sair antes do ACK, um administrador pode executar, com a raiz NightOwl da instalação e pelo runner original:

```powershell
& '<raiz NightOwl>\Updates\Runner\uninstall-<job UUID>\NightOwl.Agent.Uninstaller.exe' --resume-verified-removal
```

Esse comando atua na própria máquina. Use somente na VM autorizada e com o job auditado. O handoff tem validade de até sete dias contados da solicitação. Após o prazo, uma nova autorização depende de restaurar o canal do agente ou de um procedimento assistido. Se o resultado for incerto, o endpoint permanece visível.

## Banco e histórico

A cadeia de migrations é 0052 (identidade física), 0053 (auditoria independente) e 0054 (vínculo entre identidade e receipt, com ajuste específico no guard de DELETE de alvos de rollout). Nenhuma faz limpeza retroativa.

O endpoint, inventário, jobs e outros registros operacionais dependentes são excluídos após o receipt. As ocorrências de automação do UUID removido são eliminadas. Campanhas de rollout abertas impedem a remoção. Para campanhas terminadas, somente o alvo daquele UUID pode ser eliminado, e somente na transação de uma remoção auditada. Outros alvos, campanhas, definições, patrimônio, vínculos históricos e registros são preservados. As definições mantêm a revisão histórica dos alvos e uma instalação nova precisa de atribuição própria.

O rollback de código ou schema não restaura endpoints já removidos nem arquivos já limpos no Windows.

## Aceite em laboratório

1. Usar endpoint descartável e pacote assinado com agente e desinstalador compatíveis. Conferir a capability e o botão único. Com um agente anterior, confirmar que o modal oferece as operações locais compatíveis e preserva o cadastro.
2. Solicitar com operador autorizado, testar credencial incorreta e revogação do token do agente. Para o cenário offline, verificar fila, retorno antes do prazo e expiração sem exclusão.
3. Bloquear um arquivo NightOwl controlado e confirmar que o endpoint permanece após falha. Liberar o arquivo e retomar o mesmo runner. Interromper a comunicação depois da limpeza, restabelecer e confirmar o ACK do mesmo receipt.
4. Conferir serviço, tarefa, processos e diretórios locais após confirmação. Verificar a exclusão no backend, a auditoria do último usuário observado com origem e data quando disponíveis e a preservação integral do outro endpoint em automação/rollout.
5. Reinstalar com autorização em hardware físico identificável. Confirmar um novo UUID operacional, o mesmo UUID patrimonial e histórico anterior. Em VM, verificar exclusão e reenrollment, enquanto o vínculo SMBIOS automático deve permanecer bloqueado.
