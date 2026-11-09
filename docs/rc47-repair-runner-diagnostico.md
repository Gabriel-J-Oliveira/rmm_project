# RC47 — diagnóstico do gate Windows de repair

Na estação de publicação, o instalador simulado concluiu com código 0 e a consulta da tarefa Tray retornou. Em seguida, o `Test-Path` da configuração canônica lançou `System.UnauthorizedAccessException` ao montar o payload. O tratamento genérico gravou `failed` com `REPAIR_RESULT_PERSIST_FAILED`, embora a exceção tenha ocorrido antes da persistência.

A causa comprovada é o isolamento incompleto do fixture: o script gerado utiliza `NightOwlPaths.Current.ConfigDir`, apontando para a instalação real da máquina, à qual o processo de teste não tinha acesso. O teste agora substitui exclusivamente essa consulta no script gerado por um caminho de configuração pertencente ao diretório temporário do fixture. Ele exige que a substituição seja encontrada e que a configuração simulada exista. O runner e o caminho canônico de produção não foram alterados. O cenário com espaços inclui um apóstrofo no caminho de configuração.

O timeout de 15 s em outra execução permanece sem causa comprovada. O fixture registra etapas, durações, códigos, estados e categorias sanitizadas antes da limpeza, sem stdout/stderr bruto, tokens ou caminhos. A fila indisponível continua preservando a propriedade externa; `--repair-only` integra o gate Windows.

No teste de argumentos, `ReadToEnd()` ocorria antes do timeout e podia bloquear indefinidamente. A leitura agora é assíncrona e um processo de teste com descendente valida o encerramento da árvore após timeout. Isso não prova a causa do timeout no repair, que já usava leitura assíncrona. Nenhum prazo foi aumentado.

Executar novamente na estação de publicação e compartilhar somente `REPAIR_TEST_DIAGNOSTIC`. Confirmar que o fixture passa e investigar separadamente o timeout se voltar a ocorrer. Não publicar nem executar deploy automaticamente.
