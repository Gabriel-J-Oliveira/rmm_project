# PKG-01 — recursos de idiomas

> Correção após PKG-02: a sobreposição por cópia simples usada neste
> experimento não reproduziu as decisões de cópia de `dotnet publish` no mesmo
> diretório. No builder reproduzido, as cinco DLLs divergentes ficaram com o
> hash Desktop, não com o hash de Diagnostics. As medições abaixo pertencem à
> montagem por cópia simples; a comparação fiel e a análise estão em
> [rc46-pkg02.md](rc46-pkg02.md).

Experimento no HEAD `cbe3d23fb514590c5f49cb7305c5ccbe622cbc39`, SDK
8.0.427, `win-x64`, Release e self-contained. Nenhum builder ou contrato de
produção foi alterado; os artefatos são locais, não assinados e não instaláveis.

## Método e limites

Os cinco projetos Windows, Tray, Updater, Uninstaller e Diagnostics foram
publicados individualmente em diretórios temporários separados, com versões e
parâmetros equivalentes. Na candidata, o argumento efetivamente passado ao
MSBuild foi `-p:SatelliteResourceLanguages="pt-BR;en-US"`, com as aspas
internas preservadas. Os diretórios foram sobrepostos na ordem do builder atual;
antes da cópia, cada caminho repetido foi comparado por SHA-256. A montagem
aplicou as exclusões de nomes, diretórios e extensões do builder e acrescentou
instalador, ícone e metadados equivalentes nas duas variantes.

Não havia raízes de assinatura autorizadas para o experimento. Por isso,
`release-trust-roots.json` é uma **fixture inválida com lista vazia**, e os
metadados de versão são sintéticos e idênticos. Nenhuma assinatura foi criada.
São payloads comparativos, não releases válidas. A compactação dos dois foi
feita pela mesma ferramenta Python `zipfile`, DEFLATE nível 6, e não pelo
`Compress-Archive` do builder; os números de ZIP não são previsão exata de
release oficial. Ambos os ZIPs passaram na verificação CRC.

Uma primeira tentativa com `pt-BR%3Ben-US` foi descartada: embora a propriedade
aparecesse como uma lista ao consultar o MSBuild, a publicação eliminou também
as DLLs `pt-BR`. A candidata medida usa a lista citada e foi verificada pelos
arquivos e por leitura dos recursos. `en-US` não tinha DLL satélite separada na
baseline; o teste demonstrou fallback aos recursos neutros no caso verificado.

## Medidas

| Medida | Baseline | Candidata | Diferença |
| --- | ---: | ---: | ---: |
| Arquivos | 514 | 310 | −204 (−39,69%) |
| Extraído | 169.645.959 bytes (161,79 MiB) | 154.606.511 bytes (147,44 MiB) | −15.039.448 bytes (−8,87%) |
| ZIP | 73.331.045 bytes (69,93 MiB) | 67.946.109 bytes (64,80 MiB) | −5.384.936 bytes (−7,34%) |
| DLLs satélite | 221 | 17 | −204 |

As diferenças em relação à medição histórica decorrem dos artefatos e das
regras de montagem usados aqui, incluindo instalador e fixtures de versão/trust;
não são comparação entre releases assinadas. Todos os 204 arquivos retirados
são `.resources.dll`: 17 para cada cultura `cs`, `de`, `es`, `fr`, `it`, `ja`,
`ko`, `pl`, `ru`, `tr`, `zh-Hans` e `zh-Hant`. Os 17 recursos `pt-BR`
permaneceram. Nenhum outro arquivo foi removido ou acrescentado, e os 310
caminhos comuns têm conteúdo SHA-256 idêntico entre baseline e candidata.

## Colisões entre projetos

Dos caminhos de payload repetidos nas cinco publicações, 462 na baseline e 258
na candidata tiveram ao menos duas origens. Em **cada variante**, cinco
caminhos apresentaram hashes divergentes:

