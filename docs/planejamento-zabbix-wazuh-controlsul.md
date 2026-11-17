# ControlSul — Plano de implantação de Zabbix e Wazuh

> **Status:** planejamento / handoff para execução futura e independente  
> **Data-base:** 17/11/2026  
> **Responsável pela próxima execução:** equipe de Infraestrutura  
> **Repositório de referência:** NightOwl (documento informativo, sem vínculo de deploy)  
> **Importante:** este arquivo não autoriza instalação, alteração de firewall/GPO, credenciais, integrações ou ações de contenção.

## 1. Objetivo e decisão arquitetural

Implantar Zabbix e Wazuh em infraestrutura própria da ControlSul, preferencialmente em **máquinas virtuais separadas sobre um servidor/hypervisor destinado a essa finalidade**, sem concorrer com o runtime de produção do NightOwl.

A arquitetura deve especializar as ferramentas:

| Solução | Papel | Escopo inicial |
| --- | --- | --- |
| **NightOwl** | RMM e centro de operação de **estações Windows**: inventário, suporte, telemetria, jobs e administração autorizada | Parque de desktops/notebooks Windows |
| **Zabbix** | Saúde e disponibilidade da infraestrutura: servidores, hypervisors, rede, storage, impressoras, câmeras, telefones IP e nobreaks | Servidores e dispositivos que exponham métricas por agente, SNMP, ICMP, API ou protocolo suportado |
| **Wazuh** | SIEM/XDR-like analytics: recepção de logs, correlação, detecção, integridade de arquivos e investigação | AD, servidores críticos, eventos Bitdefender e fontes de segurança selecionadas |
| **Bitdefender GravityZone** | Proteção antimalware/EDR e resposta no endpoint, conforme módulos licenciados | Estações e servidores conforme políticas próprias |
| **Active Directory** | Fonte de identidade, computadores e políticas de domínio | AD existente; eventos e auditoria encaminhados ao SIEM |

**Regra explícita:** não instalar agente Zabbix nas estações Windows. O NightOwl continuará fazendo o monitoramento operacional desses endpoints. Agentes Zabbix **em servidores** são permitidos, conforme desenho aprovado. O NightOwl não será expandido agora para gerenciar Linux nem terá um segundo agente Windows Server independente neste projeto.

As integrações do NightOwl com Zabbix, Wazuh, Bitdefender e AD ficam **para uma etapa tardia**, depois de estabilizados o RMM e as duas plataformas. Este projeto não modifica o NightOwl.

## 2. Topologia recomendada

### 2.1 Separação lógica

- **VM Zabbix:** Zabbix Server, frontend HTTPS e banco de dados dedicado à aplicação; separar o banco em outra VM só se os testes de carga e retenção justificarem.
- **VM Wazuh:** componentes de Wazuh Server/Manager, Indexer e Dashboard; para a primeira fase, avaliar um nó único (all-in-one) com armazenamento e RAM adequados, ou distribuir os componentes se o volume de logs exigir.
- **Servidor/hypervisor dedicado:** pode hospedar ambas as VMs, mas elas **não devem compartilhar a mesma instalação de sistema operacional ou banco de dados**.
- **Backups fora do host de origem:** proteger configurações, banco do Zabbix, índices/dados relevantes do Wazuh, certificados e procedimentos de restauração.
- **Rede de gerenciamento:** acesso administrativo por VPN/rede interna com firewall e autenticação forte; não expor dashboards nem portas de agentes/indexadores diretamente à internet.

**Risco aceito a avaliar:** se as duas VMs ficarem no mesmo servidor físico, a falha desse host interrompe o monitoramento e a visibilidade de segurança simultaneamente. Registrar essa dependência, prever monitoramento externo simples e estudar redundância em etapa posterior.

### 2.2 Desenho lógico

- Equipamentos de rede, nobreaks, impressoras, câmeras e telefones -> SNMPv3/ICMP/APIs -> **Zabbix**.
- Servidores Linux e Windows -> Zabbix Agent/Agent 2, SNMP, API ou verificações remotas adequadas -> **Zabbix**.
- Controladores de domínio, servidores críticos, firewall e eventos do Bitdefender -> fontes de logs e coletores -> **Wazuh**.
- Estações Windows -> **NightOwl + Bitdefender**; para segurança, avaliar coleta seletiva de eventos via **Windows Event Forwarding (WEF)** ou integração oficial do Bitdefender antes de instalar mais um agente em todas as estações.
- NightOwl -> SIEM no futuro: enviar eventos administrativos auditáveis de acesso remoto, alterações de políticas, grants e jobs, sem conteúdo do terminal, senhas ou tickets.

O SIEM deve receber eventos **diretamente das fontes**, não depender do NightOwl para funcionar.

