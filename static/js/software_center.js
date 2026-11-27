(function () {
    "use strict";

    var root = document.querySelector("[data-software-root]");
    if (!root) return;

    var inventory = [];
    var packages = [];
    var summary = {};
    var activeTab = "inventory";
    var inventoryLoaded = false;
    var packagesLoaded = false;
    var inventoryState = root.querySelector("[data-software-inventory-state]");
    var packagesState = root.querySelector("[data-software-packages-state]");
    var inventoryList = root.querySelector("[data-software-inventory]");
    var packageList = root.querySelector("[data-software-packages]");
    var drawer = root.querySelector("[data-software-drawer]");
    var drawerBackdrop = root.querySelector("[data-software-drawer-backdrop]");

    function esc(value) {
        return String(value == null ? "" : value).replace(/[&<>"']/g, function (char) {
            return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[char];
        });
    }

    function date(value) {
        if (!value) return "Não informado";
        var parsed = new Date(value);
        return Number.isNaN(parsed.getTime()) ? "Não informado" : parsed.toLocaleString("pt-BR");
    }

    function size(value) {
        if (value == null) return "Não informado";
        var units = ["B", "KB", "MB", "GB", "TB"];
        var amount = Number(value);
        var index = 0;
        while (amount >= 1024 && index < units.length - 1) { amount /= 1024; index += 1; }
        return amount.toLocaleString("pt-BR", { maximumFractionDigits: 1 }) + " " + units[index];
    }

    function icon(name) { return '<i data-lucide="' + esc(name) + '"></i>'; }
    function icons() { if (window.lucide && window.lucide.createIcons) window.lucide.createIcons(); }

    function setState(node, message, kind) {
        if (!node) return;
        node.textContent = message;
        node.dataset.state = kind || "info";
        node.hidden = !message;
    }

    function summaryCards() {
        var node = root.querySelector("[data-software-summary]");
        if (!node) return;
        node.innerHTML = [
            ["Aplicativos encontrados", summary.software_count || 0, "boxes"],
            ["Instalações observadas", summary.install_count || 0, "monitor"],
            ["Endpoints sem inventário", summary.without_inventory_count || 0, "database-zap"],
            ["Endpoints com dados antigos", summary.stale_endpoint_count || 0, "clock-3"]
        ].map(function (item) {
            return '<article class="software-summary-card"><span>' + icon(item[2]) + esc(item[0]) + '</span><strong>' + esc(item[1]) + '</strong></article>';
        }).join("");
        icons();
    }

    function filteredInventory() {
        var query = (root.querySelector("[data-software-search]").value || "").trim().toLocaleLowerCase("pt-BR");
        var freshness = root.querySelector("[data-software-freshness]").value;
        return inventory.filter(function (item) {
            var text = [item.name, item.publisher, (item.versions || []).join(" "), (item.endpoints || []).map(function (entry) { return entry.hostname; }).join(" ")].join(" ").toLocaleLowerCase("pt-BR");
            if (query && !text.includes(query)) return false;
            if (freshness === "current" && !item.current_count) return false;
            if (freshness === "stale" && !item.stale_count) return false;
            if (freshness === "unknown" && !item.unknown_count) return false;
            return true;
        });
    }

    function renderInventory() {
        if (!inventoryLoaded) return;
        var rows = filteredInventory();
        if (!rows.length) {
            setState(inventoryState, inventory.length ? "Nenhum software corresponde aos filtros." : "Ainda não há inventário de software recebido dos endpoints.", "empty");
            inventoryList.innerHTML = "";
            return;
        }
        setState(inventoryState, "", "");
        inventoryList.innerHTML = rows.map(function (item, index) {
            var versions = item.versions && item.versions.length ? item.versions.map(esc).join(", ") : "Versão não informada";
            return '<article class="software-collected-card" data-inventory-index="' + index + '">' +
                '<div class="software-collected-main"><div><h2>' + esc(item.name) + '</h2><p>' + esc(item.publisher || "Fabricante não informado") + '</p></div><button type="button" class="software-details-link" data-software-details="' + index + '">Ver endpoints ' + icon("arrow-up-right") + '</button></div>' +
                '<dl class="software-collected-facts"><div><dt>Versões</dt><dd>' + versions + '</dd></div><div><dt>Máquinas</dt><dd>' + esc(item.endpoint_count) + '</dd></div><div><dt>Inventário</dt><dd><span class="freshness-pill current">' + esc(item.current_count || 0) + ' atual(is)</span> <span class="freshness-pill stale">' + esc(item.stale_count || 0) + ' antigo(s)</span>' + (item.unknown_count ? ' <span class="freshness-pill unknown">' + esc(item.unknown_count) + ' desconhecido(s)</span>' : '') + '</dd></div></dl>' +
                '<p class="software-collected-date">Coletado em: ' + esc(date(item.latest_collected_at)) + ' · Recebido em: ' + esc(date(item.latest_seen_at)) + '</p>' +
                '</article>';
        }).join("");
        icons();
    }

    function statusLabel(value) {
        return { draft: "Rascunho", approved: "Aprovado", revoked: "Revogado", unknown: "Não verificada", valid: "Válida", invalid: "Inválida" }[value] || "Não informado";
    }

    function can(name) { return root.dataset["can" + name] === "true"; }
    function csrfToken() {
        var match = document.cookie.match(/(?:^|;\s*)csrftoken=([^;]+)/);
        return match ? decodeURIComponent(match[1]) : (root.querySelector("[name=csrfmiddlewaretoken]") || {}).value || "";
    }
    function packageUrl(id, suffix) {
        var marker = "00000000-0000-0000-0000-000000000000";
        return root.dataset.packageDetailBase.replace(marker, encodeURIComponent(id)) + (suffix || "");
    }
    function packageProfileUrl(id, approve) {
        var marker = "00000000-0000-0000-0000-000000000000";
        var base = approve ? root.dataset.packageProfileApproveBase : root.dataset.packageProfileBase;
        return base.replace(marker, encodeURIComponent(id));
    }
    var apiErrors = {
        forbidden: "Seu usuário não tem permissão para esta operação.", file_required: "Selecione um instalador MSI ou EXE.",
        friendly_name_required: "Informe o nome amigável.", upload_too_large: "O arquivo excede o limite configurado.",
        unsupported_extension: "Selecione um arquivo MSI ou EXE.", unrecognized_binary_format: "O conteúdo não corresponde a um MSI/EXE reconhecido.",
        extension_content_mismatch: "A extensão do arquivo não corresponde ao conteúdo detectado.", invalid_executable_header: "O arquivo EXE não contém um cabeçalho PE válido.",
        empty_file: "O arquivo está vazio.", invalid_filename: "O nome original do arquivo é inválido.",
        invalid_msi_compound_file: "O arquivo não contém a estrutura de diretório esperada para um MSI.",
        stale_revision: "Este cadastro foi alterado por outra pessoa. Recarregue os dados antes de tentar novamente.",
        package_in_use_by_active_operation: "O instalador está referenciado por uma operação ativa e não pode ser substituído ou removido.",
        package_has_operational_history: "O cadastro já foi utilizado. Ele não pode ser excluído; revogue-o para retirá-lo de uso.",
        artifact_missing_or_integrity_failed: "O arquivo armazenado está ausente ou não corresponde ao hash registrado.",
        artifact_approval_required: "Aprove primeiro o arquivo do pacote.", profile_binding_stale: "O perfil pertence a outro hash ou revisão do instalador; salve novamente antes de aprovar.",
        profile_or_artifact_integrity_failed: "O perfil ou o arquivo não passou na validação de integridade.",
        expected_version_required_or_invalid: "Informe uma versão esperada válida.", msi_arguments_not_supported: "MSI usa parâmetros fixos do Windows Installer neste incremento; argumentos arbitrários não são aceitos.",
        detection_product_code_mismatch: "A detecção MSI deve usar o ProductCode configurado na instalação.", registry_detection_invalid: "Informe a chave e o nome exato do aplicativo no registro de desinstalação.",
        detection_path_invalid: "O caminho de detecção deve estar sob uma pasta Windows permitida, sem traversal.", uninstall_path_invalid: "O caminho do desinstalador EXE deve usar uma raiz Windows permitida, sem traversal.",
        exe_profile_reapproval_required: "Atualize o perfil EXE para o contrato atual e aprove novamente antes de habilitar operações.",
        operation_profile_empty: "Configure pelo menos uma operação independente para salvar o perfil.",
        update_version_unverifiable: "A detecção por serviço não informa versão. Use Registro ou versão de arquivo para configurar atualização.",
        install_timeout_invalid: "O timeout de instalação deve estar entre 30 e 1800 segundos.", update_timeout_invalid: "O timeout de atualização deve estar entre 30 e 1800 segundos.", uninstall_timeout_invalid: "O timeout de desinstalação deve estar entre 30 e 1800 segundos.",
        service_detection_invalid: "Informe o nome interno do serviço e o estado esperado.", registry_detection_invalid: "Informe a chave exata HKLM, a visão e o nome do valor de versão.",
        agent_capability_unconfirmed: "O agente ainda não anuncia a capability versionada necessária; nenhum job foi criado.",
        software_dispatch_not_enabled: "Despacho de software permanece desabilitado neste incremento.",
        storage_unconfigured: "O armazenamento privado de instaladores ainda não foi configurado neste ambiente.", upload_limit_invalid: "O limite de upload não está configurado corretamente.",
        package_storage_failed: "Não foi possível armazenar o instalador. Tente novamente ou contate um administrador."
    };
    function requestJson(url, options) {
        options = options || {};
        var headers = Object.assign({ Accept: "application/json" }, options.headers || {});
        if (options.method && options.method !== "GET") headers["X-CSRFToken"] = csrfToken();
        var fetchOptions = Object.assign({}, options, { credentials: "same-origin", headers: headers });
        return fetch(url, fetchOptions).then(function (response) {
            return response.json().catch(function () { return {}; }).then(function (payload) {
                if (!response.ok) {
                    var error = new Error(apiErrors[payload.error] || (response.status === 403 ? "Acesso não autorizado." : "Falha ao processar a solicitação."));
                    error.status = response.status;
                    error.code = payload.error;
                    throw error;
                }
                return payload;
            });
        });
    }

    function renderPackages() {
        if (!packagesLoaded) return;
        var count = root.querySelector("[data-software-package-count]");
        count.textContent = packages.length + (packages.length === 1 ? " pacote" : " pacotes");
        if (!packages.length) {
            setState(packagesState, "Nenhum instalador cadastrado. Use Adicionar arquivo de instalação para criar o primeiro rascunho.", "empty");
            packageList.innerHTML = "";
            return;
        }
        setState(packagesState, "", "");
        packageList.innerHTML = packages.map(function (pkg) {
            var label = pkg.approval_status === "approved" ? "Aprovado" : pkg.approval_status === "revoked" ? "Revogado" : "Rascunho";
            var profileLabel = { unconfigured: 'Perfil não configurado', draft: 'Perfil pendente de aprovação', approved: 'Perfil aprovado' }[pkg.execution_profile_status] || 'Perfil desconhecido';
            return '<article class="software-upload-card"><div class="software-upload-card-top">' + icon(pkg.package_type === "msi" ? "package-check" : "package") + '<span class="software-package-type">' + esc(String(pkg.package_type || "").toUpperCase()) + '</span><span class="software-approval ' + esc(pkg.approval_status) + '">' + esc(label) + '</span></div>' +
                '<h3><a href="#" data-package-details="' + esc(pkg.id) + '">' + esc(pkg.friendly_name) + '</a></h3>' +
                '<p class="software-original-name" title="' + esc(pkg.original_filename) + '">' + esc(pkg.original_filename || "Nome original não informado") + '</p>' +
                '<div class="software-upload-card-meta"><span>Versão: ' + esc(pkg.version || "Não informada") + '</span><span>Tamanho: ' + esc(size(pkg.size_bytes)) + '</span><span>' + esc(profileLabel) + '</span></div>' +
                (!pkg.has_file ? '<p class="software-package-warning">Cadastro antigo sem arquivo associado; substitua/anexe um arquivo antes de aprovar.</p>' : '') + '</article>';
        }).join("");
        icons();
    }

    function loadInventory() {
        setState(inventoryState, "Carregando inventário real…", "loading");
        return requestJson(root.dataset.inventoryUrl).then(function (payload) {
            inventory = Array.isArray(payload.items) ? payload.items : [];
            summary = payload.summary || {};
            inventoryLoaded = true;
            summaryCards();
            renderInventory();
        }).catch(function (error) {
            setState(inventoryState, error.message + " Tente novamente.", "error");
        });
    }

    function loadPackages() {
        setState(packagesState, "Carregando cadastros…", "loading");
        return requestJson(root.dataset.packagesUrl).then(function (payload) {
            packages = Array.isArray(payload.items) ? payload.items : [];
            packagesLoaded = true;
            renderPackages();
        }).catch(function (error) {
            setState(packagesState, error.message + " Tente novamente.", "error");
        });
    }

    function field(label, value, extra) {
        return '<div class="software-detail-field"><dt>' + esc(label) + '</dt><dd' + (extra ? ' class="' + extra + '"' : '') + '>' + esc(value || "Não informado") + '</dd></div>';
    }

    function showDrawer(kicker, title, subtitle, content) {
        root.querySelector("[data-software-drawer-kicker]").textContent = kicker;
        root.querySelector("[data-software-drawer-title]").textContent = title;
        root.querySelector("[data-software-drawer-subtitle]").textContent = subtitle;
        root.querySelector("[data-software-drawer-body]").innerHTML = content;
        drawer.hidden = false;
        drawer.setAttribute("aria-hidden", "false");
        drawer.classList.add("is-open");
        drawerBackdrop.hidden = false;
        document.body.classList.add("drawer-open");
        root.querySelector("[data-software-drawer-close]").focus();
        icons();
    }

    function closeDrawer() {
        drawer.setAttribute("aria-hidden", "true");
        drawer.hidden = true;
        drawer.classList.remove("is-open");
        drawerBackdrop.hidden = true;
        document.body.classList.remove("drawer-open");
    }

    function showInventoryDetails(index) {
        var item = filteredInventory()[index];
        if (!item) return;
        var endpoints = item.endpoints || [];
        var body = '<section class="software-detail-section"><h3>Informações coletadas</h3><dl class="software-detail-grid">' +
            field("Fabricante", item.publisher) + field("Versões encontradas", (item.versions || []).join(", ")) + field("Máquinas", item.endpoint_count) + field("Coleta mais recente", date(item.latest_collected_at)) + field("Recebido mais recentemente", date(item.latest_seen_at)) + '</dl></section>' +
            '<section class="software-detail-section"><h3>Endpoints relacionados (' + endpoints.length + ')</h3>' +
            (endpoints.length ? '<ul class="software-endpoint-list">' + endpoints.map(function (endpoint) {
                return '<li><a href="/endpoints/' + encodeURIComponent(endpoint.id) + '/">' + esc(endpoint.hostname) + '</a><span>' + esc(endpoint.version || "Versão não informada") + '</span><span class="freshness-pill ' + esc(endpoint.inventory_state) + '">' + (endpoint.inventory_state === "stale" ? "Antigo" : endpoint.inventory_state === "current" ? "Atual" : "Desconhecido") + '</span></li>';
            }).join("") + '</ul>' : '<p>Não há endpoints relacionados.</p>') + '</section>';
        showDrawer("Software coletado", item.name, item.publisher || "Fabricante não informado", body);
    }

    function formField(label, name, value, type, required, hint) {
        return '<label class="software-form-field"><span>' + esc(label) + (required ? ' <b>*</b>' : '') + '</span>' +
            (type === 'textarea' ? '<textarea name="' + esc(name) + '" maxlength="10000">' + esc(value || '') + '</textarea>' :
                '<input name="' + esc(name) + '" type="' + esc(type || 'text') + '" value="' + esc(value || '') + '"' + (required ? ' required' : '') + (type === 'file' ? ' accept=".msi,.exe,application/octet-stream"' : '') + '>') +
            (hint ? '<small>' + esc(hint) + '</small>' : '') + '</label>';
    }

    function selectField(label, name, value, choices, hint) {
        return '<label class="software-form-field"><span>' + esc(label) + '</span><select name="' + esc(name) + '">' + choices.map(function (choice) {
            return '<option value="' + esc(choice[0]) + '"' + (choice[0] === value ? ' selected' : '') + '>' + esc(choice[1]) + '</option>';
        }).join('') + '</select>' + (hint ? '<small>' + esc(hint) + '</small>' : '') + '</label>';
    }

    function profileArray(profile, section, field) {
        var value = profile && profile[section] && profile[section][field];
        return JSON.stringify(Array.isArray(value) ? value : [], null, 2);
    }

    function exeOperationFields(label, name, profile, uninstall) {
        profile = profile || {};
        var prefix = name + '_';
        var path = uninstall ? formField('Executável de desinstalação (raiz de máquina)', prefix + 'executable_path', profile.executable_path || '', 'text', false,
            'Caminho explícito sob ProgramFiles, ProgramFiles(x86) ou ProgramData; nunca usa UninstallString do inventário.') : '';
        return '<fieldset class="software-exe-operation"><legend><label class="software-profile-toggle"><input type="checkbox" name="' + name + '_enabled"' + (profile.method ? ' checked' : '') + '> Configurar ' + esc(label) + '</label></legend>' +
            path + formField('Argumentos documentados pelo fabricante (array JSON)', prefix + 'arguments', JSON.stringify(profile.arguments || [], null, 2), 'textarea', false,
                'Cada elemento é passado como argumento separado; não existe flag silenciosa universal.') +
            formField('Timeout (segundos)', prefix + 'timeout_seconds', profile.timeout_seconds || 600, 'number', false, 'Limite validado: 30 a 1800 segundos.') +
            formField('Códigos de saída aceitos (array JSON)', prefix + 'accepted_exit_codes', JSON.stringify(profile.accepted_exit_codes || [0], null, 2), 'textarea', false,
                'O código não comprova sucesso; a detecção pós-execução também precisa confirmar o estado.') +
            formField('Códigos que indicam reboot pendente (array JSON)', prefix + 'reboot_exit_codes', JSON.stringify(profile.reboot_exit_codes || [], null, 2), 'textarea', false,
                'Devem ser subconjunto dos códigos aceitos. O agente nunca reinicia automaticamente.') + '</fieldset>';
    }

    function exeProfileForm(pkg, profile) {
        profile = profile && profile.schema_version === 2 ? profile : {};
        var detection = profile.detection || {};
        var install = profile.install || {};
        var update = profile.update || {};
        var uninstall = profile.uninstall || {};
        return '<form class="software-editor-form" data-package-form="profile">' +
            '<input type="hidden" name="revision" value="' + esc(pkg.revision) + '">' +
            exeOperationFields('instalação', 'install', install, false) +
            exeOperationFields('atualização', 'update', update, false) +
            exeOperationFields('desinstalação', 'uninstall', uninstall, true) +
            selectField('Método de detecção', 'detection_method', detection.method || 'registry_value', [
                ['registry_value', 'Valor exato no Registro HKLM'], ['file_version', 'Versão de arquivo'], ['windows_service', 'Serviço Windows']
            ], 'A regra é declarativa. Scripts e UninstallString não são executados.') +
            selectField('Visão do Registro', 'detection_view', detection.view || 'registry64', [['registry64', '64 bits'], ['registry32', '32 bits']], '') +
            formField('Chave exata HKLM', 'detection_registry_key', detection.key || '', 'text', false, 'Exemplo: SOFTWARE\\Vendor\\Product. Sem curingas.') +
            formField('Nome exato do valor de versão', 'detection_registry_value_name', detection.value_name || '', 'text', false, 'O valor precisa ser string e corresponder à versão esperada.') +
            formField('Caminho do arquivo de detecção', 'detection_path', detection.path || '', 'text', false, 'Use %ProgramFiles%, %ProgramFiles(x86)% ou %ProgramData%; sem traversal.') +
            selectField('Versão do arquivo', 'detection_version_field', detection.version_field || 'product_version', [['product_version', 'ProductVersion'], ['file_version', 'FileVersion']], '') +
            formField('Nome interno do serviço', 'detection_service_name', detection.service_name || '', 'text', false, 'Nome SCM exato; não use nome de exibição.') +
            selectField('Estado esperado do serviço', 'detection_service_status', detection.expected_status || 'any', [['any', 'Presente em estado estável'], ['running', 'Em execução'], ['stopped', 'Parado']], '') +
            formField('Versão esperada', 'expected_version', profile.expected_version || pkg.version || '', 'text', false,
                'Obrigatória para Registro/arquivo em instalação ou atualização; opcional para presença de serviço.') +
            selectField('Comportamento se exigir reinicialização', 'reboot_behavior', profile.reboot_behavior || 'defer_to_policy', [['report_only', 'Registrar como pendente; não reiniciar'], ['defer_to_policy', 'Delegar à futura política de reboot'], ['require_operator', 'Aguardar decisão do operador']], '') +
            formField('Motivo da alteração', 'reason', '', 'text', true, 'Obrigatório para auditoria e nova aprovação.') +
            '<p class="software-package-warning">Salvar cria um novo rascunho e invalida a aprovação atual. Aprove o perfil revisado antes de qualquer operação futura.</p>' +
            '<div class="software-form-message" data-form-message role="status"></div><button class="refresh-button" type="submit">Salvar perfil como rascunho</button></form>';
    }

    function executionProfileSection(pkg) {
        var profile = pkg.execution_profile || {};
        var msi = pkg.package_type === 'msi';
        var state = pkg.execution_profile_status || 'unconfigured';
        var stateText = { unconfigured: 'Não configurado', draft: 'Rascunho — requer aprovação do perfil', approved: 'Aprovado' }[state] || 'Desconhecido';
        var readiness = pkg.operation_readiness || {};
        var operations = [['install', 'Instalação'], ['update', 'Atualização'], ['uninstall', 'Desinstalação']].map(function (item) {
            var configured = !!readiness[item[0]];
            return '<li><strong>' + item[1] + ':</strong> ' + (configured ? 'Perfil aprovado' : 'Não configurada ou requer aprovação') + '</li>';
        }).join('');
        var body = '<section class="software-detail-section software-execution-profile"><h3>Configuração de execução</h3>' +
            '<p class="software-profile-state" data-profile-state="' + esc(state) + '">' + esc(stateText) +
            (pkg.profile_binding_current === false && pkg.execution_profile_sha256 ? ' · Perfil vinculado a outro arquivo/revisão' : '') + '</p>' +
            '<dl class="software-profile-operations"><div><dt>Operações preparadas</dt><dd><ul>' + operations + '</ul></dd></div></dl>' +
            '<p class="software-package-warning">Nenhuma operação pode ser despachada nesta etapa. Mesmo um perfil aprovado não instala software. A instalação exige capability compatível do agente, autorização e uma etapa posterior.</p>';
        if (!pkg.has_file) {
            body += '<p class="software-readonly-note">Anexe primeiro um instalador válido. O perfil deve ser vinculado ao hash e à revisão do binário atual.</p>';
        } else if (can('ConfigureProfile')) {
            if (!msi) {
                body += exeProfileForm(pkg, profile);
            } else {
            body += '<form class="software-editor-form" data-package-form="profile">' +
                '<input type="hidden" name="revision" value="' + esc(pkg.revision) + '">' +
                selectField('Método de instalação', 'install_method', msi ? 'msi' : 'exe', [[msi ? 'msi' : 'exe', msi ? 'Windows Installer (MSI)' : 'Executável EXE do fornecedor']], 'O método é fixo pelo tipo do arquivo.') +
                (msi ? formField('ProductCode MSI', 'product_code', (profile.install || {}).product_code || '', 'text', true, 'GUID do produto; os parâmetros do msiexec serão controlados pelo agente.') :
                    formField('Argumentos silenciosos EXE (JSON array)', 'install_arguments', profileArray(profile, 'install', 'arguments'), 'textarea', true, 'Use somente argumentos documentados pelo fornecedor. Exemplo: ["/quiet"]. Cada item é um argumento, não uma linha de shell.')) +
                (msi ? '<input type="hidden" name="install_arguments" value="[]">' : '') +
                '<label class="software-profile-toggle"><input type="checkbox" name="update_enabled"' + (profile.update ? ' checked' : '') + '> Configurar método de atualização</label>' +
                (msi ? selectField('Método de atualização', 'update_method', (profile.update || {}).method || 'msi_upgrade', [['msi_upgrade', 'MSI upgrade controlado']], '') :
                    selectField('Método de atualização', 'update_method', (profile.update || {}).method || 'vendor_exe', [['vendor_exe', 'Executável e argumentos do fornecedor']], 'Não há parâmetros universais para EXE.')) +
                (msi ? '<input type="hidden" name="update_arguments" value="[]">' : formField('Argumentos de atualização EXE (JSON array)', 'update_arguments', profileArray(profile, 'update', 'arguments'), 'textarea', false, 'Cada item será passado como argumento separado; sem shell.')) +
                '<label class="software-profile-toggle"><input type="checkbox" name="uninstall_enabled"' + (profile.uninstall ? ' checked' : '') + '> Configurar método de desinstalação</label>' +
                (msi ? formField('ProductCode MSI para remover', 'uninstall_product_code', (profile.uninstall || {}).product_code || (profile.install || {}).product_code || '', 'text', false, 'Não serão executadas strings UninstallString do inventário.') :
                    formField('Executável do fornecedor (raiz permitida)', 'uninstall_executable_path', (profile.uninstall || {}).executable_path || '', 'text', false, 'Exemplo: %ProgramFiles%\\Vendor\\App\\uninstall.exe. A execução futura exigirá resolver e validar o caminho localmente.')) +
                (!msi ? formField('Argumentos de desinstalação EXE (JSON array)', 'uninstall_arguments', profileArray(profile, 'uninstall', 'arguments'), 'textarea', false, 'Documentados pelo fornecedor; não execute strings coletadas do inventário.') : '<input type="hidden" name="uninstall_arguments" value="[]">') +
                selectField('Regra de detecção', 'detection_method', (profile.detection || {}).method || (msi ? 'msi_product_code' : 'registry_uninstall'), msi ? [['msi_product_code', 'ProductCode MSI e versão']] : [['registry_uninstall', 'Chave exata de desinstalação e nome'], ['file_version', 'Versão de arquivo sob pasta Windows']], '') +
                (msi ? '<input type="hidden" name="detection_product_code" value="' + esc((profile.detection || {}).product_code || (profile.install || {}).product_code || '') + '">' :
                    formField('Chave do registro de desinstalação', 'detection_registry_key', (profile.detection || {}).registry_key || '', 'text', false, 'Subchave específica em SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\Uninstall.') +
                    formField('Nome exato no registro', 'detection_display_name', (profile.detection || {}).display_name || '', 'text', false, '') +
                    formField('Caminho para detecção de versão de arquivo', 'detection_path', (profile.detection || {}).path || '', 'text', false, 'Use %ProgramFiles%, %ProgramFiles(x86)%, %ProgramData% ou %LOCALAPPDATA%.')) +
                formField('Versão esperada', 'expected_version', profile.expected_version || pkg.version || '', 'text', true, 'A conclusão futura dependerá da detecção real, não apenas do exit code.') +
                selectField('Comportamento se exigir reinicialização', 'reboot_behavior', profile.reboot_behavior || 'defer_to_policy', [['report_only', 'Registrar como pendente; não reiniciar'], ['defer_to_policy', 'Delegar a futura política de reboot'], ['require_operator', 'Aguardar decisão do operador']], 'Esta configuração não executa reboot nesta etapa.') +
                formField('Motivo da alteração', 'reason', '', 'text', true, 'Obrigatório para auditoria.') +
                '<div class="software-form-message" data-form-message role="status"></div><button class="refresh-button" type="submit">Salvar perfil como rascunho</button></form>';
            }
        } else {
            body += '<p class="software-readonly-note">Seu usuário pode consultar o perfil, mas não configurá-lo.</p>';
        }
        if (pkg.has_file && can('ApproveProfile') && state !== 'approved' && pkg.execution_profile_sha256) {
            body += '<button type="button" class="refresh-button" data-package-action="approve-profile">Aprovar perfil operacional</button>';
        }
        body += '<p class="software-readonly-note">Aprovação do arquivo e aprovação do perfil são independentes. Alterar perfil ou substituir o binário invalida a aprovação operacional.</p></section>';
        return body;
    }

    function openCreateDrawer() {
        var max = Number(root.dataset.packageMaxUploadBytes || 0);
        var body = '<form class="software-editor-form" data-package-form="create" enctype="multipart/form-data">' +
            formField('Nome amigável', 'friendly_name', '', 'text', true, 'Identificação exibida na Central de Software.') +
            formField('Arquivo MSI ou EXE', 'file', '', 'file', true, 'Limite por arquivo: ' + size(max) + '. Tipo e hash são verificados no servidor.') +
            formField('Versão', 'version', '', 'text', false, 'Opcional; não executamos o instalador para detectar metadados.') +
            formField('Descrição', 'description', '', 'textarea', false, '') +
            '<div class="software-form-message" data-form-message role="status"></div><div class="software-form-actions"><button class="refresh-button" type="submit">Enviar e salvar como rascunho</button></div>' +
            '<p class="software-readonly-note">O upload não instala o software. A aprovação manual não equivale a análise antimalware nem autoriza distribuição.</p></form>';
        showDrawer('Novo instalador', 'Adicionar arquivo de instalação', 'Cadastro privado · aprovação pendente', body);
    }

    function packageEditor(pkg, audit) {
        var editable = can('Edit');
        var body = '<section class="software-detail-section"><h3>Arquivo e metadados</h3><dl class="software-detail-grid">' +
            field('Nome amigável', pkg.friendly_name) + field('Nome original', pkg.original_filename) + field('Tipo', String(pkg.package_type || '').toUpperCase()) +
            field('Versão', pkg.version) + field('Descrição', pkg.description) + field('Tamanho', size(pkg.size_bytes)) + field('SHA-256 atual', pkg.sha256, 'software-mono') +
            field('Formato detectado', pkg.metadata && pkg.metadata.detected_format) + field('Assinatura digital', statusLabel(pkg.signature_status)) +
            field('Aprovação', statusLabel(pkg.approval_status)) + field('Enviado por', pkg.uploaded_by) + field('Enviado em', date(pkg.artifact_uploaded_at)) +
            field('Criado por', pkg.created_by) + field('Criado em', date(pkg.created_at)) + field('Última alteração', date(pkg.updated_at)) +
            field('Revisão', pkg.revision) + '</dl>' + (pkg.has_file && can('Download') ? '<p><a class="software-download-link" href="' + esc(pkg.download_url) + '">Baixar arquivo (acesso autenticado)</a></p>' : '') +
            (!pkg.has_file ? '<p class="software-package-warning">Este registro não tem binário associado e não pode ser aprovado.</p>' : '') +
            '<p class="software-readonly-note">A assinatura permanece não verificada automaticamente. A aprovação é uma decisão operacional, não uma análise antimalware.</p></section>';
        if (editable) {
            body += '<section class="software-detail-section"><h3>Editar cadastro</h3><form class="software-editor-form" data-package-form="update">' +
                '<input type="hidden" name="revision" value="' + esc(pkg.revision) + '">' +
                formField('Nome amigável', 'friendly_name', pkg.friendly_name, 'text', true, '') +
                formField('Versão', 'version', pkg.version, 'text', false, '') +
                formField('Descrição', 'description', pkg.description, 'textarea', false, '') +
                '<div class="software-form-message" data-form-message role="status"></div><button class="refresh-button" type="submit">Salvar metadados</button></form></section>' +
                '<section class="software-detail-section"><h3>' + (pkg.has_file ? 'Substituir instalador' : 'Anexar instalador') + '</h3><form class="software-editor-form" data-package-form="replace" enctype="multipart/form-data">' +
                '<input type="hidden" name="revision" value="' + esc(pkg.revision) + '">' +
                formField('Novo arquivo MSI ou EXE', 'file', '', 'file', true, 'A aprovação atual será invalidada e o pacote retornará a Rascunho.') +
                formField('Motivo da substituição', 'reason', '', 'text', true, 'Obrigatório para auditoria.') +
                '<div class="software-form-message" data-form-message role="status"></div><button class="refresh-button" type="submit"' + (pkg.active_operations ? ' disabled title="Há operação ativa usando este pacote"' : '') + '>Substituir arquivo</button></form></section>';
        }
        body += executionProfileSection(pkg);
        body += '<section class="software-detail-section"><h3>Governança</h3><div class="software-governance-actions">';
        if (can('Approve') && pkg.approval_status !== 'approved') body += '<button type="button" class="refresh-button" data-package-action="approve">Aprovar</button>';
        if (can('Revoke') && pkg.approval_status !== 'revoked') body += '<button type="button" class="refresh-button" data-package-action="revoke">Revogar</button>';
        if (can('Delete') && !pkg.used && !pkg.active_operations) body += '<button type="button" class="software-danger-button" data-package-action="delete">Excluir cadastro</button>';
        if (!pkg.used && pkg.active_operations) body += '<p class="software-package-warning">Uma operação está usando o arquivo; substituição e exclusão ficam bloqueadas.</p>';
        if (pkg.used) body += '<p class="software-package-warning">Há histórico operacional. O cadastro deve ser revogado para retirada de uso, sem apagar o histórico.</p>';
        body += '</div><label class="software-form-field"><span>Motivo da ação</span><input type="text" maxlength="500" data-package-reason placeholder="Informe o motivo para aprovação, revogação ou exclusão"></label><div class="software-form-message" data-package-action-message role="status"></div></section>';
        body += '<section class="software-detail-section"><h3>Auditoria recente</h3>' + ((audit || []).length ? '<ol class="software-audit-list">' + audit.map(function (event) {
            return '<li><strong>' + esc(event.title) + '</strong><span>' + esc(event.actor || 'Operador removido') + ' · ' + esc(date(event.created_at)) + '</span>' + (event.reason ? '<p>' + esc(event.reason) + '</p>' : '') +
                (event.before && event.before.sha256 ? '<small>Hash anterior: ' + esc(event.before.sha256) + '</small>' : '') +
                (event.after && event.after.sha256 ? '<small>Hash atual: ' + esc(event.after.sha256) + '</small>' : '') + '</li>';
        }).join('') + '</ol>' : '<p class="software-readonly-note">Sem eventos de auditoria para este cadastro.</p>') + '</section>';
        return body;
    }

    function showPackageDetails(id) {
        setState(packagesState, '', '');
        return requestJson(packageUrl(id)).then(function (payload) {
            var pkg = payload.item;
            showDrawer('Cadastro de instalador', pkg.friendly_name, pkg.original_filename || String(pkg.package_type).toUpperCase(), packageEditor(pkg, payload.audit));
            drawer.dataset.packageId = pkg.id;
        }).catch(function (error) {
            showDrawer('Cadastro de instalador', 'Não foi possível abrir', '', '<p class="software-data-state" data-state="error">' + esc(error.message) + '</p>');
        });
    }

    function setFormMessage(form, message, kind) {
        var node = form.querySelector('[data-form-message]');
        if (node) { node.textContent = message; node.dataset.state = kind || 'info'; }
    }

    function parseArguments(form, name, optional) {
        var raw = (new FormData(form).get(name) || '').trim();
        if (!raw && optional) return [];
        try {
            var parsed = JSON.parse(raw);
            if (!Array.isArray(parsed) || parsed.some(function (value) { return typeof value !== 'string'; })) throw new Error('invalid');
            return parsed;
        } catch (_) {
            throw new Error('Informe os argumentos como um array JSON de strings, por exemplo ["/quiet"].');
        }
    }

    function parseNumberArray(form, name, required) {
        var raw = (new FormData(form).get(name) || '').trim();
        if (!raw && !required) return [];
        try {
            var values = JSON.parse(raw);
            if (!Array.isArray(values) || values.some(function (value) { return !Number.isInteger(value) || value < 0 || value > 65535; })) throw new Error('invalid');
            return values;
        } catch (_) {
            throw new Error('Informe códigos de saída como array JSON de inteiros.');
        }
    }

    function executionProfilePayload(form, pkg) {
        var data = new FormData(form);
        var msi = pkg.package_type === 'msi';
        if (!msi) {
            function exeOperation(name, method, uninstall) {
                if (!data.get(name + '_enabled')) return null;
                var timeout = Number(data.get(name + '_timeout_seconds'));
                if (!Number.isInteger(timeout) || timeout < 30 || timeout > 1800) throw new Error('O timeout de cada operação deve estar entre 30 e 1800 segundos.');
                var result = {
                    method: method,
                    arguments: parseArguments(form, name + '_arguments', true),
                    timeout_seconds: timeout,
                    accepted_exit_codes: parseNumberArray(form, name + '_accepted_exit_codes', true),
                    reboot_exit_codes: parseNumberArray(form, name + '_reboot_exit_codes', false)
                };
                if (uninstall) result.executable_path = String(data.get('uninstall_executable_path') || '').trim();
                return result;
            }
            var detectionMethod = String(data.get('detection_method') || '');
            if (data.get('update_enabled') && detectionMethod === 'windows_service') {
                throw new Error('A detecção por serviço confirma presença/estado, mas não comprova a versão de uma atualização.');
            }
            var detection = detectionMethod === 'registry_value' ? {
                method: detectionMethod, hive: 'HKLM', view: String(data.get('detection_view') || ''),
                key: String(data.get('detection_registry_key') || '').trim(),
                value_name: String(data.get('detection_registry_value_name') || '').trim()
            } : detectionMethod === 'file_version' ? {
                method: detectionMethod, path: String(data.get('detection_path') || '').trim(),
                version_field: String(data.get('detection_version_field') || '')
            } : {
                method: 'windows_service', service_name: String(data.get('detection_service_name') || '').trim(),
                expected_status: String(data.get('detection_service_status') || 'any')
            };
            return {
                revision: Number(data.get('revision')), reason: String(data.get('reason') || '').trim(),
                execution_profile: {
                    schema_version: 2,
                    install: exeOperation('install', 'exe', false),
                    update: exeOperation('update', 'vendor_exe', false),
                    uninstall: exeOperation('uninstall', 'vendor_exe', true),
                    detection: detection,
                    expected_version: String(data.get('expected_version') || '').trim(),
                    reboot_behavior: String(data.get('reboot_behavior') || 'defer_to_policy')
                }
            };
        }
        var product = String(data.get('product_code') || '').trim();
        var install = msi ? { method: 'msi', arguments: [], product_code: product } :
            { method: 'exe', arguments: parseArguments(form, 'install_arguments', false) };
        var update = data.get('update_enabled') ? {
            method: msi ? 'msi_upgrade' : 'vendor_exe',
            arguments: msi ? [] : parseArguments(form, 'update_arguments', true)
        } : null;
        var uninstall = null;
        if (data.get('uninstall_enabled')) {
            uninstall = msi ? { method: 'msi_product_code', product_code: String(data.get('uninstall_product_code') || '').trim() } : {
                method: 'vendor_exe', executable_path: String(data.get('uninstall_executable_path') || '').trim(),
                arguments: parseArguments(form, 'uninstall_arguments', true)
            };
        }
        var detectionMethod = String(data.get('detection_method') || '');
        var detection = msi ? { method: 'msi_product_code', product_code: String(data.get('detection_product_code') || product).trim() } :
            detectionMethod === 'registry_uninstall' ? {
                method: detectionMethod, registry_key: String(data.get('detection_registry_key') || '').trim(),
                display_name: String(data.get('detection_display_name') || '').trim()
            } : { method: 'file_version', path: String(data.get('detection_path') || '').trim() };
        return {
            revision: Number(data.get('revision')),
            reason: String(data.get('reason') || '').trim(),
            execution_profile: {
                schema_version: 1, install: install, update: update, uninstall: uninstall,
                detection: detection, expected_version: String(data.get('expected_version') || '').trim(),
                reboot_behavior: String(data.get('reboot_behavior') || 'defer_to_policy')
            }
        };
    }

    function reloadPackageDrawer(id) {
        return loadPackages().then(function () { return showPackageDetails(id); });
    }

    function handlePackageForm(event) {
        var form = event.target.closest('[data-package-form]');
        if (!form) return;
        event.preventDefault();
        var kind = form.dataset.packageForm;
        var submit = form.querySelector('[type=submit]');
        var fileInput = form.querySelector('input[type=file]');
        var max = Number(root.dataset.packageMaxUploadBytes || 0);
        if (fileInput && fileInput.files[0] && fileInput.files[0].size > max) {
            setFormMessage(form, 'O arquivo excede o limite de ' + size(max) + '.', 'error');
            return;
        }
        if (submit) submit.disabled = true;
        var operation;
        var id = drawer.dataset.packageId;
        if (kind === 'create') {
            operation = requestJson(root.dataset.packageCreateUrl, { method: 'POST', body: new FormData(form) });
        } else if (kind === 'replace') {
            operation = requestJson(packageUrl(id, 'replace/'), { method: 'POST', body: new FormData(form) });
        } else if (kind === 'profile') {
            var pkg = packages.find(function (item) { return item.id === id; });
            try {
                if (!pkg) throw new Error('Recarregue o cadastro antes de configurar o perfil.');
                operation = requestJson(packageProfileUrl(id, false), { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(executionProfilePayload(form, pkg)) });
            } catch (error) {
                setFormMessage(form, error.message, 'error');
                if (submit) submit.disabled = false;
                return;
            }
        } else {
            var payload = Object.fromEntries(new FormData(form).entries());
            payload.revision = Number(payload.revision);
            operation = requestJson(packageUrl(id, 'update/'), { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) });
        }
        operation.then(function (payload) {
            var saved = kind === 'create' ? payload.item : payload.item;
            return loadPackages().then(function () {
                return showPackageDetails(saved.id);
            });
        }).catch(function (error) {
            setFormMessage(form, error.message, 'error');
        }).finally(function () { if (submit && submit.isConnected) submit.disabled = false; });
    }

    function handlePackageAction(event) {
        var button = event.target.closest('[data-package-action]');
        if (!button) return;
        var action = button.dataset.packageAction;
        var id = drawer.dataset.packageId;
        var pkg = packages.find(function (item) { return item.id === id; });
        var message = root.querySelector('[data-package-action-message]');
        if (!pkg) return;
        if (action === 'delete' && !window.confirm('Excluir definitivamente este cadastro não utilizado e seu arquivo privado? O evento de auditoria será preservado.')) return;
        var reason = (root.querySelector('[data-package-reason]') || {}).value || '';
        if (!reason.trim()) { if (message) { message.textContent = 'Informe o motivo antes de continuar.'; message.dataset.state = 'error'; } return; }
        button.disabled = true;
        var payload = { revision: pkg.revision, reason: reason.trim() };
        var url = action === 'approve-profile' ? packageProfileUrl(id, true) : packageUrl(id, action === 'delete' ? 'delete/' : action + '/');
        requestJson(url, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) }).then(function () {
            if (action === 'delete') { closeDrawer(); return loadPackages(); }
            return reloadPackageDrawer(id);
        }).catch(function (error) {
            if (message) { message.textContent = error.message; message.dataset.state = 'error'; }
            button.disabled = false;
        });
    }

    root.querySelectorAll("[data-software-tab]").forEach(function (button) {
        button.addEventListener("click", function () {
            activeTab = button.dataset.softwareTab;
            root.querySelectorAll("[data-software-tab]").forEach(function (tab) {
                var selected = tab === button;
                tab.classList.toggle("is-active", selected);
                tab.setAttribute("aria-selected", selected ? "true" : "false");
            });
            root.querySelectorAll("[data-software-panel]").forEach(function (panel) {
                var selected = panel.dataset.softwarePanel === activeTab;
                panel.hidden = !selected;
                panel.classList.toggle("is-active", selected);
            });
            if (activeTab === "uploads" && !packagesLoaded) loadPackages();
        });
    });

    root.querySelector("[data-software-search]").addEventListener("input", renderInventory);
    root.querySelector("[data-software-freshness]").addEventListener("change", renderInventory);
    root.querySelector("[data-software-search-clear]").addEventListener("click", function () {
        root.querySelector("[data-software-search]").value = "";
        root.querySelector("[data-software-freshness]").value = "all";
        renderInventory();
    });
    root.querySelector("[data-software-refresh]").addEventListener("click", function () {
        loadInventory();
        if (activeTab === "uploads" || packagesLoaded) loadPackages();
    });
    inventoryList.addEventListener("click", function (event) {
        var button = event.target.closest("[data-software-details]");
        if (button) showInventoryDetails(Number(button.dataset.softwareDetails));
    });
    packageList.addEventListener("click", function (event) {
        var link = event.target.closest("[data-package-details]");
        if (!link) return;
        event.preventDefault();
        showPackageDetails(link.dataset.packageDetails);
    });
    root.querySelector('[data-software-add-upload]').addEventListener('click', function () {
        if (can('Upload')) openCreateDrawer();
    });
    root.querySelector('[data-software-drawer-body]').addEventListener('submit', handlePackageForm);
    root.querySelector('[data-software-drawer-body]').addEventListener('click', handlePackageAction);
    root.querySelector("[data-software-drawer-close]").addEventListener("click", closeDrawer);
    drawerBackdrop.addEventListener("click", closeDrawer);
    document.addEventListener("keydown", function (event) { if (event.key === "Escape" && !drawer.hidden) closeDrawer(); });

    loadInventory();
})();
