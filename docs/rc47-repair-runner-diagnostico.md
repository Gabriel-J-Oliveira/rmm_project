# RC47 — diagnóstico do gate Windows de repair

Na estação de publicação, o instalador concluiu com código 0; em seguida, `Get-ScheduledTask` lançou `System.UnauthorizedAccessException` ao montar o payload. O tratamento genérico gravou `failed` com `REPAIR_RESULT_PERSIST_FAILED`, embora a exceção tenha ocorrido antes da persistência. A falha de status foi localizada; o timeout de 15 s de outra execução permanece sem causa comprovada.

A consulta opcional da tarefa Tray agora retorna `Unknown` quando negada por `UnauthorizedAccessException`, preservando status e código reais do instalador. Outras exceções continuam falhando. Testes injetam essa exceção e verificam `completed` com saída 0 e `failed` com saída 7, com `tray_task_status=Unknown` em ambos.

O fixture registra apenas etapas, durações, códigos, estados e categorias sanitizadas antes de limpar o diretório temporário. Não imprime stdout/stderr bruto, tokens ou caminhos. O cenário com fila indisponível continua preservando a propriedade externa; `--repair-only` integra o gate Windows.

No teste de argumentos, `ReadToEnd()` ocorria antes do timeout e podia bloquear indefinidamente. A leitura agora é assíncrona e um processo de teste com descendente valida o encerramento da árvore após timeout. Isso não prova a causa do timeout no repair, que já usava leitura assíncrona. Nenhum prazo foi aumentado.

Na estação de desenvolvimento, o instalador simulado levou cerca de 1,1 s e `Get-ScheduledTask` cerca de 1,0–1,2 s. No runner Windows limpo, o primeiro cenário levou 7 s e `Get-Service` aproximadamente 1,5 s. Essas medições não estabelecem desempenho de produção.

Executar novamente na estação de publicação e compartilhar somente `REPAIR_TEST_DIAGNOSTIC`. Confirmar o resultado real e investigar separadamente o timeout se voltar a ocorrer. A nova versão Windows exige assinatura e homologação; não publicar nem executar deploy automaticamente.