## 3. Inventário e levantamentos obrigatórios antes de instalar

A próxima execução deve levantar e registrar:

- Servidor físico/host escolhido, CPU, RAM, armazenamento, redundância, hypervisor e política de manutenção.
- Quantidade de servidores Windows e Linux, hypervisors, switches, firewalls, impressoras, câmeras, telefones IP, nobreaks e dispositivos SNMP.
- Localização/site de cada fonte, VLAN/rede, roteamento entre unidades, VPN e eventual necessidade de **Zabbix Proxy** por localidade.
- Protocolos de monitoração disponíveis por família de dispositivos; confirmar suporte a SNMPv3 e, quando aplicável, MIBs e APIs oficiais.
- Número aproximado de métricas, intervalos de polling, crescimento, janelas de histórico e retenção de tendências no Zabbix.
- Fontes de logs do Wazuh, eventos por segundo/dia, regras de retenção e quanto dado precisa ser pesquisável.
- Versão, licença e módulos habilitados do GravityZone (EDR, proteção de rede, isolamento e controles de dados).
- Requisitos internos de LGPD, classificação, acesso, retenção, segregação de funções e resposta a incidentes.
- Recursos e software oficialmente suportados **na data da execução**, sem presumir versões/pacotes a partir deste documento.

Não colocar senhas, tokens, certificados privados, strings de comunidade SNMP ou endereços internos sensíveis neste repositório.

## 4. Dimensionamento inicial — não definitivo

Não reservar recursos de produção com base apenas em número de hosts. A necessidade depende sobretudo de **itens monitorados, frequência, alertas indexados e retenção**.

Como referência para discussão e laboratório:

| VM | Ponto de partida para piloto (ajustar após levantamento) | Maior fator de crescimento |
| --- | --- | --- |
| Zabbix | Cerca de 4 vCPU e 8 GiB RAM, com disco dedicado e capacidade a definir | Itens/segundo, histórico e banco de dados |
| Wazuh all-in-one | Planejar provisoriamente 8–12 vCPU e 24–32 GiB RAM, com volume de dados separado; validar na documentação atual | Indexer, eventos/segundo, retenção e pesquisa |
| WEF/collector Windows (se adotado) | Dimensionar separadamente conforme número de eventos e estações | Taxa de eventos, filtros e filas |

Esses valores **não são requisitos mínimos certificados nem aprovação de compra**. Confirmar o dimensionamento nas calculadoras/tabelas oficiais, executar carga piloto e registrar folga de armazenamento. Para Wazuh, decidir all-in-one versus distribuído antes de adquirir/particionar recursos.

Prever alertas de capacidade do próprio monitoramento, backups e restauração verificada.

## 5. Zabbix — escopo e implantação por fases

### Fase Z0 — Projeto técnico

- Fechar topologia, FQDNs internos, DNS, TLS, firewall e autenticação administrativa.
- Selecionar versão estável/LTS suportada e banco de dados compatível.
- Preparar VM, sistema operacional suportado, sincronização de horário, backup e atualização.
- Definir grupos, templates, tags e padrão de nomes por localidade/tipo/criticidade.
- Definir usuários de serviço de menor privilégio; para SNMP, preferir **SNMPv3** com credenciais individualizadas.

### Fase Z1 — Piloto de infraestrutura

- Integrar primeiro um servidor Linux, um servidor Windows e um hypervisor de laboratório/autorizado.
- Adicionar um switch, um nobreak e uma impressora por protocolo suportado.
- Validar métricas reais, polling, disponibilidade, thresholds, incidentes simulados e resolução.
- Para câmeras e telefones IP, começar por ping/SNMP ou API de estado quando disponíveis; evitar sondagens que perturbem dispositivos sensíveis.
- Não instalar agente Zabbix em notebooks/desktops Windows.

### Fase Z2 — Expansão controlada

- Servidores de aplicação, banco, arquivos, virtualização, storage, links e equipamentos de rede.
- Acompanhar DNS/DHCP/AD, certificados, serviços críticos, capacidade e falhas de backup.
- Implantar proxies por unidade somente se latência, isolamento de rede ou continuidade justificarem.
- Configurar severidade, deduplicação, manutenção planejada e escalonamento por equipe.

**Critério de aceite Zabbix:** os equipamentos esperados aparecem com coleta íntegra; alertas críticos chegam ao responsável; não há avalanche de falsos positivos; dashboards e relatórios básicos funcionam; backup/restauração foram testados.

## 6. Wazuh — escopo e implantação por fases

### Fase W0 — Projeto de logs

