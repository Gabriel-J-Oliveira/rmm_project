(function () {
    "use strict";

    /**
     * @typedef {"success"|"info"|"warning"|"critical"|"security"|"muted"} Severity
     * @typedef {"online"|"offline"|"unknown"|"critical"} EndpointStatus
     * @typedef {"open"|"acknowledged"|"muted"|"resolved"} AlertStatus
     * @typedef {"agent"|"system"|"alerts"|"jobs"|"security"|"inventory"|"maintenance"} EventCategory
     * @typedef {"queued"|"sent"|"running"|"completed"|"failed"|"preview"|"scheduled"} JobStatus
     * @typedef {"low"|"normal"|"high"|"critical"} TaskPriority
     * @typedef {"open"|"scheduled"|"in_progress"|"waiting"|"done"|"cancelled"} TaskStatus
     * @typedef {"support"|"maintenance"|"security"|"inventory"|"onboarding"|"offboarding"|"change"} TaskCategory
     *
     * @typedef {Object} EndpointDisk
     * @property {string} name
     * @property {number} usedPercent
     * @property {number} totalGb
     * @property {number} freeGb
     * @property {Severity} severity
     *
     * @typedef {Object} EndpointSoftware
     * @property {string} name
     * @property {string} category
     * @property {string} risk
     * @property {string} version
     * @property {string} publisher
     * @property {string} installedAt
     *
     * @typedef {Object} EndpointSecurity
     * @property {string} antivirus
     * @property {"ok"|"attention"|"critical"|"unknown"} status
     * @property {string} signature
     * @property {string} firewall
     * @property {string} bitlocker
     * @property {string[]} remoteTools
     *
     * @typedef {Object} EndpointAgent
     * @property {string} version
     * @property {string} recommendedVersion
     * @property {"current"|"outdated"|"unknown"} state
     * @property {string} mode
     * @property {string} path
     * @property {string} source
     * @property {string} runtime
     * @property {string} lastRun
     * @property {string} nextHeartbeat
     * @property {string} lastError
     *
     * @typedef {Object} EndpointSummary
     * @property {string} id
     * @property {string} hostname
     * @property {EndpointStatus} status
     * @property {string} ip
     * @property {string} user
     * @property {string} sector
     * @property {string} domain
     * @property {string} os
     * @property {string} type
     * @property {number} healthScore
     * @property {string} attention
     * @property {EndpointAgent} agent
     *
     * @typedef {EndpointSummary & {
     *   disks: EndpointDisk[],
     *   software: EndpointSoftware[],
     *   security: EndpointSecurity,
     *   tickets: TicketSummary[],
     *   alerts: AlertItem[],
     *   events: EventItem[],
     *   jobs: JobItem[]
     * }} EndpointDetail
     *
     * @typedef {Object} AlertItem
     * @property {string} id
     * @property {string} endpointId
     * @property {string} endpoint
     * @property {string} title
     * @property {string} description
     * @property {Severity} severity
     * @property {AlertStatus} status
     * @property {string} type
     * @property {string} owner
     * @property {string} age
     * @property {string=} ticket
     *
     * @typedef {Object} EventItem
     * @property {string} id
     * @property {string} title
     * @property {string} eventType
     * @property {Severity} severity
     * @property {EventCategory} category
     * @property {string} source
     * @property {string=} endpointId
     * @property {string=} endpoint
     * @property {string} actor
     * @property {string} description
     * @property {string} timestamp
     *
     * @typedef {Object} JobItem
     * @property {string} id
     * @property {string} endpointId
     * @property {string} endpoint
     * @property {string} name
     * @property {JobStatus} status
     * @property {string} command
     * @property {string} createdAt
     *
     * @typedef {Object} TicketSummary
     * @property {string} id
     * @property {string} number
     * @property {string} title
     * @property {string} status
     * @property {string} priority
     *
     * @typedef {Object} TaskChecklistItem
     * @property {string} id
     * @property {string} title
     * @property {boolean} done
     *
     * @typedef {Object} OperationalTask
     * @property {string} id
     * @property {string} title
     * @property {string} description
     * @property {TaskStatus} status
     * @property {TaskPriority} priority
     * @property {TaskCategory} category
     * @property {string} startAt
     * @property {string} dueAt
     * @property {string} responsible
     * @property {TaskChecklistItem[]} checklist
     * @property {string=} linkedTicketId
     * @property {string=} linkedEndpointId
     * @property {string=} linkedUser
     * @property {string=} location
     * @property {string[]=} jobIds
     *
     * @typedef {Object} TaskTemplate
     * @property {string} id
     * @property {string} name
     * @property {TaskCategory} category
     * @property {string} description
     * @property {TaskChecklistItem[]} checklist
     *
     * @typedef {Object} SoftwareInventoryItem
     * @property {string} id
     * @property {string} name
     * @property {string} publisher
     * @property {string} category
     * @property {string} risk
     * @property {"approved"|"sensitive"|"forbidden"|"unknown"|"evaluating"|"required"} status
     * @property {number} endpointCount
     * @property {string[]} versions
     * @property {string} latestSeenAt
     * @property {string[]} endpointIds
     *
     * @typedef {Object} SoftwareCatalogItem
     * @property {string} id
     * @property {string} name
     * @property {string} publisher
     * @property {string} category
     * @property {string} approvedVersion
     * @property {string} packageId
     * @property {"draft"|"testing"|"approved"|"retired"} status
     *
     * @typedef {Object} SoftwarePackage
     * @property {string} id
     * @property {string} name
     * @property {string} softwareId
     * @property {string} version
     * @property {string} publisher
     * @property {string} file
     * @property {string} fileType
     * @property {string} architecture
     * @property {string} logicalPath
     * @property {string} sha256
     * @property {string} installCommand
     * @property {string} uninstallCommand
     * @property {string} detectionMethod
     * @property {string} detectionRule
     * @property {number} timeoutMinutes
     * @property {boolean} requiresReboot
     * @property {"system"|"user"} executionContext
     * @property {"draft"|"testing"|"approved"|"retired"} status
     *
     * @typedef {Object} SoftwareDeployment
     * @property {string} id
     * @property {string} softwareId
     * @property {string} packageId
     * @property {string} targetType
     * @property {"queued"|"running"|"completed"|"failed"|"cancelled"} status
     *
     * @typedef {Object} SoftwareRule
     * @property {string} id
     * @property {string} name
     * @property {string} condition
     * @property {Severity} severity
     * @property {boolean} active
     *
     * @typedef {Object} SoftwareUpdateDrift
     * @property {string} id
     * @property {string} softwareId
     * @property {string} approvedVersion
     * @property {string[]} detectedVersions
     * @property {number} outdatedEndpoints
     */

    var apiDelay = 180;
    var localEventsKey = "nightowl.mock.api.events";
    var localJobsKey = "nightowl.mock.api.jobs";
    var localTasksKey = "nightowl.mock.api.operational_tasks";
    var localAlertStateKey = "nightowl.mock.api.alert_state";
    var localGeneratedAlertsKey = "nightowl.mock.api.generated_alerts";
    var localTicketStateKey = "nightowl.mock.api.tickets";
    var localSoftwareInventoryStateKey = "nightowl.mock.api.software_inventory_state";
    var localSoftwareCatalogKey = "nightowl.mock.api.software_catalog";
    var localSoftwarePackagesKey = "nightowl.mock.api.software_packages";
    var localSoftwareDeploymentsKey = "nightowl.mock.api.software_deployments";
    var localSoftwareRulesKey = "nightowl.mock.api.software_rules";

    var endpoints = [
        {
            id: "00000000-0000-4000-8000-000000000101",
            hostname: "FIN-012",
            status: "online",
            ip: "192.168.104.42",
            user: "Mariana Souza",
            sector: "Financeiro",
            domain: "CONTROL",
            os: "Windows 11 Pro 23H2",
            type: "workstation",
            healthScore: 46,
            attention: "Defender critico",
            agent: {
                version: "1.4.2",
                recommendedVersion: "1.4.2",
                state: "current",
                mode: "PowerShell agendado",
                path: "C:\\RMM",
                source: "\\\\192.168.104.120\\controlsul\\Comum\\_Agents",
                runtime: "PowerShell 5.1",
                lastRun: "ha 3 min",
                nextHeartbeat: "~5 min",
                lastError: ""
            }
        },
        {
            id: "00000000-0000-4000-8000-000000000105",
            hostname: "SRV-ERP-01",
            status: "online",
            ip: "192.168.104.10",
            user: "svc-erp",
            sector: "Infraestrutura",
            domain: "CONTROL",
            os: "Windows Server 2022",
            type: "server",
            healthScore: 58,
            attention: "Disco 93%",
            agent: {
                version: "1.4.2",
                recommendedVersion: "1.4.2",
                state: "current",
                mode: "PowerShell agendado",
                path: "C:\\RMM",
                source: "\\\\192.168.104.120\\controlsul\\Comum\\_Agents",
                runtime: "PowerShell 5.1",
                lastRun: "ha 2 min",
                nextHeartbeat: "~5 min",
                lastError: ""
            }
        },
        {
            id: "00000000-0000-4000-8000-000000000102",
            hostname: "JUR-PRINT-01",
            status: "offline",
            ip: "192.168.104.66",
            user: "juridico",
            sector: "Juridico",
            domain: "CONTROL",
            os: "Windows Server 2019",
            type: "server",
            healthScore: 24,
            attention: "Offline ha 3h",
            agent: {
                version: "1.3.9",
                recommendedVersion: "1.4.2",
                state: "outdated",
                mode: "PowerShell agendado",
                path: "C:\\RMM",
                source: "\\\\192.168.104.120\\controlsul\\Comum\\_Agents",
                runtime: "PowerShell 5.1",
                lastRun: "ha 3h",
                nextHeartbeat: "atrasado",
                lastError: "Heartbeat nao recebido"
            }
        },
        {
            id: "00000000-0000-4000-8000-000000000104",
            hostname: "DIR-NB-03",
            status: "online",
            ip: "192.168.104.88",
            user: "Claudia Ferraz",
            sector: "Diretoria",
            domain: "CONTROL",
            os: "Windows 11 Pro 24H2",
            type: "notebook",
            healthScore: 71,
            attention: "AnyDesk detectado",
            agent: {
                version: "1.4.1",
                recommendedVersion: "1.4.2",
                state: "outdated",
                mode: "PowerShell agendado",
                path: "C:\\RMM",
                source: "\\\\192.168.104.120\\controlsul\\Comum\\_Agents",
                runtime: "PowerShell 5.1",
                lastRun: "ha 14 min",
                nextHeartbeat: "~5 min",
                lastError: ""
            }
        },
        {
            id: "00000000-0000-4000-8000-000000000103",
            hostname: "REC-004",
            status: "online",
            ip: "192.168.104.23",
            user: "recepcao",
            sector: "Recepcao",
            domain: "CONTROL",
            os: "Windows 10 Pro 22H2",
            type: "workstation",
            healthScore: 82,
            attention: "Disco 82%",
            agent: {
                version: "1.4.2",
                recommendedVersion: "1.4.2",
                state: "current",
                mode: "PowerShell agendado",
                path: "C:\\RMM",
                source: "\\\\192.168.104.120\\controlsul\\Comum\\_Agents",
                runtime: "PowerShell 5.1",
                lastRun: "ha 9 min",
                nextHeartbeat: "~5 min",
                lastError: ""
            }
        },
        {
            id: "00000000-0000-4000-8000-000000000106",
            hostname: "COM-017",
            status: "unknown",
            ip: "192.168.104.77",
            user: "Daniel Ribeiro",
            sector: "Comercial",
            domain: "CONTROL",
            os: "Windows 11 Pro 23H2",
            type: "workstation",
            healthScore: 64,
            attention: "Inventario vencido",
            agent: {
                version: "",
                recommendedVersion: "1.4.2",
                state: "unknown",
                mode: "PowerShell agendado",
                path: "C:\\RMM",
                source: "\\\\192.168.104.120\\controlsul\\Comum\\_Agents",
                runtime: "PowerShell 5.1",
                lastRun: "ha 54 min",
                nextHeartbeat: "incerto",
                lastError: ""
            }
        }
    ];

    var disksByEndpoint = {
        "FIN-012": [{ name: "C:", usedPercent: 56, totalGb: 512, freeGb: 225, severity: "success" }],
        "SRV-ERP-01": [{ name: "C:", usedPercent: 93, totalGb: 512, freeGb: 36, severity: "critical" }, { name: "D:", usedPercent: 61, totalGb: 1024, freeGb: 399, severity: "success" }],
        "JUR-PRINT-01": [{ name: "C:", usedPercent: 78, totalGb: 256, freeGb: 56, severity: "warning" }],
        "DIR-NB-03": [{ name: "C:", usedPercent: 64, totalGb: 512, freeGb: 184, severity: "success" }],
        "REC-004": [{ name: "C:", usedPercent: 82, totalGb: 256, freeGb: 46, severity: "warning" }],
        "COM-017": [{ name: "C:", usedPercent: 68, totalGb: 512, freeGb: 164, severity: "info" }]
    };

    var softwareByEndpoint = {
        "FIN-012": [
            { name: "Microsoft 365 Apps", category: "microsoft", risk: "low", version: "2407", publisher: "Microsoft Corporation", installedAt: "2026-07-08T10:15:00" },
            { name: "Windows Defender", category: "security", risk: "medium", version: "4.18.24060", publisher: "Microsoft", installedAt: "2026-07-08T10:15:00" }
        ],
        "SRV-ERP-01": [
            { name: "SQL Server Runtime", category: "admin", risk: "medium", version: "16.0", publisher: "Microsoft Corporation", installedAt: "2026-07-08T10:11:00" },
            { name: "Bitdefender Endpoint Security Tools", category: "security", risk: "low", version: "7.9.12", publisher: "Bitdefender", installedAt: "2026-07-08T10:11:00" }
        ],
        "DIR-NB-03": [
            { name: "AnyDesk", category: "remote", risk: "high", version: "8.0.9", publisher: "AnyDesk Software GmbH", installedAt: "2026-07-08T09:46:00" },
            { name: "Microsoft 365 Apps", category: "microsoft", risk: "low", version: "2407", publisher: "Microsoft Corporation", installedAt: "2026-07-08T09:46:00" }
        ],
        "REC-004": [
            { name: "Google Chrome", category: "other", risk: "low", version: "126.0.6478", publisher: "Google LLC", installedAt: "2026-07-08T10:08:00" },
            { name: "Bitdefender Endpoint Security Tools", category: "security", risk: "low", version: "7.9.12", publisher: "Bitdefender", installedAt: "2026-07-08T10:08:00" }
        ],
        "JUR-PRINT-01": [],
        "COM-017": []
    };

    var securityByEndpoint = {
        "FIN-012": { antivirus: "Defender/Bitdefender ausente", status: "critical", signature: "-", firewall: "Ativo", bitlocker: "Nao coletado", remoteTools: [] },
        "SRV-ERP-01": { antivirus: "Bitdefender", status: "ok", signature: "2026-07-08 10:11", firewall: "Ativo", bitlocker: "Nao aplicavel", remoteTools: [] },
        "JUR-PRINT-01": { antivirus: "Nao coletado", status: "unknown", signature: "-", firewall: "Nao coletado", bitlocker: "Nao coletado", remoteTools: [] },
        "DIR-NB-03": { antivirus: "Bitdefender", status: "attention", signature: "2026-07-08 09:46", firewall: "Ativo", bitlocker: "Ativo", remoteTools: ["AnyDesk"] },
        "REC-004": { antivirus: "Bitdefender", status: "ok", signature: "2026-07-08 10:08", firewall: "Ativo", bitlocker: "Nao coletado", remoteTools: [] },
        "COM-017": { antivirus: "Desconhecido", status: "unknown", signature: "-", firewall: "Nao coletado", bitlocker: "Nao coletado", remoteTools: [] }
    };

    var softwareCatalog = [
        { id: "SC-001", name: "Google Chrome", publisher: "Google LLC", category: "browser", approvedVersion: "126.0.6478", packageId: "PKG-001", status: "approved", requiresLicense: false, requiresReboot: false, updatedAt: "2026-07-07T15:00:00" },
        { id: "SC-002", name: "Bitdefender Endpoint Security Tools", publisher: "Bitdefender", category: "security", approvedVersion: "7.9.12", packageId: "PKG-002", status: "approved", requiresLicense: true, requiresReboot: false, updatedAt: "2026-07-06T11:20:00" },
        { id: "SC-003", name: "Microsoft 365 Apps", publisher: "Microsoft Corporation", category: "office", approvedVersion: "2407", packageId: "PKG-003", status: "approved", requiresLicense: true, requiresReboot: false, updatedAt: "2026-07-05T09:40:00" },
        { id: "SC-004", name: "AnyDesk", publisher: "AnyDesk Software GmbH", category: "remote", approvedVersion: "8.0.9", packageId: "", status: "retired", requiresLicense: true, requiresReboot: false, updatedAt: "2026-07-04T08:10:00" },
        { id: "SC-005", name: "Advanced IP Scanner", publisher: "Famatech", category: "admin", approvedVersion: "2.5", packageId: "", status: "testing", requiresLicense: false, requiresReboot: false, updatedAt: "2026-07-03T17:30:00" }
    ];

    var softwarePackages = [
        { id: "PKG-001", name: "Google Chrome Enterprise MSI", softwareId: "SC-001", version: "126.0.6478", publisher: "Google LLC", category: "browser", file: "googlechromestandaloneenterprise64.msi", fileType: "MSI", architecture: "x64", sizeMb: 112, sha256: "b5f2d8c8a6f44e2c7b1f2f5f4a9c00112233445566778899aabbccddeeff0011", logicalPath: "/opt/nightowl/packages/google-chrome/126.0.6478/googlechromestandaloneenterprise64.msi", repositoryRoot: "/opt/nightowl/packages/", endpointCachePath: "C:\\ProgramData\\NightOwl\\Packages\\", endpointLogsPath: "C:\\ProgramData\\NightOwl\\Logs\\", installCommand: "msiexec /i googlechromestandaloneenterprise64.msi /qn /norestart", installArguments: "/qn /norestart", uninstallCommand: "msiexec /x {chrome} /qn", uninstallArguments: "/qn", detectionMethod: "registry_key", detectionRule: "HKLM\\Software\\Google\\Chrome", timeoutMinutes: 20, requiresReboot: false, requiresLoggedOff: false, executionContext: "system", status: "approved", uploadedBy: "Gabriel Oliveira", uploadedAt: "2026-07-07T15:00:00", approvedBy: "Gabriel Oliveira", approvedAt: "2026-07-07T15:24:00" },
        { id: "PKG-002", name: "Bitdefender BEST Installer", softwareId: "SC-002", version: "7.9.12", publisher: "Bitdefender", category: "security", file: "best_windows.exe", fileType: "EXE", architecture: "x64", sizeMb: 368, sha256: "a1e8d8c8a6f44e2c7b1f2f5f4a9c00998877665544332211ffeeddccbbaa00", logicalPath: "/opt/nightowl/packages/bitdefender-endpoint-security-tools/7.9.12/best_windows.exe", repositoryRoot: "/opt/nightowl/packages/", endpointCachePath: "C:\\ProgramData\\NightOwl\\Packages\\", endpointLogsPath: "C:\\ProgramData\\NightOwl\\Logs\\", installCommand: "best_windows.exe /quiet /norestart", installArguments: "/quiet /norestart", uninstallCommand: "best_windows.exe /uninstall /quiet", uninstallArguments: "/uninstall /quiet", detectionMethod: "service_exists", detectionRule: "Service:EPIntegrationService", timeoutMinutes: 45, requiresReboot: false, requiresLoggedOff: false, executionContext: "system", status: "approved", uploadedBy: "Gabriel Oliveira", uploadedAt: "2026-07-06T11:20:00", approvedBy: "Gabriel Oliveira", approvedAt: "2026-07-06T12:05:00" },
        { id: "PKG-003", name: "Microsoft 365 Apps ODT", softwareId: "SC-003", version: "2407", publisher: "Microsoft Corporation", category: "office", file: "m365-odt.zip", fileType: "ZIP", architecture: "x64", sizeMb: 42, sha256: "c3f2d8c8a6f44e2c7b1f2f5f4a9c00ffeeddccbbaa00112233445566778899", logicalPath: "/opt/nightowl/packages/microsoft-365-apps/2407/m365-odt.zip", repositoryRoot: "/opt/nightowl/packages/", endpointCachePath: "C:\\ProgramData\\NightOwl\\Packages\\", endpointLogsPath: "C:\\ProgramData\\NightOwl\\Logs\\", installCommand: "setup.exe /configure configuration.xml", installArguments: "/configure configuration.xml", uninstallCommand: "setup.exe /configure uninstall.xml", uninstallArguments: "/configure uninstall.xml", detectionMethod: "software_name_version", detectionRule: "ClickToRun Version >= 2407", timeoutMinutes: 60, requiresReboot: false, requiresLoggedOff: false, executionContext: "system", status: "testing", uploadedBy: "Renan Santos", uploadedAt: "2026-07-05T09:40:00", approvedBy: "", approvedAt: "" }
    ];

    var softwareDeployments = [
        { id: "DEP-001", softwareId: "SC-002", packageId: "PKG-002", software: "Bitdefender Endpoint Security Tools", packageName: "Bitdefender BEST Installer", targetType: "endpoint", targetLabel: "FIN-012", endpointIds: [endpoints[0].id], status: "running", progress: 66, createdBy: "Gabriel Oliveira", createdAt: "2026-07-08T10:15:00", finishedAt: "", failures: 0, jobIds: ["J-001"], stdout: "Instalacao enviada ao agente.", stderr: "" },
        { id: "DEP-002", softwareId: "SC-001", packageId: "PKG-001", software: "Google Chrome", packageName: "Google Chrome Enterprise MSI", targetType: "tag", targetLabel: "Workstations", endpointIds: [endpoints[0].id, endpoints[3].id, endpoints[4].id, endpoints[5].id], status: "completed", progress: 100, createdBy: "Renan Santos", createdAt: "2026-07-07T14:40:00", finishedAt: "2026-07-07T15:12:00", failures: 0, jobIds: ["J-004"], stdout: "4 endpoints atualizados.", stderr: "" }
    ];

    var softwareRules = [
        { id: "SR-001", name: "AnyDesk detectado em endpoint sensivel", condition: "software.name = AnyDesk AND endpoint.sector IN Diretoria, Financeiro", scope: "Diretoria e Financeiro", severity: "security", action: "Criar alerta e mostrar no NOC", active: true, lastRunAt: "2026-07-08T09:48:00", occurrences: 1 },
        { id: "SR-002", name: "Bitdefender ausente", condition: "software.security.bitdefender = absent", scope: "Todos os endpoints", severity: "critical", action: "Criar alerta e tarefa", active: true, lastRunAt: "2026-07-08T10:06:00", occurrences: 1 },
        { id: "SR-003", name: "Chrome abaixo da versao aprovada", condition: "software.name = Chrome AND version < 126.0.6478", scope: "Workstations", severity: "warning", action: "Registrar evento", active: true, lastRunAt: "2026-07-08T08:00:00", occurrences: 2 },
        { id: "SR-004", name: "Software sem fabricante", condition: "publisher is empty", scope: "Todos", severity: "info", action: "Mostrar no inventario", active: false, lastRunAt: "2026-07-06T08:00:00", occurrences: 0 }
    ];

    var inventoryByEndpoint = {
        "FIN-012": { osVersion: "23H2", build: "22631.3880", architecture: "x64", manufacturer: "Dell Inc.", model: "OptiPlex 7010", serial: "FIN012-CTRL", cpu: "Intel Core i5-13500", memoryGb: 16, bios: "UEFI 1.14.0", macs: ["00-15-5D-10-2A-42"], uptime: "2d 4h", lastFullInventory: "ha 18 min" },
        "SRV-ERP-01": { osVersion: "2022 Datacenter", build: "20348.2527", architecture: "x64", manufacturer: "HPE", model: "ProLiant DL360", serial: "SRVERP01", cpu: "Intel Xeon Silver", memoryGb: 64, bios: "UEFI 2.74", macs: ["00-50-56-A1-10-10"], uptime: "18d 7h", lastFullInventory: "ha 11 min" },
        "JUR-PRINT-01": { osVersion: "2019 Standard", build: "17763.5936", architecture: "x64", manufacturer: "HP", model: "Print Server VM", serial: "JURPRINT01", cpu: "2 vCPU", memoryGb: 8, bios: "Legacy", macs: ["00-50-56-A1-10-66"], uptime: "nao coletado", lastFullInventory: "ha 3h" },
        "DIR-NB-03": { osVersion: "24H2", build: "26100.1150", architecture: "x64", manufacturer: "Lenovo", model: "ThinkPad X1 Carbon", serial: "DIRNB03", cpu: "Intel Core Ultra 7", memoryGb: 32, bios: "UEFI 1.32", macs: ["A0-29-42-44-88-03"], uptime: "8h 12min", lastFullInventory: "ha 26 min" },
        "REC-004": { osVersion: "22H2", build: "19045.4651", architecture: "x64", manufacturer: "Dell Inc.", model: "Vostro 3710", serial: "REC004-CTRL", cpu: "Intel Core i3-12100", memoryGb: 8, bios: "UEFI 1.9.2", macs: ["00-15-5D-10-2A-23"], uptime: "4d 1h", lastFullInventory: "ha 42 min" },
        "COM-017": { osVersion: "23H2", build: "22631.3737", architecture: "x64", manufacturer: "Acer", model: "Veriton", serial: "COM017-CTRL", cpu: "Intel Core i5-10400", memoryGb: 16, bios: "UEFI 1.03", macs: ["00-15-5D-10-2A-77"], uptime: "nao confiavel", lastFullInventory: "ha 26h" }
    };

    var patchByEndpoint = {
        "FIN-012": { compliance: 82, lastScan: "ha 6h", criticalPending: 1, importantPending: 4, rebootPending: false, pending: [{ kb: "KB5060842", title: "Windows cumulative update", severity: "critical" }, { kb: "KB5058499", title: ".NET security update", severity: "warning" }], history: [{ title: "KB5056578 instalado", status: "completed", when: "ha 3d" }] },
        "SRV-ERP-01": { compliance: 71, lastScan: "ha 2h", criticalPending: 2, importantPending: 6, rebootPending: true, pending: [{ kb: "KB5060520", title: "Windows Server security rollup", severity: "critical" }, { kb: "KB5059991", title: "SQL runtime update", severity: "warning" }], history: [{ title: "Scan WSUS concluido", status: "completed", when: "ha 2h" }] },
        "DIR-NB-03": { compliance: 91, lastScan: "ha 1d", criticalPending: 0, importantPending: 2, rebootPending: false, pending: [{ kb: "KB5058499", title: ".NET quality update", severity: "info" }], history: [{ title: "Drivers Lenovo verificados", status: "completed", when: "ha 1d" }] }
    };

    var localAdminsByEndpoint = {
        "FIN-012": ["CONTROL\\Domain Admins", "FIN-012\\Administrador"],
        "SRV-ERP-01": ["CONTROL\\Infra Admins"],
        "DIR-NB-03": ["CONTROL\\Domain Admins", "DIR-NB-03\\Claudia.Ferraz"]
    };

    var policyViolationsByEndpoint = {
        "DIR-NB-03": [{ policy: "Acesso remoto nao homologado", item: "AnyDesk", severity: "security" }],
        "FIN-012": [{ policy: "Endpoint financeiro com AV obrigatorio", item: "Bitdefender ausente", severity: "critical" }]
    };

    var alerts = [
        { id: "A-1048", endpointId: endpoints[0].id, endpoint: "FIN-012", title: "Bitdefender ausente em FIN-012", description: "O agente detectou ausencia do Bitdefender na maquina financeira FIN-012.", severity: "critical", status: "open", type: "security_antivirus", owner: "Nao atribuido", age: "ha 6 min", ticket: "" },
        { id: "A-1042", endpointId: endpoints[1].id, endpoint: "SRV-ERP-01", title: "Disco C: acima de 90%", description: "Volume principal do servidor ERP esta com pouco espaco livre.", severity: "warning", status: "open", type: "disk_low", owner: "Gabriel Oliveira", age: "ha 18 min", ticket: "#1042" },
        { id: "A-1039", endpointId: endpoints[2].id, endpoint: "JUR-PRINT-01", title: "Endpoint offline ha mais de 3h", description: "Servidor de impressao do Juridico parou de comunicar.", severity: "critical", status: "open", type: "endpoint_offline", owner: "Nao atribuido", age: "ha 3h", ticket: "" },
        { id: "A-1037", endpointId: endpoints[3].id, endpoint: "DIR-NB-03", title: "AnyDesk detectado em notebook da diretoria", description: "Software de acesso remoto identificado em endpoint sensivel.", severity: "security", status: "acknowledged", type: "remote_access_software", owner: "Renan", age: "ha 27 min", ticket: "" },
        { id: "A-1032", endpointId: endpoints[5].id, endpoint: "COM-017", title: "Inventario desatualizado", description: "Endpoint sem snapshot completo nas ultimas 24h.", severity: "info", status: "muted", type: "stale_inventory", owner: "Nao atribuido", age: "ha 54 min", ticket: "" }
    ];

    var tickets = [
        { id: "T-1042", number: "#1042", title: "Disco do servidor acima de 90%", status: "Em atendimento", priority: "Alta", endpointId: endpoints[1].id },
        { id: "T-1048", number: "#1048", title: "Bitdefender ausente em FIN-012", status: "Novo", priority: "Critica", endpointId: endpoints[0].id }
    ];

    var operationalTasks = [
        {
            id: "OT-001",
            title: "Validar Bitdefender no Financeiro",
            description: "Confirmar ausencia do AV em FIN-012 e abrir plano de correcao.",
            status: "in_progress",
            priority: "critical",
            category: "security",
            startAt: new Date(Date.now() - 2 * 3600000).toISOString(),
            dueAt: new Date(Date.now() + 2 * 3600000).toISOString(),
            responsible: "Gabriel Oliveira",
            checklist: [
                { id: "c1", title: "Conferir alerta no endpoint", done: true },
                { id: "c2", title: "Executar verificacao Defender", done: true },
                { id: "c3", title: "Acionar instalacao ou chamado", done: false }
            ],
            linkedTicketId: "T-1048",
            linkedEndpointId: endpoints[0].id,
            linkedUser: "Mariana Souza",
            location: "Financeiro",
            jobIds: ["J-001"],
            createdAt: new Date(Date.now() - 3 * 3600000).toISOString(),
            updatedAt: new Date(Date.now() - 26 * 60000).toISOString(),
            timeline: [{ at: new Date(Date.now() - 26 * 60000).toISOString(), actor: "Gabriel Oliveira", text: "Verificacao remota enfileirada." }]
        },
        {
            id: "OT-002",
            title: "Liberar espaco no SRV-ERP-01",
            description: "Executar limpeza orientada e validar crescimento do volume C:.",
            status: "scheduled",
            priority: "high",
            category: "maintenance",
            startAt: new Date(Date.now() + 4 * 3600000).toISOString(),
            dueAt: new Date(Date.now() + 26 * 3600000).toISOString(),
            responsible: "Renan Santos",
            checklist: [
                { id: "c1", title: "Coletar logs de disco", done: false },
                { id: "c2", title: "Executar limpeza temporaria", done: false },
                { id: "c3", title: "Criar chamado se espaco livre ficar abaixo de 15%", done: false }
            ],
            linkedTicketId: "T-1042",
            linkedEndpointId: endpoints[1].id,
            linkedUser: "svc-erp",
            location: "Datacenter",
            jobIds: ["J-002"],
            createdAt: new Date(Date.now() - 1 * 3600000).toISOString(),
            updatedAt: new Date(Date.now() - 1 * 3600000).toISOString(),
            timeline: [{ at: new Date(Date.now() - 1 * 3600000).toISOString(), actor: "Renan Santos", text: "Tarefa agendada para janela de manutencao." }]
        },
        {
            id: "OT-003",
            title: "Offboarding usuario Comercial",
            description: "Remover acessos, recolher notebook e validar softwares sensiveis.",
            status: "open",
            priority: "normal",
            category: "offboarding",
            startAt: new Date(Date.now() + 86400000).toISOString(),
            dueAt: new Date(Date.now() + 2 * 86400000).toISOString(),
            responsible: "Equipe TI",
            checklist: [
                { id: "c1", title: "Bloquear conta AD", done: false },
                { id: "c2", title: "Recolher equipamento", done: false },
                { id: "c3", title: "Forcar inventario final", done: false }
            ],
            linkedTicketId: "",
            linkedEndpointId: endpoints[5].id,
            linkedUser: "Daniel Ribeiro",
            location: "Comercial",
            jobIds: ["J-005"],
            createdAt: new Date(Date.now() - 6 * 3600000).toISOString(),
            updatedAt: new Date(Date.now() - 6 * 3600000).toISOString(),
            timeline: [{ at: new Date(Date.now() - 6 * 3600000).toISOString(), actor: "Sistema", text: "Tarefa criada a partir de rotina de desligamento." }]
        },
        {
            id: "OT-004",
            title: "Revisao de backup do ERP",
            description: "Conferir ultimo job de backup, retencao e restauracao pontual.",
            status: "done",
            priority: "high",
            category: "maintenance",
            startAt: new Date(Date.now() - 2 * 86400000).toISOString(),
            dueAt: new Date(Date.now() - 86400000).toISOString(),
            responsible: "Gabriel Oliveira",
            checklist: [
                { id: "c1", title: "Conferir job noturno", done: true },
                { id: "c2", title: "Validar retencao", done: true },
                { id: "c3", title: "Registrar evidencia", done: true }
            ],
            linkedTicketId: "",
            linkedEndpointId: endpoints[1].id,
            linkedUser: "Infraestrutura",
            location: "Datacenter",
            jobIds: [],
            createdAt: new Date(Date.now() - 3 * 86400000).toISOString(),
            updatedAt: new Date(Date.now() - 86400000).toISOString(),
            timeline: [{ at: new Date(Date.now() - 86400000).toISOString(), actor: "Gabriel Oliveira", text: "Checklist concluido." }]
        },
        {
            id: "OT-005",
            title: "Troca de equipamento Diretoria",
            description: "Preparar notebook reserva, migrar perfil e validar acesso remoto homologado.",
            status: "waiting",
            priority: "normal",
            category: "change",
            startAt: new Date(Date.now() - 3600000).toISOString(),
            dueAt: new Date(Date.now() - 20 * 60000).toISOString(),
            responsible: "Equipe TI",
            checklist: [
                { id: "c1", title: "Preparar imagem", done: true },
                { id: "c2", title: "Migrar perfil", done: false },
                { id: "c3", title: "Validar MFA e VPN", done: false }
            ],
            linkedTicketId: "",
            linkedEndpointId: endpoints[3].id,
            linkedUser: "Claudia Ferraz",
            location: "Diretoria",
            jobIds: ["J-003"],
            createdAt: new Date(Date.now() - 5 * 3600000).toISOString(),
            updatedAt: new Date(Date.now() - 42 * 60000).toISOString(),
            timeline: [{ at: new Date(Date.now() - 42 * 60000).toISOString(), actor: "Equipe TI", text: "Aguardando disponibilidade da usuaria." }]
        }
    ];

    function checklistFromTitles(titles) {
        return titles.map(function (title, index) {
            return { id: "c" + (index + 1), title: title, done: false };
        });
    }

    var taskTemplates = [
        {
            id: "TT-001",
            name: "Onboarding de funcionario",
            category: "onboarding",
            description: "Preparacao de conta, equipamento, acessos corporativos e entrega ao novo funcionario.",
            checklist: checklistFromTitles([
                "Receber dados do novo funcionario",
                "Criar usuario no Active Directory",
                "Definir OU correta",
                "Adicionar aos grupos de acesso",
                "Criar/configurar e-mail",
                "Configurar MFA, se aplicavel",
                "Separar equipamento",
                "Entrar equipamento no dominio",
                "Instalar softwares padrao",
                "Configurar Outlook",
                "Configurar impressoras",
                "Configurar monitores/perifericos",
                "Validar acesso aos sistemas necessarios",
                "Registrar patrimonio",
                "Entregar equipamento"
            ])
        },
        {
            id: "TT-002",
            name: "Offboarding de funcionario",
            category: "offboarding",
            description: "Bloqueio de acessos, recolhimento de equipamento e atualizacao de inventario.",
            checklist: checklistFromTitles([
                "Confirmar data de desligamento",
                "Desativar usuario no AD",
                "Revogar sessoes",
                "Remover de grupos criticos",
                "Bloquear e-mail ou converter em caixa compartilhada",
                "Recolher equipamento",
                "Fazer backup de dados necessarios",
                "Remover acessos de sistemas externos",
                "Atualizar inventario"
            ])
        },
        {
            id: "TT-003",
            name: "Troca de equipamento",
            category: "change",
            description: "Substituicao controlada de endpoint com migracao de dados e validacao do usuario.",
            checklist: checklistFromTitles([
                "Separar novo equipamento",
                "Coletar inventario do equipamento antigo",
                "Fazer backup de dados",
                "Entrar novo equipamento no dominio",
                "Instalar softwares necessarios",
                "Migrar perfil/dados",
                "Validar impressoras",
                "Validar e-mail",
                "Atualizar patrimonio",
                "Coletar equipamento antigo"
            ])
        },
        {
            id: "TT-004",
            name: "Manutencao de servidor",
            category: "maintenance",
            description: "Janela tecnica para servidor com comunicacao, backup, execucao e evidencias.",
            checklist: checklistFromTitles([
                "Definir janela de manutencao",
                "Comunicar envolvidos",
                "Validar backup recente",
                "Criar ponto de restauracao/snapshot se aplicavel",
                "Executar manutencao",
                "Validar servicos",
                "Validar conectividade",
                "Registrar evidencias",
                "Encerrar manutencao"
            ])
        },
        {
            id: "TT-005",
            name: "Instalacao de software",
            category: "support",
            description: "Instalacao assistida ou remota com validacao de licenca e resultado.",
            checklist: checklistFromTitles([
                "Validar licenca",
                "Validar instalador",
                "Definir endpoints alvo",
                "Agendar execucao",
                "Executar instalacao",
                "Validar instalacao",
                "Registrar resultado"
            ])
        },
        {
            id: "TT-006",
            name: "Revisao de backup",
            category: "maintenance",
            description: "Conferencia operacional de backup, retencao e evidencia.",
            checklist: checklistFromTitles([
                "Verificar ultimo job de backup",
                "Validar retencao",
                "Validar destino de backup",
                "Executar teste pontual de restauracao, se aplicavel",
                "Registrar evidencia",
                "Registrar resultado"
            ])
        },
        {
            id: "TT-007",
            name: "Atualizacao Windows",
            category: "maintenance",
            description: "Rotina de patching Windows com scan, instalacao, reboot e validacao.",
            checklist: checklistFromTitles([
                "Verificar updates pendentes",
                "Validar janela de manutencao",
                "Executar instalacao",
                "Reiniciar se necessario",
                "Validar retorno do endpoint",
                "Registrar falhas"
            ])
        }
    ];

    var baseEvents = [
        { id: "E-001", title: "Heartbeat recebido", eventType: "agent.heartbeat_received", severity: "success", category: "agent", source: "Agent", endpointId: endpoints[0].id, endpoint: "FIN-012", actor: "Agent", description: "FIN-012 enviou heartbeat e metricas basicas.", timestamp: new Date(Date.now() - 2 * 60000).toISOString() },
        { id: "E-002", title: "Alerta critico criado", eventType: "alert.created", severity: "critical", category: "alerts", source: "System", endpointId: endpoints[0].id, endpoint: "FIN-012", actor: "NightOwl", description: "Bitdefender ausente detectado no FIN-012.", timestamp: new Date(Date.now() - 5 * 60000).toISOString() },
        { id: "E-003", title: "Disco em atencao", eventType: "alert.created", severity: "warning", category: "alerts", source: "System", endpointId: endpoints[1].id, endpoint: "SRV-ERP-01", actor: "NightOwl", description: "Disco C: acima de 90% no servidor ERP.", timestamp: new Date(Date.now() - 18 * 60000).toISOString() },
        { id: "E-004", title: "Endpoint ficou offline", eventType: "endpoint.status_changed", severity: "warning", category: "system", source: "System", endpointId: endpoints[2].id, endpoint: "JUR-PRINT-01", actor: "NightOwl", description: "JUR-PRINT-01 deixou de comunicar com o RMM.", timestamp: new Date(Date.now() - 185 * 60000).toISOString() },
        { id: "E-005", title: "Politica violada", eventType: "software_policy.violation", severity: "security", category: "security", source: "Policy", endpointId: endpoints[3].id, endpoint: "DIR-NB-03", actor: "NightOwl", description: "AnyDesk detectado em endpoint da diretoria.", timestamp: new Date(Date.now() - 27 * 60000).toISOString() }
    ];

    var jobs = [
        {
            id: "J-001",
            endpointId: endpoints[0].id,
            endpoint: "FIN-012",
            type: "force_inventory",
            name: "Coleta de inventario",
            status: "completed",
            command: "nightowl.inventory.collect",
            createdBy: "Gabriel Oliveira",
            createdAt: new Date(Date.now() - 32 * 60000).toISOString(),
            startedAt: new Date(Date.now() - 31 * 60000).toISOString(),
            finishedAt: new Date(Date.now() - 30 * 60000).toISOString(),
            durationMs: 58000,
            result: "Inventario atualizado",
            stdout: "Inventory snapshot collected: software=42 disks=1 security=attention",
            stderr: "",
            exitCode: 0,
            payload: { endpoint: "FIN-012", collect: ["software", "hardware", "security"] },
            timeline: ["queued", "sent", "running", "completed"]
        },
        {
            id: "J-002",
            endpointId: endpoints[1].id,
            endpoint: "SRV-ERP-01",
            type: "disk_check",
            name: "Verificacao de disco",
            status: "queued",
            command: "nightowl.disk.check",
            createdBy: "Renan Santos",
            createdAt: new Date(Date.now() - 4 * 60000).toISOString(),
            startedAt: "",
            finishedAt: "",
            durationMs: 0,
            result: "Aguardando agente",
            stdout: "",
            stderr: "",
            exitCode: null,
            payload: { endpoint: "SRV-ERP-01", volumes: ["C:", "D:"] },
            timeline: ["queued"]
        },
        {
            id: "J-003",
            endpointId: endpoints[3].id,
            endpoint: "DIR-NB-03",
            type: "defender_check",
            name: "Verificar Defender",
            status: "running",
            command: "nightowl.security.defender",
            createdBy: "Gabriel Oliveira",
            createdAt: new Date(Date.now() - 7 * 60000).toISOString(),
            startedAt: new Date(Date.now() - 5 * 60000).toISOString(),
            finishedAt: "",
            durationMs: 0,
            result: "Executando",
            stdout: "Checking Defender and Bitdefender state...",
            stderr: "",
            exitCode: null,
            payload: { endpoint: "DIR-NB-03", check: "defender" },
            timeline: ["queued", "sent", "running"]
        },
        {
            id: "J-004",
            endpointId: endpoints[2].id,
            endpoint: "JUR-PRINT-01",
            type: "ping",
            name: "Ping",
            status: "failed",
            command: "nightowl.network.ping",
            createdBy: "Sistema",
            createdAt: new Date(Date.now() - 62 * 60000).toISOString(),
            startedAt: new Date(Date.now() - 61 * 60000).toISOString(),
            finishedAt: new Date(Date.now() - 60 * 60000).toISOString(),
            durationMs: 22000,
            result: "Sem resposta",
            stdout: "Pinging 192.168.104.66...",
            stderr: "Request timed out.",
            exitCode: 1,
            payload: { endpoint: "JUR-PRINT-01", count: 4 },
            timeline: ["queued", "sent", "running", "failed"]
        },
        {
            id: "J-005",
            endpointId: endpoints[5].id,
            endpoint: "COM-017",
            type: "windows_update_scan",
            name: "Windows Update Scan",
            status: "expired",
            command: "nightowl.patch.scan",
            createdBy: "Scheduler",
            createdAt: new Date(Date.now() - 9 * 3600000).toISOString(),
            startedAt: "",
            finishedAt: "",
            durationMs: 0,
            result: "Expirado antes do pull do agente",
            stdout: "",
            stderr: "Agent did not pull job before timeout.",
            exitCode: null,
            payload: { endpoint: "COM-017", timeoutMinutes: 120 },
            timeline: ["queued", "expired"]
        }
    ];

    function clone(value) {
        return JSON.parse(JSON.stringify(value));
    }

    function stored(key) {
        try {
            return JSON.parse(window.localStorage.getItem(key) || "[]");
        } catch (error) {
            return [];
        }
    }

    function save(key, value) {
        window.localStorage.setItem(key, JSON.stringify(value.slice(-150)));
    }

    function storedObject(key) {
        try {
            var value = JSON.parse(window.localStorage.getItem(key) || "{}");
            return value && typeof value === "object" && !Array.isArray(value) ? value : {};
        } catch (error) {
            return {};
        }
    }

    function saveObject(key, value) {
        window.localStorage.setItem(key, JSON.stringify(value || {}));
    }

    function delay(value, ms) {
        return new Promise(function (resolve) {
            window.setTimeout(function () {
                resolve(clone(value));
            }, ms == null ? apiDelay : ms);
        });
    }

    function allEvents() {
        return baseEvents.concat(stored(localEventsKey));
    }

    function allJobs() {
        return jobs.concat(stored(localJobsKey));
    }

    function allOperationalTasks() {
        return operationalTasks.concat(stored(localTasksKey));
    }

    function saveOperationalTasks(tasks) {
        save(localTasksKey, tasks);
    }

    function alertState() {
        try {
            return JSON.parse(window.localStorage.getItem(localAlertStateKey) || "{}");
        } catch (error) {
            return {};
        }
    }

    function saveAlertState(value) {
        window.localStorage.setItem(localAlertStateKey, JSON.stringify(value || {}));
    }

    function allTickets() {
        return tickets.concat(stored(localTicketStateKey));
    }

    function findAlertIndex(idOrTitle) {
        var needle = normalizeText(idOrTitle);
        return alerts.concat(stored(localGeneratedAlertsKey)).findIndex(function (item) {
            return normalizeText(item.id) === needle || normalizeText(item.title) === needle;
        });
    }

    function allAlerts() {
        var state = alertState();
        return alerts.concat(stored(localGeneratedAlertsKey)).map(function (alert) {
            return Object.assign({}, alert, state[alert.id] || {}, state[alert.title] || {});
        });
    }

    function findEndpoint(idOrHost) {
        return endpoints.find(function (endpoint) {
            return endpoint.id === idOrHost || endpoint.hostname === idOrHost;
        });
    }

    function normalizeText(value) {
        return String(value == null ? "" : value).toLowerCase();
    }

    function filterEndpoints(filters) {
        var f = filters || {};
        var q = normalizeText(f.q);
        return endpoints.filter(function (endpoint) {
            if (q && [
                endpoint.hostname,
                endpoint.ip,
                endpoint.user,
                endpoint.sector,
                endpoint.domain,
                endpoint.os,
                endpoint.type,
                endpoint.attention
            ].join(" ").toLowerCase().indexOf(q) < 0) return false;
            if (f.status && endpoint.status !== f.status) return false;
            if (f.type && endpoint.type !== f.type) return false;
            if (f.sector && endpoint.sector !== f.sector) return false;
            if (f.agent && endpoint.agent.state !== f.agent) return false;
            return true;
        });
    }

    function filterAlerts(filters) {
        var f = filters || {};
        var q = normalizeText(f.q);
        return allAlerts().filter(function (alert) {
            if (q && [alert.title, alert.endpoint, alert.description, alert.type, alert.owner].join(" ").toLowerCase().indexOf(q) < 0) return false;
            if (f.status && alert.status !== f.status) return false;
            if (f.severity && alert.severity !== f.severity) return false;
            if (f.endpointId && alert.endpointId !== f.endpointId) return false;
            return true;
        });
    }

    function filterEvents(filters) {
        var f = filters || {};
        var q = normalizeText(f.q);
        return allEvents().filter(function (event) {
            if (q && [event.title, event.eventType, event.endpoint, event.actor, event.description].join(" ").toLowerCase().indexOf(q) < 0) return false;
            if (f.category && f.category !== "all" && event.category !== f.category) return false;
            if (f.severity && event.severity !== f.severity) return false;
            if (f.endpointId && event.endpointId !== f.endpointId) return false;
            return true;
        }).sort(function (a, b) {
            return new Date(b.timestamp).getTime() - new Date(a.timestamp).getTime();
        });
    }

    function addMockEvent(partial) {
        var event = Object.assign({
            id: "E-local-" + Date.now(),
            title: "Acao operacional",
            eventType: "ui.mock_action",
            severity: "info",
            category: "system",
            source: "User",
            endpointId: "",
            endpoint: "",
            actor: "Usuario atual",
            description: "Acao registrada na camada mockada do frontend.",
            timestamp: new Date().toISOString()
        }, partial || {});
        var events = stored(localEventsKey);
        events.push(event);
        save(localEventsKey, events);
        window.dispatchEvent(new CustomEvent("nightowl:mock-api-event", { detail: clone(event) }));
        window.dispatchEvent(new CustomEvent("nightowl:event-created", { detail: clone(event) }));
        return clone(event);
    }

    function setAlertPatch(id, patch) {
        var index = findAlertIndex(id);
        if (index < 0) return null;
        var base = alerts.concat(stored(localGeneratedAlertsKey))[index];
        var state = alertState();
        var nextPatch = Object.assign({}, state[base.id] || {}, patch || {});
        state[base.id] = nextPatch;
        state[base.title] = nextPatch;
        saveAlertState(state);
        var alert = Object.assign({}, base, nextPatch);
        window.dispatchEvent(new CustomEvent("nightowl:alert-updated", { detail: clone(alert) }));
        return alert;
    }

    function setAlertStatus(id, status) {
        var alert = setAlertPatch(id, { status: status });
        if (!alert) return null;
        addMockEvent({
            title: "Alerta atualizado",
            eventType: "alert." + status,
            category: "alerts",
            severity: status === "resolved" ? "success" : "info",
            endpointId: alert.endpointId,
            endpoint: alert.endpoint,
            description: alert.title + " marcado como " + status + "."
        });
        return alert;
    }

    function addAlertNote(id, note) {
        var alert = setAlertPatch(id, {
            lastNote: note || "Observacao operacional registrada.",
            lastNoteAt: new Date().toISOString()
        });
        if (!alert) return null;
        addMockEvent({
            title: "Observacao adicionada ao alerta",
            eventType: "alert.note_added",
            category: "alerts",
            severity: "info",
            endpointId: alert.endpointId,
            endpoint: alert.endpoint,
            description: alert.lastNote
        });
        return alert;
    }

    function createTicketFromAlert(id) {
        var alert = allAlerts().find(function (item) { return item.id === id || item.title === id; });
        if (!alert) return null;
        var ticket = {
            id: "T-MOCK-" + Date.now(),
            number: "#MOCK-" + String(Date.now()).slice(-4),
            title: alert.title,
            status: "Novo",
            priority: alert.severity === "critical" ? "Critica" : alert.severity === "warning" ? "Alta" : "Normal",
            endpointId: alert.endpointId
        };
        var localTickets = stored(localTicketStateKey);
        localTickets.push(ticket);
        save(localTicketStateKey, localTickets);
        setAlertPatch(id, { ticket: ticket.number });
        addMockEvent({
            title: "Chamado mockado criado",
            eventType: "ticket.created_from_alert",
            category: "alerts",
            source: "User",
            severity: "info",
            endpointId: alert.endpointId,
            endpoint: alert.endpoint,
            description: ticket.number + " criado a partir de " + alert.title + "."
        });
        return ticket;
    }

    function createTicket(payload) {
        var endpoint = findEndpoint(payload && (payload.endpointId || payload.endpoint));
        var ticket = {
            id: "T-MOCK-" + Date.now(),
            number: "#MOCK-" + String(Date.now()).slice(-4),
            title: (payload && payload.title) || ("Atendimento RMM - " + (endpoint ? endpoint.hostname : (payload && payload.endpoint) || "endpoint")),
            status: "Novo",
            priority: (payload && payload.priority) || "Normal",
            endpointId: endpoint ? endpoint.id : (payload && payload.endpointId) || ""
        };
        var localTickets = stored(localTicketStateKey);
        localTickets.push(ticket);
        save(localTicketStateKey, localTickets);
        addMockEvent({
            title: "Chamado mockado criado",
            eventType: "ticket.created_from_endpoint",
            category: "alerts",
            source: "User",
            severity: "info",
            endpointId: ticket.endpointId,
            endpoint: endpoint ? endpoint.hostname : (payload && payload.endpoint) || "",
            description: ticket.number + " criado para atendimento operacional."
        });
        return ticket;
    }

    function taskChecklistProgress(task) {
        var items = task && task.checklist ? task.checklist : [];
        var done = items.filter(function (item) { return item.done; }).length;
        return { done: done, total: items.length };
    }

    function filterOperationalTasks(filters) {
        var f = filters || {};
        var q = normalizeText(f.q);
        var now = Date.now();
        var weekEnd = now + (7 * 86400000);
        return allOperationalTasks().filter(function (task) {
            if (q && [task.title, task.description, task.responsible, task.category, task.priority, task.linkedUser, task.location].join(" ").toLowerCase().indexOf(q) < 0) return false;
            if (f.status && task.status !== f.status) return false;
            if (f.priority && task.priority !== f.priority) return false;
            if (f.category && task.category !== f.category) return false;
            if (f.responsible && normalizeText(task.responsible) !== normalizeText(f.responsible)) return false;
            if (f.endpointId && task.linkedEndpointId !== f.endpointId) return false;
            if (f.due === "today") {
                var due = new Date(task.dueAt);
                var today = new Date();
                if (due.toDateString() !== today.toDateString()) return false;
            }
            if (f.due === "overdue" && !(new Date(task.dueAt).getTime() < now && task.status !== "done" && task.status !== "cancelled")) return false;
            if (f.due === "week" && !(new Date(task.dueAt).getTime() >= now && new Date(task.dueAt).getTime() <= weekEnd)) return false;
            return true;
        }).sort(function (a, b) {
            return new Date(a.dueAt || 0).getTime() - new Date(b.dueAt || 0).getTime();
        });
    }

    function createOperationalTask(payload) {
        var baseChecklist = payload && payload.checklist ? payload.checklist : [{ id: "c-" + Date.now(), title: "Executar atividade", done: false }];
        var task = Object.assign({
            id: "OT-local-" + Date.now(),
            title: "Nova tarefa operacional",
            description: "",
            status: "open",
            priority: "normal",
            category: "support",
            startAt: new Date().toISOString(),
            dueAt: new Date(Date.now() + 86400000).toISOString(),
            responsible: "Usuario atual",
            checklist: baseChecklist,
            linkedTicketId: "",
            linkedEndpointId: "",
            linkedUser: "",
            location: "",
            jobIds: [],
            createdAt: new Date().toISOString(),
            updatedAt: new Date().toISOString(),
            timeline: [{ at: new Date().toISOString(), actor: "Usuario atual", text: "Tarefa criada no mock frontend." }]
        }, payload || {});
        task.jobIds = task.jobIds || [];
        if (!task.id) {
            task.id = "OT-local-" + Date.now();
        }
        var localTasks = stored(localTasksKey);
        localTasks.push(task);
        saveOperationalTasks(localTasks);
        addMockEvent({
            title: "Tarefa operacional criada",
            eventType: "task.created",
            category: "system",
            source: "User",
            severity: task.priority === "critical" ? "critical" : "info",
            endpointId: task.linkedEndpointId || "",
            endpoint: (findEndpoint(task.linkedEndpointId) || {}).hostname || "",
            description: task.title
        });
        window.dispatchEvent(new CustomEvent("nightowl:task-updated", { detail: clone(task) }));
        return clone(task);
    }

    function taskById(id) {
        return allOperationalTasks().find(function (task) { return task.id === id; });
    }

    function createTicketFromTask(taskId) {
        var task = taskById(taskId);
        if (!task) return null;
        if (task.linkedTicketId) {
            var existing = allTickets().find(function (ticket) {
                return ticket.id === task.linkedTicketId || ticket.number === task.linkedTicketId;
            });
            if (existing) return existing;
        }
        var endpoint = findEndpoint(task.linkedEndpointId);
        var ticket = createTicket({
            endpointId: task.linkedEndpointId,
            endpoint: endpoint ? endpoint.hostname : "",
            title: "Tarefa operacional - " + task.title,
            priority: task.priority === "critical" ? "Critica" : task.priority === "high" ? "Alta" : "Normal"
        });
        updateOperationalTask(task.id, {
            linkedTicketId: ticket.id,
            timeline: (task.timeline || []).concat({
                at: new Date().toISOString(),
                actor: "Usuario atual",
                text: "Chamado " + ticket.number + " vinculado a tarefa."
            })
        });
        addMockEvent({
            title: "Chamado vinculado a tarefa",
            eventType: "task.ticket_linked",
            category: "alerts",
            source: "User",
            severity: "info",
            endpointId: task.linkedEndpointId || "",
            endpoint: endpoint ? endpoint.hostname : "",
            description: ticket.number + " vinculado a " + task.title + "."
        });
        return ticket;
    }

    function linkJobToTask(taskId, jobId) {
        var task = taskById(taskId);
        if (!task || !jobId) return null;
        var jobIds = Array.from(new Set((task.jobIds || []).concat(jobId)));
        return updateOperationalTask(task.id, {
            jobIds: jobIds,
            timeline: (task.timeline || []).concat({
                at: new Date().toISOString(),
                actor: "Usuario atual",
                text: "Job " + jobId + " vinculado a tarefa."
            })
        });
    }

    function updateOperationalTask(id, patch) {
        var localTasks = stored(localTasksKey);
        var localIndex = localTasks.findIndex(function (task) { return task.id === id; });
        var source = localTasks;
        var index = localIndex;
        if (index < 0) {
            index = operationalTasks.findIndex(function (task) { return task.id === id; });
            source = operationalTasks;
        }
        if (index < 0) return null;
        var existingTimeline = source[index].timeline || [];
        var patchTimeline = patch && Array.isArray(patch.timeline) ? patch.timeline : null;
        var eventText = patch && patch.timelineEvent ? patch.timelineEvent : (patch && patch.status === "done") ? "Tarefa marcada como concluida." : "Tarefa atualizada no mock.";
        source[index] = Object.assign({}, source[index], patch || {}, {
            updatedAt: new Date().toISOString(),
            timeline: (patchTimeline || existingTimeline).concat({
                at: new Date().toISOString(),
                actor: "Usuario atual",
                text: eventText
            })
        });
        if (source === localTasks) saveOperationalTasks(source);
        addMockEvent({
            title: "Tarefa operacional atualizada",
            eventType: "task.updated",
            category: "system",
            source: "User",
            severity: source[index].priority === "critical" ? "warning" : "info",
            endpointId: source[index].linkedEndpointId || "",
            endpoint: (findEndpoint(source[index].linkedEndpointId) || {}).hostname || "",
            description: source[index].title
        });
        window.dispatchEvent(new CustomEvent("nightowl:task-updated", { detail: clone(source[index]) }));
        return clone(source[index]);
    }

    function createTaskFromTemplate(templateId, patch) {
        var template = taskTemplates.find(function (item) { return item.id === templateId; });
        if (!template) return null;
        return createOperationalTask(Object.assign({
            title: template.name,
            description: template.description,
            category: template.category,
            priority: "normal",
            checklist: template.checklist.map(function (item) {
                return Object.assign({}, item, { id: "c-" + Date.now() + "-" + item.id, done: false });
            })
        }, patch || {}));
    }

    function createMockJob(payload) {
        var endpoint = findEndpoint(payload && (payload.endpointId || payload.endpoint));
        var type = (payload && (payload.type || payload.action)) || "run_script";
        var commandByType = {
            force_inventory: "nightowl.inventory.collect",
            defender_check: "nightowl.security.defender",
            disk_check: "nightowl.disk.check",
            collect_logs: "nightowl.logs.collect",
            ping: "nightowl.network.ping",
            cleanup_temp: "nightowl.cleanup.temp",
            run_script: "nightowl.script.run",
            install_software: "nightowl.software.install",
            windows_update_scan: "nightowl.patch.scan"
        };
        var labelByType = {
            force_inventory: "Forcar inventario",
            defender_check: "Verificar Defender",
            disk_check: "Verificar disco",
            collect_logs: "Coletar logs",
            ping: "Ping",
            cleanup_temp: "Limpeza temporaria",
            run_script: "Executar script",
            install_software: "Instalar software",
            windows_update_scan: "Windows Update Scan"
        };
        var job = Object.assign({
            id: "J-local-" + Date.now(),
            endpointId: endpoint ? endpoint.id : (payload && payload.endpointId) || "",
            endpoint: endpoint ? endpoint.hostname : (payload && payload.endpoint) || "",
            type: type,
            name: labelByType[type] || "Execucao remota",
            status: "queued",
            command: commandByType[type] || "nightowl.check",
            createdBy: "Usuario atual",
            createdAt: new Date().toISOString(),
            startedAt: "",
            finishedAt: "",
            durationMs: 0,
            result: "Aguardando pull do agente",
            stdout: "",
            stderr: "",
            exitCode: null,
            payload: { endpoint: endpoint ? endpoint.hostname : (payload && payload.endpoint) || "", type: type },
            timeline: ["queued"]
        }, payload || {});
        var localJobs = stored(localJobsKey);
        localJobs.push(job);
        save(localJobsKey, localJobs);
        window.dispatchEvent(new CustomEvent("nightowl:job-created", { detail: clone(job) }));
        if (payload && payload.taskId) {
            linkJobToTask(payload.taskId, job.id);
        }
        addMockEvent({
            title: "Job remoto enfileirado",
            eventType: "job.queued",
            category: "jobs",
            source: "Job",
            severity: "info",
            endpointId: job.endpointId,
            endpoint: job.endpoint,
            description: job.name + " aguardando pull do agente."
        });
        ["sent", "running", "completed"].forEach(function (status, index) {
            window.setTimeout(function () {
                var jobsNow = stored(localJobsKey);
                var item = jobsNow.find(function (entry) { return entry.id === job.id; });
                if (item) {
                    item.status = status;
                    item.timeline = Array.from(new Set((item.timeline || []).concat(status)));
                    if (status === "running" && !item.startedAt) item.startedAt = new Date().toISOString();
                    if (status === "completed") {
                        item.finishedAt = new Date().toISOString();
                        item.durationMs = item.startedAt ? Math.max(1000, new Date(item.finishedAt).getTime() - new Date(item.startedAt).getTime()) : 2800;
                        item.result = "Concluido no mock";
                        item.stdout = item.stdout || (item.name + " concluido com sucesso em " + item.endpoint + ".");
                        item.exitCode = 0;
                    }
                    save(localJobsKey, jobsNow);
                    window.dispatchEvent(new CustomEvent("nightowl:job-updated", { detail: clone(item) }));
                    addMockEvent({
                        title: status === "completed" ? "Job concluido" : "Job " + status,
                        eventType: "job." + status,
                        category: "jobs",
                        source: status === "sent" ? "Job" : "Agent",
                        severity: status === "completed" ? "success" : "info",
                        endpointId: job.endpointId,
                        endpoint: job.endpoint,
                        description: job.name + " atualizado para " + status + "."
                    });
                }
            }, 700 + (index * 900));
        });
        return clone(job);
    }

    function updateLocalJob(jobId, patch) {
        var localJobs = stored(localJobsKey);
        var index = localJobs.findIndex(function (item) { return item.id === jobId; });
        var source = localJobs;
        if (index < 0) {
            index = jobs.findIndex(function (item) { return item.id === jobId; });
            source = jobs;
        }
        if (index < 0) return null;
        source[index] = Object.assign({}, source[index], patch || {}, { updatedAt: new Date().toISOString() });
        if (source === localJobs) save(localJobsKey, source);
        window.dispatchEvent(new CustomEvent("nightowl:job-updated", { detail: clone(source[index]) }));
        return clone(source[index]);
    }

    function cancelJob(jobId) {
        var job = updateLocalJob(jobId, {
            status: "cancelled",
            finishedAt: new Date().toISOString(),
            result: "Cancelado pelo operador",
            stderr: "Job cancelled before completion.",
            exitCode: null
        });
        if (job) {
            addMockEvent({
                title: "Job cancelado",
                eventType: "job.cancelled",
                category: "jobs",
                source: "User",
                severity: "warning",
                endpointId: job.endpointId,
                endpoint: job.endpoint,
                description: job.name + " cancelado na fila operacional."
            });
        }
        return job;
    }

    function softwareStatusFor(item) {
        var state = storedObject(localSoftwareInventoryStateKey)[item.id] || {};
        if (state.status === "prohibited") return "forbidden";
        if (state.status) return state.status;
        if (item.category === "remote" || item.risk === "high") return "sensitive";
        if (item.name.toLowerCase().indexOf("anydesk") >= 0) return "forbidden";
        if (item.category === "other" && !item.publisher) return "unknown";
        if (["microsoft", "security", "browser", "office"].indexOf(item.category) >= 0) return "approved";
        return "unknown";
    }

    function softwareCategoryLabel(category) {
        return {
            microsoft: "Microsoft",
            security: "Seguranca",
            remote: "Acesso remoto",
            admin: "Admin/Rede",
            browser: "Navegador",
            office: "Produtividade",
            development: "Desenvolvimento",
            other: "Outros"
        }[category] || category || "Outros";
    }

    function softwareRiskLabel(risk) {
        return { low: "Baixo", medium: "Medio", high: "Alto", critical: "Critico" }[risk] || risk || "Baixo";
    }

    function softwareStatusLabel(status) {
        return {
            approved: "Aprovado",
            sensitive: "Sensivel",
            forbidden: "Proibido",
            prohibited: "Proibido",
            unknown: "Desconhecido",
            evaluating: "Em avaliacao",
            required: "Obrigatorio",
            draft: "Rascunho",
            testing: "Teste",
            retired: "Inativo",
            queued: "Em fila",
            running: "Em execucao",
            completed: "Concluida",
            failed: "Falha",
            cancelled: "Cancelada"
        }[status] || status || "-";
    }

    function softwareId(name, publisher) {
        return normalizeText((name || "") + "::" + (publisher || "")).replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "");
    }

    function slug(value) {
        return normalizeText(value || "software").replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "") || "software";
    }

    function packageHash() {
        return ("mock" + Date.now() + "00112233445566778899aabbccddeeff").slice(0, 64);
    }

    function normalizeSoftwarePackage(pkg) {
        var catalog = allSoftwareCatalog().find(function (item) { return item.id === pkg.softwareId; }) || {};
        var name = pkg.name || (catalog.name ? catalog.name + " package" : "Pacote NightOwl");
        var version = pkg.version || catalog.approvedVersion || "1.0.0";
        var file = pkg.file || (slug(name) + "." + normalizeText(pkg.fileType || "msi"));
        var softwareSlug = slug(catalog.name || name);
        return Object.assign({
            publisher: catalog.publisher || "",
            category: catalog.category || "other",
            fileType: "MSI",
            architecture: "x64",
            sizeMb: 0,
            sha256: packageHash(),
            logicalPath: "/opt/nightowl/packages/" + softwareSlug + "/" + version + "/" + file,
            repositoryRoot: "/opt/nightowl/packages/",
            endpointCachePath: "C:\\ProgramData\\NightOwl\\Packages\\",
            endpointLogsPath: "C:\\ProgramData\\NightOwl\\Logs\\",
            installCommand: "",
            installArguments: "",
            uninstallCommand: "",
            uninstallArguments: "",
            detectionMethod: "software_name_version",
            detectionRule: catalog.name ? catalog.name + " >= " + version : "",
            timeoutMinutes: 30,
            requiresReboot: false,
            requiresLoggedOff: false,
            executionContext: "system",
            status: "draft",
            uploadedBy: "Usuario atual",
            uploadedAt: new Date().toISOString(),
            approvedBy: "",
            approvedAt: ""
        }, pkg || {}, {
            name: name,
            version: version,
            file: file,
            publisher: pkg.publisher || catalog.publisher || "",
            category: pkg.category || catalog.category || "other"
        });
    }

    function buildSoftwareInventory() {
        var grouped = {};
        endpoints.forEach(function (endpoint) {
            (softwareByEndpoint[endpoint.hostname] || []).forEach(function (software) {
                var id = softwareId(software.name, software.publisher);
                if (!grouped[id]) {
                    grouped[id] = {
                        id: id,
                        name: software.name,
                        publisher: software.publisher || "",
                        category: software.category || "other",
                        categoryLabel: softwareCategoryLabel(software.category),
                        risk: software.risk || "low",
                        riskLabel: softwareRiskLabel(software.risk),
                        versions: [],
                        endpointIds: [],
                        endpoints: [],
                        latestSeenAt: software.installedAt || new Date().toISOString()
                    };
                }
                if (grouped[id].versions.indexOf(software.version || "-") < 0) grouped[id].versions.push(software.version || "-");
                grouped[id].endpointIds.push(endpoint.id);
                grouped[id].endpoints.push({
                    id: endpoint.id,
                    hostname: endpoint.hostname,
                    sector: endpoint.sector,
                    user: endpoint.user,
                    version: software.version || "-",
                    installedAt: software.installedAt || ""
                });
                if (new Date(software.installedAt || 0).getTime() > new Date(grouped[id].latestSeenAt || 0).getTime()) {
                    grouped[id].latestSeenAt = software.installedAt;
                }
            });
        });
        return Object.keys(grouped).map(function (id) {
            var item = grouped[id];
            item.endpointCount = item.endpointIds.length;
            item.installCount = item.endpointIds.length;
            item.versionsDisplay = item.versions.join(", ");
            item.status = softwareStatusFor(item);
            item.statusLabel = softwareStatusLabel(item.status);
            item.catalogId = (allSoftwareCatalog().find(function (catalog) {
                return normalizeText(catalog.name) === normalizeText(item.name);
            }) || {}).id || "";
            return item;
        }).sort(function (a, b) {
            return b.endpointCount - a.endpointCount || a.name.localeCompare(b.name);
        });
    }

    function allSoftwareCatalog() {
        return softwareCatalog.concat(stored(localSoftwareCatalogKey));
    }

    function allSoftwarePackages() {
        return softwarePackages.concat(stored(localSoftwarePackagesKey)).map(normalizeSoftwarePackage);
    }

    function allSoftwareDeployments() {
        return softwareDeployments.concat(stored(localSoftwareDeploymentsKey));
    }

    function allSoftwareRules() {
        return softwareRules.concat(stored(localSoftwareRulesKey));
    }

    function filterSoftwareInventory(filters) {
        var f = filters || {};
        var q = normalizeText(f.q);
        return buildSoftwareInventory().filter(function (item) {
            var text = [item.name, item.publisher, item.versionsDisplay].concat(item.endpoints.map(function (endpoint) { return endpoint.hostname; })).join(" ");
            if (q && normalizeText(text).indexOf(q) < 0) return false;
            if (f.category && f.category !== "all" && item.category !== f.category) return false;
            if (f.risk && f.risk !== "all" && item.risk !== f.risk) return false;
            if (f.status && f.status !== "all" && item.status !== f.status) return false;
            if (f.publisher && normalizeText(item.publisher) !== normalizeText(f.publisher)) return false;
            if (f.endpointId && item.endpointIds.indexOf(f.endpointId) < 0) return false;
            return true;
        });
    }

    function filterSoftwareCatalog(filters) {
        var q = normalizeText((filters || {}).q);
        return allSoftwareCatalog().filter(function (item) {
            if (q && normalizeText([item.name, item.publisher, item.category, item.approvedVersion].join(" ")).indexOf(q) < 0) return false;
            if (filters && filters.status && filters.status !== "all" && item.status !== filters.status) return false;
            return true;
        });
    }

    function filterSoftwarePackages(filters) {
        var q = normalizeText((filters || {}).q);
        return allSoftwarePackages().filter(function (item) {
            if (q && normalizeText([item.name, item.file, item.version, item.fileType].join(" ")).indexOf(q) < 0) return false;
            if (filters && filters.status && filters.status !== "all" && item.status !== filters.status) return false;
            return true;
        });
    }

    function buildSoftwareUpdates() {
        return allSoftwareCatalog().filter(function (catalog) {
            return catalog.approvedVersion;
        }).map(function (catalog) {
            var inventory = buildSoftwareInventory().find(function (item) {
                return normalizeText(item.name) === normalizeText(catalog.name);
            });
            var detected = inventory ? inventory.versions : [];
            var outdated = inventory ? inventory.endpoints.filter(function (endpoint) { return endpoint.version !== catalog.approvedVersion; }) : [];
            return {
                id: "UPD-" + catalog.id,
                softwareId: catalog.id,
                software: catalog.name,
                approvedVersion: catalog.approvedVersion,
                detectedVersions: detected,
                outdatedEndpoints: outdated.length,
                risk: outdated.length ? (catalog.category === "security" ? "critical" : "warning") : "low",
                suggestedAction: outdated.length ? "Criar implantacao de atualizacao" : "Sem acao",
                latestSeenAt: inventory ? inventory.latestSeenAt : catalog.updatedAt,
                endpoints: outdated
            };
        }).filter(function (item) {
            return item.detectedVersions.length || item.outdatedEndpoints;
        });
    }

    function setSoftwareInventoryStatus(id, status) {
        if (status === "prohibited") status = "forbidden";
        var state = storedObject(localSoftwareInventoryStateKey);
        state[id] = Object.assign({}, state[id] || {}, { status: status, updatedAt: new Date().toISOString() });
        saveObject(localSoftwareInventoryStateKey, state);
        var item = buildSoftwareInventory().find(function (entry) { return entry.id === id; });
        addMockEvent({
            title: "Software marcado como " + softwareStatusLabel(status),
            eventType: "software.governance_changed",
            category: "inventory",
            source: "User",
            severity: status === "forbidden" ? "security" : status === "sensitive" ? "warning" : "info",
            description: item ? item.name + " atualizado na central de softwares." : id
        });
        return item || null;
    }

    function createSoftwarePackage(payload) {
        var item = normalizeSoftwarePackage(Object.assign({
            id: "PKG-local-" + Date.now(),
            name: "Novo pacote",
            softwareId: "",
            version: "1.0.0",
            file: "upload-preview.msi",
            fileType: "MSI",
            architecture: "x64",
            sizeMb: 0,
            sha256: "mock-" + Date.now(),
            installCommand: "",
            uninstallCommand: "",
            detectionRule: "",
            timeoutMinutes: 30,
            requiresReboot: false,
            status: "draft",
            uploadedBy: "Usuario atual",
            uploadedAt: new Date().toISOString()
        }, payload || {}));
        var local = stored(localSoftwarePackagesKey);
        local.push(item);
        save(localSoftwarePackagesKey, local);
        addMockEvent({ title: "Pacote de software enviado", eventType: "software.package_uploaded", category: "inventory", source: "User", severity: "info", description: item.name });
        return clone(item);
    }

    function updateSoftwarePackage(id, patch) {
        var local = stored(localSoftwarePackagesKey);
        var source = local;
        var index = local.findIndex(function (item) { return item.id === id; });
        if (index < 0) {
            source = softwarePackages;
            index = source.findIndex(function (item) { return item.id === id; });
        }
        if (index < 0) return null;
        var nextPatch = Object.assign({}, patch || {});
        if (nextPatch.status === "approved" && !nextPatch.approvedAt) {
            nextPatch.approvedBy = nextPatch.approvedBy || "Usuario atual";
            nextPatch.approvedAt = new Date().toISOString();
        }
        source[index] = normalizeSoftwarePackage(Object.assign({}, source[index], nextPatch, { updatedAt: new Date().toISOString() }));
        if (source === local) save(localSoftwarePackagesKey, source);
        addMockEvent({ title: "Pacote de software atualizado", eventType: "software.package_updated", category: "inventory", source: "User", severity: "info", description: source[index].name });
        return clone(source[index]);
    }

    function duplicateSoftwarePackage(id) {
        var pkg = allSoftwarePackages().find(function (item) { return item.id === id; });
        if (!pkg) return null;
        return createSoftwarePackage(Object.assign({}, pkg, {
            id: "PKG-local-" + Date.now(),
            name: pkg.name + " copia",
            status: "draft",
            uploadedBy: "Usuario atual",
            uploadedAt: new Date().toISOString(),
            approvedBy: "",
            approvedAt: ""
        }));
    }

    function createSoftwareDeployment(payload) {
        payload = payload || {};
        var catalog = allSoftwareCatalog().find(function (item) { return item.id === payload.softwareId; }) || {};
        var pkg = allSoftwarePackages().find(function (item) { return item.id === payload.packageId; }) || {};
        if (!catalog.id && pkg.softwareId) {
            catalog = allSoftwareCatalog().find(function (item) { return item.id === pkg.softwareId; }) || {};
        }
        var targetIds = payload.endpointIds && payload.endpointIds.length ? payload.endpointIds : endpoints.slice(0, 2).map(function (endpoint) { return endpoint.id; });
        var deploymentId = payload.id || ("DEP-local-" + Date.now());
        var jobsCreated = targetIds.map(function (endpointId) {
            var endpoint = findEndpoint(endpointId);
            return createMockJob({
                endpointId: endpointId,
                endpoint: endpoint ? endpoint.hostname : endpointId,
                type: "install_software",
                name: "Implantar " + (catalog.name || payload.software || "software"),
                command: pkg.installCommand || "nightowl.software.install",
                payload: {
                    deploymentId: deploymentId,
                    packageId: pkg.id || payload.packageId,
                    softwareId: catalog.id || payload.softwareId,
                    allowReboot: !!payload.allowReboot,
                    timeoutMinutes: payload.timeoutMinutes || 45,
                    retryOnFailure: payload.retryOnFailure !== false
                }
            });
        });
        jobsCreated.forEach(function (job) {
            addMockEvent({
                title: "Job tecnico criado",
                eventType: "job.created",
                category: "jobs",
                source: "Job",
                severity: "info",
                endpointId: job.endpointId,
                endpoint: job.endpoint,
                description: job.name + " criado para a implantacao " + deploymentId + "."
            });
        });
        var deployment = Object.assign({
            id: deploymentId,
            softwareId: catalog.id || payload.softwareId || "",
            packageId: pkg.id || payload.packageId || "",
            software: catalog.name || payload.software || "Software",
            packageName: pkg.name || payload.packageName || "Pacote mockado",
            targetType: payload.targetType || "endpoint",
            targetLabel: payload.targetLabel || "Selecao manual",
            endpointIds: targetIds,
            status: "queued",
            progress: 0,
            scheduleMode: payload.scheduleMode || "now",
            scheduledAt: payload.scheduledAt || "",
            allowReboot: !!payload.allowReboot,
            timeoutMinutes: payload.timeoutMinutes || 45,
            retryOnFailure: payload.retryOnFailure !== false,
            notes: payload.notes || "",
            createdBy: "Usuario atual",
            createdAt: new Date().toISOString(),
            finishedAt: "",
            failures: 0,
            jobIds: jobsCreated.map(function (job) { return job.id; }),
            stdout: "Implantacao criada no mock.",
            stderr: ""
        }, payload || {});
        var local = stored(localSoftwareDeploymentsKey);
        local.push(deployment);
        save(localSoftwareDeploymentsKey, local);
        addMockEvent({
            title: "Implantacao de software criada",
            eventType: "software.deployment_created",
            category: "jobs",
            source: "User",
            severity: "info",
            description: deployment.software + " para " + deployment.targetLabel
        });
        return clone(deployment);
    }

    function createSoftwareGeneratedAlert(software, severity) {
        if (!software || !software.endpointIds || !software.endpointIds.length) return null;
        var endpoint = findEndpoint(software.endpointIds[0]);
        var alert = {
            id: "A-SW-" + Date.now(),
            endpointId: endpoint ? endpoint.id : software.endpointIds[0],
            endpoint: endpoint ? endpoint.hostname : "",
            title: software.name + " exige governanca",
            description: software.name + " foi classificado como " + softwareStatusLabel(software.status) + " e requer acompanhamento.",
            severity: severity || (software.status === "forbidden" ? "security" : "warning"),
            status: "open",
            type: "software_governance",
            owner: "Nao atribuido",
            age: "agora",
            ticket: ""
        };
        var local = stored(localGeneratedAlertsKey);
        local.push(alert);
        save(localGeneratedAlertsKey, local);
        addMockEvent({
            title: "Alerta de software criado",
            eventType: "alert.created",
            category: "alerts",
            source: "System",
            severity: alert.severity,
            endpointId: alert.endpointId,
            endpoint: alert.endpoint,
            description: alert.title
        });
        return clone(alert);
    }

    function createSoftwareRuleFromInventory(id) {
        var software = buildSoftwareInventory().find(function (item) { return item.id === id; });
        if (!software) return null;
        var rule = {
            id: "SR-local-" + Date.now(),
            name: software.name + " detectado",
            condition: "software.name = " + software.name,
            scope: "Todos os endpoints",
            severity: software.status === "forbidden" ? "critical" : software.status === "sensitive" ? "security" : "info",
            action: software.status === "forbidden" || software.status === "sensitive" ? "Criar alerta" : "Registrar evento",
            active: true,
            lastRunAt: "",
            occurrences: software.endpointCount
        };
        var local = stored(localSoftwareRulesKey);
        local.push(rule);
        save(localSoftwareRulesKey, local);
        addMockEvent({ title: "Regra de software criada", eventType: "software.rule_created", category: "security", source: "User", severity: rule.severity, description: rule.name });
        if (software.status === "forbidden" || software.status === "sensitive") createSoftwareGeneratedAlert(software, rule.severity);
        return clone(rule);
    }

    function createSoftwareTask(id, taskType) {
        var software = buildSoftwareInventory().find(function (item) { return item.id === id; });
        if (!software) return null;
        var isUpdate = taskType === "update";
        var firstEndpoint = software.endpointIds && software.endpointIds[0] ? software.endpointIds[0] : "";
        return createOperationalTask({
            title: (isUpdate ? "Atualizar " : "Remover/regularizar ") + software.name,
            description: "Tarefa mockada criada a partir da Central de Softwares para " + software.endpointCount + " endpoint(s).",
            status: "open",
            priority: software.status === "forbidden" || software.risk === "high" ? "high" : "normal",
            category: isUpdate ? "maintenance" : "security",
            dueAt: new Date(Date.now() + (isUpdate ? 3 : 1) * 86400000).toISOString(),
            linkedEndpointId: firstEndpoint,
            linkedUser: (findEndpoint(firstEndpoint) || {}).user || "",
            checklist: [
                { id: "sw-c1-" + Date.now(), title: "Validar endpoints afetados", done: false },
                { id: "sw-c2-" + Date.now(), title: isUpdate ? "Preparar pacote de atualizacao" : "Confirmar politica de remocao", done: false },
                { id: "sw-c3-" + Date.now(), title: "Executar acao e registrar evidencias", done: false }
            ],
            softwareId: software.id,
            softwareName: software.name,
            affectedEndpointIds: software.endpointIds
        });
    }

    function endpointDetail(endpoint) {
        if (!endpoint) return null;
        var inventory = inventoryByEndpoint[endpoint.hostname] || {};
        var patches = patchByEndpoint[endpoint.hostname] || { compliance: 100, lastScan: "nao coletado", criticalPending: 0, importantPending: 0, rebootPending: false, pending: [], history: [] };
        return Object.assign({}, endpoint, {
            inventory: inventory,
            disks: disksByEndpoint[endpoint.hostname] || [],
            software: softwareByEndpoint[endpoint.hostname] || [],
            security: securityByEndpoint[endpoint.hostname] || { antivirus: "Nao coletado", status: "unknown", signature: "-", firewall: "-", bitlocker: "-", remoteTools: [] },
            localAdmins: localAdminsByEndpoint[endpoint.hostname] || [],
            policyViolations: policyViolationsByEndpoint[endpoint.hostname] || [],
            patches: patches,
            tickets: allTickets().filter(function (ticket) { return ticket.endpointId === endpoint.id; }),
            alerts: filterAlerts({ endpointId: endpoint.id }),
            events: filterEvents({ endpointId: endpoint.id }),
            jobs: allJobs().filter(function (job) { return job.endpointId === endpoint.id; })
        });
    }

    var api = {
        getDashboardSummary: function () {
            return delay({
                endpoints: endpoints.length,
                online: endpoints.filter(function (item) { return item.status === "online"; }).length,
                offline: endpoints.filter(function (item) { return item.status === "offline"; }).length,
                criticalAlerts: alerts.filter(function (item) { return item.severity === "critical" && item.status !== "resolved"; }).length,
                attention: endpoints.filter(function (item) { return item.healthScore < 70 || item.status !== "online"; }).length
            });
        },
        getNocSummary: function () {
            var openAlerts = filterAlerts({}).filter(function (alert) { return alert.status !== "resolved" && alert.status !== "muted"; });
            return delay({
                endpoints: clone(endpoints),
                alerts: clone(openAlerts),
                events: clone(filterEvents({}).slice(0, 10)),
                metrics: {
                    monitored: endpoints.length,
                    critical: openAlerts.filter(function (item) { return item.severity === "critical"; }).length,
                    offline: endpoints.filter(function (item) { return item.status === "offline"; }).length,
                    affected: new Set(openAlerts.map(function (item) { return item.endpointId; })).size
                }
            });
        },
        getEndpoints: function (filters) {
            return delay(filterEndpoints(filters));
        },
        getEndpointById: function (id) {
            return delay(endpointDetail(findEndpoint(id)));
        },
        getAlerts: function (filters) {
            return delay(filterAlerts(filters));
        },
        getEvents: function (filters) {
            return delay(filterEvents(filters));
        },
        getJobs: function (filters) {
            var f = filters || {};
            return delay(allJobs().filter(function (job) {
                var q = normalizeText(f.q);
                if (q && [job.endpoint, job.type, job.command, job.createdBy, job.name, job.result].join(" ").toLowerCase().indexOf(q) < 0) return false;
                if (f.endpointId && job.endpointId !== f.endpointId) return false;
                if (f.endpoint && normalizeText(job.endpoint) !== normalizeText(f.endpoint)) return false;
                if (f.status && job.status !== f.status) return false;
                if (f.type && job.type !== f.type) return false;
                if (f.period === "24h" && new Date(job.createdAt).getTime() < Date.now() - 86400000) return false;
                if (f.period === "7d" && new Date(job.createdAt).getTime() < Date.now() - (7 * 86400000)) return false;
                return true;
            }));
        },
        getOperationalTasks: function (filters) {
            return delay(filterOperationalTasks(filters));
        },
        getTaskTemplates: function () {
            return delay(taskTemplates);
        },
        createOperationalTask: function (payload) {
            return delay(createOperationalTask(payload));
        },
        updateOperationalTask: function (id, patch) {
            return delay(updateOperationalTask(id, patch));
        },
        createTaskFromTemplate: function (templateId, patch) {
            return delay(createTaskFromTemplate(templateId, patch));
        },
        createTicketFromTask: function (taskId) {
            return delay(createTicketFromTask(taskId));
        },
        linkJobToTask: function (taskId, jobId) {
            return delay(linkJobToTask(taskId, jobId));
        },
        acknowledgeAlert: function (id) {
            return delay(setAlertStatus(id, "acknowledged"));
        },
        resolveAlert: function (id) {
            return delay(setAlertStatus(id, "resolved"));
        },
        silenceAlert: function (id) {
            return delay(setAlertStatus(id, "muted"));
        },
        addAlertNote: function (id, note) {
            return delay(addAlertNote(id, note));
        },
        createTicketFromAlert: function (id) {
            return delay(createTicketFromAlert(id));
        },
        createTicket: function (payload) {
            return delay(createTicket(payload));
        },
        createMockJob: function (payload) {
            return delay(createMockJob(payload));
        },
        cancelJob: function (jobId) {
            return delay(cancelJob(jobId));
        },
        rerunJob: function (jobId) {
            var job = allJobs().find(function (item) { return item.id === jobId; });
            return delay(job ? createMockJob({
                endpointId: job.endpointId,
                endpoint: job.endpoint,
                type: job.type,
                name: job.name,
                command: job.command,
                payload: job.payload,
                createdBy: "Usuario atual",
                rerunOf: job.id
            }) : null);
        },
        forceInventory: function (endpointId) {
            var endpoint = findEndpoint(endpointId);
            return delay(createMockJob({
                endpointId: endpoint ? endpoint.id : endpointId,
                endpoint: endpoint ? endpoint.hostname : endpointId,
                type: "force_inventory",
                name: "Forcar inventario",
                command: "nightowl.inventory.collect"
            }));
        },
        runEndpointCheck: function (endpointId, checkType) {
            var endpoint = findEndpoint(endpointId);
            var typeMap = {
                check_defender: "defender_check",
                check_disk: "disk_check",
                collect_logs: "collect_logs",
                run_cleanup: "cleanup_temp",
                execute_check: "run_script",
                ping: "ping"
            };
            var type = typeMap[checkType] || checkType || "run_script";
            var command = "nightowl." + (checkType || "check");
            return delay(createMockJob({
                endpointId: endpoint ? endpoint.id : endpointId,
                endpoint: endpoint ? endpoint.hostname : endpointId,
                type: type,
                name: "Executar " + (checkType || "verificacao"),
                command: command
            }));
        },
        addMockEvent: addMockEvent,
        getSoftwareInventory: function (filters) {
            return delay(filterSoftwareInventory(filters));
        },
        getSoftwareCatalog: function (filters) {
            return delay(filterSoftwareCatalog(filters || {}));
        },
        getSoftwarePackages: function (filters) {
            return delay(filterSoftwarePackages(filters || {}));
        },
        getSoftwareDeployments: function () {
            return delay(allSoftwareDeployments());
        },
        getSoftwareRules: function () {
            return delay(allSoftwareRules());
        },
        getSoftwareUpdates: function () {
            return delay(buildSoftwareUpdates());
        },
        setSoftwareInventoryStatus: function (id, status) {
            return delay(setSoftwareInventoryStatus(id, status));
        },
        createSoftwarePackage: function (payload) {
            return delay(createSoftwarePackage(payload));
        },
        updateSoftwarePackage: function (id, patch) {
            return delay(updateSoftwarePackage(id, patch));
        },
        duplicateSoftwarePackage: function (id) {
            return delay(duplicateSoftwarePackage(id));
        },
        createSoftwareDeployment: function (payload) {
            return delay(createSoftwareDeployment(payload || {}));
        },
        createSoftwareRuleFromInventory: function (id) {
            return delay(createSoftwareRuleFromInventory(id));
        },
        createSoftwareTask: function (id, taskType) {
            return delay(createSoftwareTask(id, taskType));
        },
        getSnapshot: function () {
            return clone({
                endpoints: endpoints,
                alerts: allAlerts(),
                events: allEvents(),
                jobs: allJobs(),
                operationalTasks: allOperationalTasks(),
                taskTemplates: taskTemplates,
                tickets: allTickets(),
                softwareInventory: buildSoftwareInventory(),
                softwareCatalog: allSoftwareCatalog(),
                softwarePackages: allSoftwarePackages(),
                softwareDeployments: allSoftwareDeployments(),
                softwareRules: allSoftwareRules(),
                softwareUpdates: buildSoftwareUpdates()
            });
        },
        __types: [
            "EndpointSummary",
            "EndpointDetail",
            "EndpointDisk",
            "EndpointSoftware",
            "EndpointSecurity",
            "EndpointAgent",
            "AlertItem",
            "EventItem",
            "JobItem",
            "TicketSummary",
            "OperationalTask",
            "TaskChecklistItem",
            "TaskTemplate",
            "TaskPriority",
            "TaskStatus",
            "TaskCategory",
            "SoftwareInventoryItem",
            "SoftwareCatalogItem",
            "SoftwarePackage",
            "SoftwareDeployment",
            "SoftwareDeploymentTarget",
            "SoftwareRule",
            "SoftwareUpdateDrift",
            "Severity",
            "EndpointStatus",
            "AlertStatus",
            "EventCategory",
            "JobStatus"
        ]
    };

    window.MockNightowlApi = api;
}());
