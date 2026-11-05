# PKG-02 — colisões do builder

**Classificação: inconclusiva por falta de validação específica.** As cinco
colisões foram identificadas e probes Core/Desktop conseguiram carregar as
identidades no layout reproduzido do builder. Isso ainda não comprova o
funcionamento dos cinco processos, a instalação ou o lifecycle em VM. Por esse
motivo, a propriedade de idiomas não foi incorporada ao builder; a publicação
padrão e todos os contratos continuam inalterados.

## Correção do PKG-01

O PKG-01 publicou os cinco projetos separadamente e depois os sobrepôs com
cópia simples. Nessa montagem, a cópia Core de Diagnostics venceu. Isso **não
reproduziu** o comportamento observado no builder, que chama `dotnet publish`
cinco vezes diretamente no mesmo diretório. Com SDK 8.0.427, repetimos esse
procedimento para baseline e candidata, na ordem Windows, Tray, Updater,
Uninstaller, Diagnostics. Em ambos, as cinco versões **Desktop** permaneceram
no diretório final; os publishes posteriores não as sobrescreveram. Também
conferimos esses mesmos hashes no artefato de desenvolvimento previamente
publicado diretamente no diretório compartilhado. O motivo interno preciso da
decisão de cópia do SDK não foi estabelecido; não deve ser tratado como regra
para outras versões ou ambientes.

Um probe baseado na montagem por cópia simples anterior não conseguiu resolver
`WindowsBase, Version=8.0.0.0` ao carregar `System.Windows.Window`; substituir
essa DLL em uma cópia temporária pela versão Desktop permitiu a resolução. Essa
falha foi da **montagem experimental PKG-01**, não foi reproduzida na montagem
em diretório compartilhado. Não há evidência aqui de falha WPF no pacote real
do NightOwl, nem de uso de WPF pelos fluxos NightOwl. Isso não é justificativa
para mudar a ordem de publicação ou selecionar DLLs manualmente.

## As cinco colisões

Cada caminho é publicado pelos cinco projetos. Windows, Uninstaller e
Diagnostics produziram o hash **C**; Tray e Updater produziram o hash **D**.
Em `.deps.json`, VisualBasic, Drawing e WindowsBase vêm do runtime pack
`Microsoft.NETCore.App.Runtime.win-x64/8.0.33` nos primeiros e do
`Microsoft.WindowsDesktop.App.Runtime.win-x64/8.0.33` nos últimos. Os dois
EventLog vêm do pacote `System.Diagnostics.EventLog/8.0.1` nos primeiros e do
runtime pack Desktop 8.0.33 nos últimos. Todas as identidades são neutras. Os
hashes abaixo são SHA-256 completos; **D venceu nas duas reproduções fiéis**.

| Arquivo | Identidade de assembly / versão de arquivo C → D | SHA-256 C / D | Risco observado |
| --- | --- | --- | --- |
| `Microsoft.VisualBasic.dll` | `10.0.0.0` / `8.0.3326.52219` → `10.1.0.0` / `8.0.3326.52211`; token `b03f5f7f11d50a3a` | C `1a485a7aaffa4083da03da322ec154bc1b59d243feab2d082fda050bb25def7e`; D `874ab40b8c0333e9400bc6a67f1b032cd83f7a8795810a33c3a4acfcc88618a1` | Identidade difere. Probe Core pediu 10.0 e carregou 10.1; probe Desktop carregou 10.1. Uso real de APIs ainda requer teste. |
| `System.Drawing.dll` | `4.0.0.0` / `8.0.3326.52219` → `8.0.0.0` / `8.0.3326.52211`; token `b03f5f7f11d50a3a` | C `11b9054ce0eb63b743bcba81a34c5bca253e6ed9490c632fea2197d518d86c42`; D `931c7b342ea228826976c9d8d3f59fcd0225ca3e069dc463c1ecb3231552b1a8` | Identidade difere. Core pediu 4.0 e carregou 8.0; Desktop carregou 8.0 e recursos Windows Forms. Desenho/ícones reais do Tray continuam sem teste. |
| `WindowsBase.dll` | `4.0.0.0` / `8.0.3326.52219` → `8.0.0.0` / `8.0.3326.52221`; token `31bf3856ad364e35` | C `daccb377aafa51e69ebb8112fc2320adc3738c95f73b8b4cba5e02c87924211a`; D `c4379d217a72659d1e8bb08d9f59b213080ae94ce8f2f811be9aa5d6fe3ba858` | Core pediu 4.0 e carregou 8.0; Desktop carregou 8.0 e resolveu `System.Windows.Window`. Uso e lifecycle dos componentes reais não foram comprovados. |
| `System.Diagnostics.EventLog.dll` | Ambas `8.0.0.0`/token `cc7b13ffcd2ddd51`; C arquivo `8.0.1024.46610`, produto 8.0.10; D arquivo `8.0.3326.52219`, produto 8.0.33 | C `8343020e3752dc8fae7598816570aa30b041f835412a76dac7f65b229ed9532b`; D `1d6d1fff06429c5e201d3ab7f8c2fd2a7724b843d4efccbab9a7d4b5edcf518d` | Mesma identidade, implementações diferentes. Ambos os probes carregaram D e o Core consultou `EventLog.Exists("Application")` com sucesso. Diagnóstico e logging completos não foram exercitados. |
| `System.Diagnostics.EventLog.Messages.dll` | Ambas `8.0.0.0`/token `cc7b13ffcd2ddd51`; metadados `FileVersion`/`ProductVersion` indisponíveis | C `dbfb7c81fb62a0bd3f77da92496640f179e96dd0c1a41a0d3c5d2a2445a3be46`; D `4cf7524327feb872e6b724aff4d79cac430a2f60a7719e37fbd647401bae21ad` | D carregou nos dois probes; equivalência das mensagens e recursos EventLog ainda não foi comprovada. |