- Selecionar arquitetura (all-in-one ou distribuída), indexação, retenção, criptografia e RBAC.
- Definir casos de uso: logons anormais, contas privilegiadas, falhas em massa, alterações de grupos AD, criação/alteração de serviços, integridade de arquivos críticos e eventos Bitdefender.
- Evitar ingestão indiscriminada: estabelecer filtros, qualidade de dados, custo de armazenamento e política de descarte.
- Preservar timestamp, hostname original, SID/GUID/IDs de origem e identificadores imutáveis para correlação; não confiar apenas em hostname.

### Fase W1 — Fontes críticas

- Conectar controladores de domínio e servidores Windows/Linux críticos por métodos suportados.
- Configurar coleta de eventos Windows e logs de autenticação, com controle explícito do volume.
- Avaliar Wazuh Agent **nos servidores**, quando necessário para FIM, análise local e telemetria.
- Avaliar integração oficial do GravityZone por API/event push/SIEM, respeitando os módulos efetivamente licenciados.
- Ingerir eventos do firewall, VPN e outras fontes de segurança prioritárias.

### Fase W2 — Estações sem instalação generalizada do Wazuh Agent

- Avaliar **Windows Event Forwarding (WEF)** com coletor Windows e assinaturas de eventos selecionados por GPO.
- Projetar e homologar WEF/WinRM como infraestrutura própria; não reutilizar automaticamente os listeners ou as políticas de recuperação WinRM do NightOwl.
- Documentar limitações da estratégia sem agente Wazuh nas estações: visibilidade local, FIM, algumas detecções e Active Response podem ficar indisponíveis.
- Só decidir por agente Wazuh em estações se os casos de uso e o Bitdefender mostrarem lacuna concreta.

### Fase W3 — Detecção de movimentação lateral e resposta

Priorizar detecções de:

- Autenticações RDP/SMB/WinRM anormais entre estações e servidores.
- Tentativas de uso de contas administrativas em múltiplos dispositivos.
- Aumento incomum de falhas de autenticação e mudanças em grupos privilegiados.
- Criação de serviços/tarefas remotas e comportamento de administração inesperado.
- Eventos relevantes do Bitdefender e do firewall associados a identidades e hosts.

**Detectar não é bloquear.** A prevenção principal deve combinar segmentação, regras de firewall, hardening do AD, privilégios mínimos, MFA onde possível e capacidades de bloqueio/isolamento do Bitdefender. Wazuh Active Response só após teste em laboratório, allowlists de ativos críticos, aprovação, rollback e critérios para falso positivo. Não habilitar bloqueios automáticos de contas/IPs em produção neste projeto inicial.

**Critério de aceite Wazuh:** fontes críticas conectadas, ingestão verificável, regras de detecção benignamente testadas, falsos positivos avaliados, retenção e busca adequadas, acesso restrito e um runbook de investigação executável.

## 7. DLP — avaliação posterior, não implantação nesta fase

O objetivo futuro é reduzir saída indevida de documentos e dados sensíveis por USB, nuvens pessoais, e-mail, uploads e outras vias.

- Primeiro mapear tipos de dados, classificação, armazenamento, destinos legítimos e responsáveis.
- Verificar o que a licença atual do GravityZone já cobre e quais recursos exigem módulos adicionais.
- Considerar soluções de DLP especializadas e, se pertinente, Microsoft Purview, sem decidir licenciamento agora.
- Distinguir **SIEM/FIM** de **DLP**: Wazuh pode registrar/detectar alterações ou atividades suspeitas, mas não deve ser tratado como substituto de uma plataforma DLP completa.
- Fazer política de auditoria antes de bloqueio, com exceções e testes junto às áreas de negócio.

## 8. Segurança, governança e operações

- Interfaces web apenas em rede de gestão/VPN, com HTTPS, identidade forte e permissões mínimas.
- Certificados, chaves, secrets e comunidades SNMP mantidos fora do Git e com rotação documentada.
- Firewall de menor privilégio por origem/destino e serviço; liberar apenas portas comprovadamente necessárias.
- Separar credenciais de coleta de credenciais administrativas; proibir contas excessivamente privilegiadas em scripts de monitoração.
- Proteger dados de eventos, auditoria e indexação; definir retenção, backup, acesso e descarte, considerando LGPD.
- Manter horário sincronizado e mapeamento de identidades consistente entre sistemas.
- Definir responsáveis por alertas (triagem, incidente, escalonamento, plantão), e não apenas dashboards.
- Testar restore, atualização, indisponibilidade do host e reentrada em operação.
- Registrar riscos de dependência única de host/storage, capacidade e alert fatigue.

## 9. Matriz de riscos e controles

