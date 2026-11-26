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

    function renderPackages() {
        if (!packagesLoaded) return;
        var count = root.querySelector("[data-software-package-count]");
        count.textContent = packages.length + (packages.length === 1 ? " pacote" : " pacotes");
        if (!packages.length) {
            setState(packagesState, "Nenhum instalador cadastrado. Os arquivos poderão ser adicionados no incremento RC49-03B.", "empty");
            packageList.innerHTML = "";
            return;
        }
        setState(packagesState, "", "");
        packageList.innerHTML = packages.map(function (pkg) {
            var label = pkg.approval_status === "approved" ? "Aprovado" : pkg.approval_status === "revoked" ? "Revogado" : "Rascunho";
            return '<article class="software-upload-card"><div class="software-upload-card-top">' + icon(pkg.package_type === "msi" ? "package-check" : "package") + '<span class="software-package-type">' + esc(String(pkg.package_type || "").toUpperCase()) + '</span><span class="software-approval ' + esc(pkg.approval_status) + '">' + esc(label) + '</span></div>' +
                '<h3><a href="#" data-package-details="' + esc(pkg.id) + '">' + esc(pkg.friendly_name) + '</a></h3>' +
                '<p class="software-original-name" title="' + esc(pkg.original_filename) + '">' + esc(pkg.original_filename || "Nome original não informado") + '</p>' +
                '<div class="software-upload-card-meta"><span>Versão: ' + esc(pkg.version || "Não informada") + '</span><span>Tamanho: ' + esc(size(pkg.size_bytes)) + '</span></div></article>';
        }).join("");
        icons();
    }

    function requestJson(url) {
        return fetch(url, { credentials: "same-origin", headers: { Accept: "application/json" } }).then(function (response) {
            if (!response.ok) throw new Error(response.status === 403 ? "Acesso não autorizado." : "Falha ao consultar dados.");
            return response.json();
        });
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

    function showPackageDetails(id) {
        var pkg = packages.find(function (item) { return item.id === id; });
        if (!pkg) return;
        var type = String(pkg.package_type || "").toUpperCase();
        var body = '<section class="software-detail-section"><h3>Arquivo cadastrado</h3><dl class="software-detail-grid">' +
            field("Nome amigável", pkg.friendly_name) + field("Nome original", pkg.original_filename) + field("Tipo", type) +
            field("Versão", pkg.version) + field("Tamanho", size(pkg.size_bytes)) + field("SHA-256", pkg.sha256, "software-mono") +
            field("Assinatura", statusLabel(pkg.signature_status)) + field("Signatário", pkg.signer) +
            field("Aprovação", statusLabel(pkg.approval_status)) + field("Aprovado por", pkg.approved_by) +
            field("Aprovado em", date(pkg.approved_at)) + field("Revogado por", pkg.revoked_by) +
            field("Revogado em", date(pkg.revoked_at)) + field("Criado por", pkg.created_by) +
            field("Criado em", date(pkg.created_at)) + field("Atualizado em", date(pkg.updated_at)) + '</dl></section>' +
            '<p class="software-readonly-note">Consulta somente leitura. Upload, substituição e aprovação são escopos posteriores.</p>';
        showDrawer("Upload cadastrado", pkg.friendly_name, pkg.original_filename || type, body);
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
    root.querySelector("[data-software-drawer-close]").addEventListener("click", closeDrawer);
    drawerBackdrop.addEventListener("click", closeDrawer);
    document.addEventListener("keydown", function (event) { if (event.key === "Escape" && !drawer.hidden) closeDrawer(); });

    loadInventory();
})();
