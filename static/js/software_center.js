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
            return '<article class="software-upload-card"><div class="software-upload-card-top">' + icon(pkg.package_type === "msi" ? "package-check" : "package") + '<span class="software-package-type">' + esc(String(pkg.package_type || "").toUpperCase()) + '</span><span class="software-approval ' + esc(pkg.approval_status) + '">' + esc(label) + '</span></div>' +
                '<h3><a href="#" data-package-details="' + esc(pkg.id) + '">' + esc(pkg.friendly_name) + '</a></h3>' +
                '<p class="software-original-name" title="' + esc(pkg.original_filename) + '">' + esc(pkg.original_filename || "Nome original não informado") + '</p>' +
                '<div class="software-upload-card-meta"><span>Versão: ' + esc(pkg.version || "Não informada") + '</span><span>Tamanho: ' + esc(size(pkg.size_bytes)) + '</span></div>' +
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
        requestJson(packageUrl(id)).then(function (payload) {
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

    function reloadPackageDrawer(id) {
        return loadPackages().then(function () { showPackageDetails(id); });
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
        } else {
            var payload = Object.fromEntries(new FormData(form).entries());
            payload.revision = Number(payload.revision);
            operation = requestJson(packageUrl(id, 'update/'), { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) });
        }
        operation.then(function (payload) {
            var saved = kind === 'create' ? payload.item : payload.item;
            return loadPackages().then(function () {
                showPackageDetails(saved.id);
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
        var url = packageUrl(id, action === 'delete' ? 'delete/' : action + '/');
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