| Risco | Controle proposto |
| --- | --- |
| Zabbix e Wazuh no mesmo host físico | VMs isoladas, backups externos, monitor externo do host e plano posterior de HA |
| Crescimento de índices e banco | Capacidade medida, retenção aprovada, alertas de disco e expansão planejada |
| Alertas em excesso | Casos de uso e thresholds com responsável; piloto de ruído |
| Bloqueio automático incorreto | Resposta manual no início; teste de falso positivo e rollback |
| Logs com dados pessoais/sensíveis | Minimização, RBAC, criptografia, retenção e trilha de acesso |
| Identidade de host incorreta | Correlacionar IDs imutáveis (GUID/SID/IDs da plataforma), não hostname isolado |
| SNMP inseguro | Preferir SNMPv3, redes de gerência e ACLs específicas |
| WEF e WinRM conflitando com NightOwl | Desenho, GPO e homologação independentes |
| Host de monitoramento indisponível | Backup restaurável, alerta externo e runbook de recuperação |

## 10. Cronograma por marcos (sem datas assumidas)

| Marco | Entrega verificável | Gate |
| --- | --- | --- |
| M0 — Levantamento | Inventário, fontes, redes, capacidade, retenção e licenças | Aprovação de arquitetura e recursos |
| M1 — Infraestrutura | VMs, TLS, DNS, firewall, backup e hardening | Pré-check e restauração |
| M2 — Zabbix piloto | Servidores e amostra de SNMP/ICMP monitorados | Alertas e coleta validados |
| M3 — Zabbix produção | Servidores e equipamentos aprovados em grupos/templates | Responsáveis e métricas estáveis |
| M4 — Wazuh piloto | AD, servidores e Bitdefender/segurança, conforme viabilidade | Detecções, busca e retenção validadas |
| M5 — Wazuh expandido | WEF seletivo e outras fontes aprovadas | Sem ruído/custo de dados descontrolado |
| M6 — Segurança avançada | Detecção lateral, hardening e avaliação DLP | Aprovação de segurança e resposta |
| M7 — Conectores NightOwl | Integrações opcionais e somente leitura inicialmente | **Projeto futuro separado** |

## 11. Itens explicitamente fora do escopo inicial

- Instalar agente Zabbix nas estações Windows.
- Substituir NightOwl, Bitdefender ou Active Directory.
- Desenvolver NightOwl Agent para Linux ou um agente Server novo neste projeto.
- Implantar bloqueios automáticos do SIEM em estações/servidores sem homologação.
- Contratar ou ativar DLP antes de análise de dados e licenças.
- Criar conectores NightOwl agora.
- Alterar produção do NightOwl, suas flags, grants, APIs, banco ou agentes.
- Distribuir GPOs ou instalar agentes sem autorização específica.

## 12. Primeira tarefa para o próximo chat/execução

**Contexto para transferência:** a ControlSul quer iniciar projeto independente de monitoramento de infraestrutura e SIEM. NightOwl fica responsável por estações Windows e continuará sendo desenvolvido em outra conversa. Zabbix cobrirá servidores, hypervisors e dispositivos que forneçam SNMP/ICMP/API; Wazuh começará por AD, servidores e segurança, com Bitdefender mantido como proteção EDR. Não instalar Zabbix Agent nas estações. Evitar agentes adicionais nas estações sem justificativa; avaliar WEF. DLP e conectores NightOwl são fases posteriores.

**Solicitação inicial para a próxima execução:**
1. Confirmar o servidor/hypervisor destinado ao projeto e os recursos disponíveis.
2. Fazer inventário de ativos e protocolos por localidade, sem solicitar credenciais nesta fase.
3. Propor topologia de VMs, dimensionamento, rede, DNS/TLS, armazenamento e backups.
4. Validar versões estáveis suportadas e pré-requisitos oficiais vigentes.
5. Construir plano de implantação e testes Zabbix antes da expansão Wazuh.
6. Submeter plano GO/NO-GO; **não instalar nada nem alterar produção até autorização explícita**.

## 13. Referências oficiais para revalidar na execução

- Zabbix — requisitos e sizing: https://www.zabbix.com/documentation/current/en/manual/installation/requirements
- Zabbix — documentação: https://www.zabbix.com/documentation/current/
- Wazuh — instalação e arquitetura: https://documentation.wazuh.com/current/installation-guide/index.html
- Wazuh — quickstart e sizing: https://documentation.wazuh.com/current/quickstart.html
- Wazuh — Wazuh Indexer: https://documentation.wazuh.com/current/installation-guide/wazuh-indexer/index.html
- Microsoft — Windows Event Forwarding: https://learn.microsoft.com/en-us/windows/security/operating-system-security/device-management/use-windows-event-forwarding-to-assist-in-intrusion-detection

---

**Princípio orientador:** NightOwl administra estações, Zabbix monitora infraestrutura, Wazuh investiga eventos de segurança, Bitdefender protege endpoints e AD mantém identidades. A implantação deve ser modular, auditável e independente dos demais projetos.
