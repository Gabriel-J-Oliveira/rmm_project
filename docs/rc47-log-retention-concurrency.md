# RC47 — contenção dos logs JSONL

O sidecar `.lock` é mantido aberto durante expiração de arquivos, inspeção da cauda, rotação e escrita. A implementação anterior aguardava 40 intervalos de 10 ms e repetia qualquer `IOException`. Em Windows local, cinco testes com um escritor segurando o lock por 700 ms produziram erro de compartilhamento 32 após 624–640 ms; o escalonamento tornou o tempo real maior que os 400 ms nominais. Isso reproduz a limitação, mas não comprova a causa exclusiva da falha original do publicador.

Com quatro threads e três processos concorrentes, rotação e leitor, cinco execuções locais verificaram 421 registros únicos cada. As esperas máximas observadas chegaram a 2.063 ms, incluindo contenção e escalonamento; p95 por grupo de concorrentes chegou a 252 ms. A maior rotação observada foi 6,03 ms e o trecho instrumentado da seção crítica, excluindo o fechamento, chegou a 15,06 ms. O bloqueio controlado durou pelo menos 800 ms. As métricas de espera são de relógio de parede e não isolam o tempo de CPU nem o atraso de agendamento. Não são promessa de latência em produção.

A nova política usa prazo monotônico de cinco segundos, com verificação antes de uma nova tentativa; o valor oferece margem sobre a espera máxima observada sem espera infinita. Apenas conflitos de compartilhamento Windows 32/33 (ou EAGAIN em plataformas Unix) são repetidos; outros erros propagam. O prazo pode ser ultrapassado pelo escalonamento ou por chamada do sistema, e não garante justiça entre processos. No prazo, uma `IOException` explícita mantém a falha original como causa. O sidecar permanece estável e `FileShare.None` é preservado.

A suíte mantém testes de acesso negado, disco cheio simulado, escrita parcial, sanitização, proteção de arquivos e fila pendente. O novo teste sincroniza threads e processos, usa leitor e rotação simultâneos, verifica todas as identidades sem perdas ou duplicações, cobre timeout curto injetado, prazo público de cinco segundos e aquisição posterior. O CI Windows executa o projeto Shared.Tests completo.

O publicador `-DryRun` exige a configuração e chave de assinatura autorizadas. Nesta máquina, a configuração padrão e as variáveis de assinatura não estavam disponíveis; a tentativa terminou em `validation_failed` por ausência de `SigningKeyId`, antes do builder. Não houve release publicada. Após o merge, executar na máquina de publicação configurada, com a árvore limpa e o trust existente:

```powershell
cd <caminho-do-repositorio>
git checkout main
git pull --ff-only
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\Publish-NightOwlAgentRelease.ps1 -Version '0.1.1.0-rc47' -DryRun
```

Não usar `-SkipTests` nem publicar automaticamente. Confirmar o resultado do manifesto e da assinatura no fluxo oficial antes da publicação autorizada separadamente.

## Investigação da falha intermitente na validação integrada

O resultado original não continha tempos nem identificação do detentor, portanto não permite atribuir aquela ocorrência específica a contenção, starvation ou outro atraso. Dez execuções normais do teste anterior passaram nesta estação Windows; não são prova de ausência de falha sob carga.

Foi identificado e reproduzido um defeito do fixture: os quatro threads começavam a esperar pelo lock antes de três processos completarem sua inicialização, enquanto o detentor permanecia bloqueado aguardando sua prontidão e depois dormia 800 ms. Com atraso de 4.400 ms no início de um dos processos, três execuções independentes falharam: o detentor manteve o lock por 5.340–5.348 ms e os quatro threads atingiram o timeout de aquisição de cinco segundos. O atraso foi deliberadamente introduzido apenas no teste; não é medição da estação da falha original.

Os concorrentes agora sinalizam prontidão antes de o detentor adquirir o lock. Então todos recebem o sinal de partida, o lock fica intencionalmente ocupado por 800 ms e a carga continua com quatro threads, três processos, leitor e rotação. O cenário adicional de processo lento é executado no CI Windows sem repetir testes que falham. Em três execuções locais com atraso de 4.400 ms, a preparação levou 4.529–4.561 ms antes da aquisição; o lock controlado ficou ocupado por 802–810 ms, todos os processos terminaram com código 0 e os 421 registros permaneceram íntegros. O teste público ainda verifica o timeout real de cinco segundos.

As métricas agora identificam processo, thread e trabalhador; falhas de I/O também registram etapa, tempo e códigos, sem conteúdo dos registros. O tempo de espera continua incluindo escalonamento; a medição da seção crítica termina antes do descarte dos streams. O lock não garante ordem justa entre processos, portanto starvation sob outras cargas permanece um risco residual não comprovado. Não houve alteração no mecanismo de produção ou no prazo de aquisição nesta investigação.
