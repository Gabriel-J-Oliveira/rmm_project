# RC47 — diagnóstico do gate Windows de repair

Na estação de publicação, o instalador concluiu com código 0, mas a montagem do payload terminou com `System.UnauthorizedAccessException`. O tratamento genérico gravou `failed` com `REPAIR_RESULT_PERSIST_FAILED`, embora a exceção tenha ocorrido antes da persistência. O evento ocorreu depois do início da consulta da tarefa Tray; uma segunda instrumentação distingue essa consulta da verificação subsequente da configuração canônica. O timeout de 15 s de outra execução permanece sem causa comprovada.

A hipótese inicial de acesso negado à tarefa Tray motivou um tratamento específico de `UnauthorizedAccessException`, com `Unknown` em vez de `NotFound`; o teste injetado passou, mas a execução real continuou falhando. Essa hipótese não está confirmada e o tratamento será reavaliado após o resultado da nova instrumentação.

O fixture registra apenas etapas, durações, códigos, estados e categorias sanitizadas antes de limpar o diretório temporário. Não imprime stdout/stderr bruto, tokens ou caminhos. O cenário com fila indisponível continua preservando a propriedade externa; `--repair-only` integra o gate Windows.

No teste de argumentos, `ReadToEnd()` ocorria antes do timeout e podia bloquear indefinidamente. A leitura agora é assíncrona e um processo de teste com descendente valida o encerramento da árvore após timeout. Isso não prova a causa do timeout no repair, que já usava leitura assíncrona. Nenhum prazo foi aumentado.

Executar novamente na estação de publicação e compartilhar somente `REPAIR_TEST_DIAGNOSTIC`. Confirmar qual campo lança a exceção antes de concluir a correção. A nova versão Windows exige assinatura e homologação; não publicar nem executar deploy automaticamente.