As diferenças de origem/identidade são explicadas pelos conjuntos de runtime e
pacote distintos. A resolução testada não equivale a uma garantia de todas as
APIs. Varredura de referências de assembly identificou, entre os binários
NightOwl inspecionados, referência direta do Diagnostics a EventLog 8.0; não
resolve dependências dinâmicas nem valida todos os caminhos de execução.

## Comparação fiel de publicação

As duas publicações usaram SDK 8.0.427, versões e configurações equivalentes,
diretórios compartilhados separados, e a candidata passou
`SatelliteResourceLanguages="pt-BR;en-US"` corretamente citado. Na montagem,
aplicamos o filtro do builder e as mesmas fixtures de ícone, instalador, versão
e trust vazio. Os ZIPs foram produzidos com a mesma ferramenta/nível Python
DEFLATE 6; são **não assinados, não instaláveis**, e não incluem trust válido.
O builder oficial não foi modificado nem executado para criar release.

| Variante | Arquivos | Extraído | ZIP comparável |
| --- | ---: | ---: | ---: |
| Baseline em diretório compartilhado | 514 | 172.100.607 bytes (164,13 MiB) | 74.430.348 bytes (70,98 MiB) |
| Candidata em diretório compartilhado | 310 | 157.061.159 bytes (149,79 MiB) | 69.045.412 bytes (65,85 MiB) |

Redução de 204 arquivos (39,69%) e 5.384.936 bytes no ZIP (7,23%). Todos os
204 removidos são recursos de idiomas fora da lista; nenhum outro caminho foi
adicionado ou removido, e os 310 caminhos comuns têm hashes iguais. As cinco
colisões de origem são as mesmas nas duas variantes. A diferença em relação ao
PKG-01 resulta das DLLs vencedoras e da montagem mais fiel, não de release
assinada ou de mudança no builder. Não foram verificados todos os contratos de
manifesto em um pacote assinado real.

## Validações e pendências

- Probes self-contained Core e Windows Forms/Desktop executados em cópias das
  publicações compartilhadas carregaram as cinco identidades conforme a tabela;
  o probe Desktop também resolveu `System.Windows.Window`. `pt-BR`, `en-US` e
  neutro produziram os mesmos hashes e contagens de strings Windows Forms e a
  mesma mensagem de erro entre as duas variantes. São probes, não os processos
  reais de serviço, Tray e updater.
- Passaram build Release do agente/testes, fixture de WinRM, trust do bootstrap,
  rejeição de assinatura inválida, cache/mutex, no-op, updater `already_current`
  e rollback simulado, e runner de repair. Diagnóstico e desinstalador no PKG-01
  foram iniciados apenas para rejeitar argumentos inválidos. Não houve instalação
  nem alteração de serviços Windows reais.
- Presença dos caminhos obrigatórios e CRC dos dois ZIPs reproduzidos foram verificados;
  não houve release assinada com raízes autorizadas. Para segurança antes de
  habilitar a opção, faltam testes funcionais dos cinco executáveis no layout
  compartilhado, incluindo Tray/ícones, EventLog/Diagnostics, updater/runner,
  serviço, Uninstaller, instalação, repair e rollback com trust e identidade em
  VM isolada, mais verificação da decisão de cópia em SDKs suportados.

Nenhuma configuração opcional foi acrescentada ao builder, pois a compatibilidade
integral ainda não foi demonstrada. Se os gates passarem, pode-se propor flag
opt-in que aplique `SatelliteResourceLanguages` aos cinco publishes e mantenha
o padrão atual; não se deve copiar DLLs ou alterar a ordem apenas para eliminar
hashes diferentes. Os artefatos/probes locais ficam em
`C:\Users\gabri\AppData\Local\Temp\nightowl-pkg01-xbw2lels` e podem ser
removidos pelo sistema operacional.