- `Microsoft.VisualBasic.dll`
- `System.Diagnostics.EventLog.dll`
- `System.Diagnostics.EventLog.Messages.dll`
- `System.Drawing.dll`
- `WindowsBase.dll`

Cada um teve duas variantes de conteúdo: as publicações do Tray/Updater
diferiram das publicações do serviço/Uninstaller/Diagnostics. A ordem reproduzida
seleciona a cópia de Diagnostics por último. As cinco colisões e os hashes são
iguais nas duas variantes; são uma característica preexistente da sobreposição,
não uma regressão demonstrada do filtro de idiomas. Porém, equivalência
funcional dos binários divergentes não foi comprovada. Por exemplo, as duas
versões de `WindowsBase.dll` têm tamanhos diferentes; não é seguro inferir
compatibilidade pelo nome.

## Validação

- As duas publicações dos cinco projetos e o build Release do projeto de testes
  do agente terminaram com sucesso. Ambos os payloads contêm os cinco EXEs,
  `NightOwl.Agent.Shared.dll`, ícone, instalador, `agent.version.json`, fixture
  de trust, `coreclr.dll`, `hostfxr.dll`, `hostpolicy.dll` e runtimeconfigs;
  ambos os ZIPs passaram no CRC. A fixture não valida assinatura ou cadeia real.
- Um programa de teste temporário, executado em cópias isoladas de cada payload,
  leu os três conjuntos de recursos de strings do Windows Forms. Para `pt-BR`,
  `en-US` e cultura neutra, quantidades e SHA-256 dos mapas de strings foram
  idênticos entre as variantes. O conjunto principal continha 2.272 strings;
  os dois diálogos inspecionados continham 21 e 109. O `pt-BR` foi de fato
  localizado, e os hashes `en-US` e neutro coincidiram. Um erro de conversão
  `Int32` também teve a mesma mensagem nas três culturas e nas duas variantes;
  nesse caso a mensagem permaneceu em inglês. Isso não valida todas as telas
  ou mensagens dos cinco processos.
- Diagnostics sem comando e Uninstaller com argumento inválido iniciaram e
  retornaram o código de rejeição esperado nas duas variantes; não houve
  instalação. Serviço, Tray e Updater **não foram executados a partir dos
  payloads experimentais**, para evitar operações reais neste host.
- Passaram os testes .NET `--hardware-only`, incluindo isolamento de inventário
  e heartbeat, a fixture PowerShell da coleta WinRM e o teste de serialização
  .NET/requisição simulada e recebimento autenticado no backend. A execução
  focada Django incluiu também os testes de cadência de inventário: 10 testes
  aprovados. Passaram ainda as fixtures de no-op do instalador, preparação
  WinRM e trust do bootstrap. Não houve alteração de serviço ou firewall real.

## Decisão

**Investigar as colisões antes de adotar.** O ganho de 7,34% no ZIP ultrapassa a
referência de 5% e o filtro é promissor, sem diferença identificada nos recursos
e arquivos comuns testados. A compatibilidade integral, sobretudo dos binários
com cinco colisões e dos processos em execução, ainda não foi demonstrada.
Manter a publicação atual para o piloto até homologação em VM isolada com os
cinco componentes, serviço, Tray, updater, repair/rollback e trust assinados.

Se os gates passarem, a menor mudança proposta é aplicar a propriedade
`SatelliteResourceLanguages` somente às publicações dos cinco projetos no
builder, com lista de culturas corretamente citada, mais uma verificação
automatizada dos recursos preservados e das colisões. Isso deve preservar
formato e contratos; não foi aplicado nesta rodada. Qualquer mudança na ordem de
sobreposição ou no vencedor de DLL divergente exige investigação própria.

Os detalhes temporários de medição, caminhos removidos, outputs e colisões
estão em `C:\Users\gabri\AppData\Local\Temp\nightowl-pkg01-xbw2lels`;
podem desaparecer com a limpeza do diretório temporário. Não foram assinados,
distribuídos ou instalados.
