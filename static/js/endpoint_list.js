(function () {
    const root = document.querySelector("[data-endpoint-ops]");
    const toast = document.querySelector("[data-endpoint-toast]");
    const operational = window.NightOwlOperational;

    function showToast(message) {
        if (operational && typeof operational.showToast === "function") {
            operational.showToast(message, { target: toast, timeout: 2600 });
            return;
        }
        if (!toast) return;
        toast.textContent = message || "Acao registrada.";
        toast.hidden = false;
        toast.classList.add("is-visible");
        window.clearTimeout(toast.__endpointToastTimer);
        toast.__endpointToastTimer = window.setTimeout(function () {
            toast.classList.remove("is-visible");
            toast.hidden = true;
        }, 2600);
    }

    function rowFromElement(element) {
        return element ? element.closest("[data-endpoint-row]") : null;
    }

    function runEndpointAction(action, row, message) {
        const endpoint = row ? row.dataset.endpoint : "Endpoints";
        if (operational && typeof operational.runAction === "function") {
            operational.runAction(action, {
                endpoint,
                card: row,
                toastOptions: { target: toast, timeout: 2800 },
                description: message || `${action} solicitado para ${endpoint}.`
            });
            return;
        }
        showToast(message || `${action} solicitado para ${endpoint}.`);
    }

    async function copyValue(value, label) {
        if (!value) {
            showToast(`${label || "Valor"} indisponivel.`);
            return;
        }
        try {
            await navigator.clipboard.writeText(value);
            showToast(`${label || "Valor"} copiado.`);
        } catch (error) {
            showToast("Nao foi possivel copiar.");
        }
    }

    if (root) {
        const viewButtons = Array.from(root.querySelectorAll("[data-endpoint-view]"));
        const panels = Array.from(root.querySelectorAll("[data-endpoint-view-panel]"));
        const bulkBar = root.querySelector("[data-endpoint-bulk-bar]");
        const selectedCount = root.querySelector("[data-endpoint-selected-count]");
        const selectionHidden = root.querySelector("[data-endpoint-selection-hidden]");
        const selectAll = root.querySelector("[data-endpoint-select-all]");
        const pagination = root.querySelector("[data-endpoint-pagination]");
        const pageSizeControl = root.querySelector("[data-endpoint-page-size]");
        const pageSummary = root.querySelector("[data-endpoint-pagination-summary]");
        const pageCurrent = root.querySelector("[data-endpoint-page-current]");
        const visibleCount = document.querySelector("[data-endpoint-visible-count]");
        const filterForm = document.querySelector("[data-endpoint-filter-form]");
        const serverPaginated = root.dataset.endpointServerPaginated === "true";
        const selectedEndpoints = new Map();
        let pageSize = Number(pageSizeControl?.value) || Number(root.dataset.endpointPageSizeValue) || 10;
        let currentPage = Number(root.dataset.endpointPage);
        if (!Number.isFinite(currentPage)) currentPage = 1;
        let resultCount = Number(root.dataset.endpointResultCount) || 0;
        const requestedView = new URL(window.location.href).searchParams.get("view");
        let activeView = ["table", "cards", "health"].includes(requestedView) ? requestedView : "table";
        let requestController = null;
        let requestSequence = 0;
        let searchTimer = null;
        let localPage = 1;

        function pageRows() {
            if (serverPaginated) {
                const panel = panels.find(item => item.dataset.endpointViewPanel === activeView);
                return panel ? Array.from(panel.querySelectorAll("[data-endpoint-row]")) : [];
            }
            return Array.from(root.querySelectorAll("tr[data-endpoint-row]")).filter(row => !row.hidden);
        }

        function displayCount() {
            return serverPaginated ? resultCount : root.querySelectorAll("tr[data-endpoint-row]").length;
        }

        function notifySelectionChanged() {
            document.dispatchEvent(new CustomEvent("nightowl:endpoint-selection-change", {
                detail: { ids: Array.from(selectedEndpoints.keys()) }
            }));
        }

        function updateBulkBar() {
            const rows = pageRows();
            const pageIds = new Set();
            rows.forEach(function (row) {
                const input = row.querySelector("[data-endpoint-select]");
                const id = row.dataset.endpointId || input?.dataset.fleetEndpointId;
                if (!id) return;
                pageIds.add(id);
                if (input) input.checked = selectedEndpoints.has(id);
                row.classList.toggle("is-selected", selectedEndpoints.has(id));
                if (selectedEndpoints.has(id)) selectedEndpoints.set(id, row.dataset.endpoint || selectedEndpoints.get(id) || id);
            });
            if (bulkBar) bulkBar.hidden = selectedEndpoints.size === 0;
            if (selectedCount) selectedCount.textContent = String(selectedEndpoints.size);
            if (selectionHidden) {
                const outsidePage = Array.from(selectedEndpoints.keys()).filter(id => !pageIds.has(id)).length;
                selectionHidden.hidden = outsidePage === 0;
                selectionHidden.textContent = outsidePage ? `${outsidePage} fora da página atual` : "";
            }
            if (selectAll) {
                const checks = rows.map(row => row.querySelector("[data-endpoint-select]")).filter(Boolean);
                const checked = checks.filter(input => selectedEndpoints.has(input.dataset.fleetEndpointId)).length;
                selectAll.checked = checks.length > 0 && checked === checks.length;
                selectAll.indeterminate = checked > 0 && checked < checks.length;
            }
        }

        function updatePagination(data) {
            currentPage = Number(data.page) || 0;
            pageSize = Number(data.page_size) || pageSize;
            resultCount = Number(data.result_count) || 0;
            root.dataset.endpointPage = String(currentPage);
            root.dataset.endpointPageCount = String(data.page_count || 0);
            root.dataset.endpointPageSizeValue = String(pageSize);
            root.dataset.endpointResultCount = String(resultCount);
            if (pageSizeControl) pageSizeControl.value = String(pageSize);
            const start = resultCount ? ((currentPage - 1) * pageSize) + 1 : 0;
            const end = Math.min(currentPage * pageSize, resultCount);
            if (pageSummary) pageSummary.textContent = resultCount
                ? `Exibindo ${start}-${end} de ${resultCount} endpoints`
                : "Nenhum endpoint para exibir";
            if (visibleCount) visibleCount.textContent = String(resultCount);
            if (pageCurrent) pageCurrent.textContent = `${currentPage} / ${data.page_count || 0}`;
            if (pagination) pagination.hidden = displayCount() <= pageSize;
            [
                ["[data-endpoint-page-first]", currentPage <= 1],
                ["[data-endpoint-page-previous]", currentPage <= 1],
                ["[data-endpoint-page-next]", currentPage >= Number(data.page_count || 0)],
                ["[data-endpoint-page-last]", currentPage >= Number(data.page_count || 0)]
            ].forEach(function (entry) {
                const button = root.querySelector(entry[0]);
                if (button) button.disabled = entry[1] || resultCount === 0;
            });
        }

        function updateMetrics(data) {
            Object.keys(data).forEach(function (key) {
                const metric = document.querySelector(`[data-endpoint-count="${key}"]`);
                if (metric) metric.textContent = String(data[key]);
            });
        }

        function currentParameters() {
            const params = new URLSearchParams(window.location.search);
            if (filterForm) {
                ["q", "status", "type", "sector", "os", "domain", "agent", "defender", "disk", "attention", "quick"].forEach(key => params.delete(key));
                new FormData(filterForm).forEach(function (value, key) {
                    params.set(key, String(value));
                });
            }
            params.set("page", String(currentPage || 1));
            params.set("page_size", String(pageSize));
            params.set("view", activeView);
            return params;
        }

        function syncFilterControls(params) {
            if (!filterForm) return;
            Array.from(filterForm.elements).forEach(function (control) {
                if (!control.name) return;
                if (control.type === "checkbox") control.checked = params.getAll(control.name).includes(control.value);
                else if (params.has(control.name)) control.value = params.get(control.name);
                else control.value = control.name === "quick" ? "all" : "";
            });
        }

        async function loadResults(params, pushHistory) {
            if (!serverPaginated) return;
            if (requestController) requestController.abort();
            requestController = new AbortController();
            const sequence = ++requestSequence;
            const url = `${root.dataset.endpointUrl}?${params.toString()}`;
            try {
                const response = await fetch(url, {
                    credentials: "same-origin",
                    headers: { "X-Requested-With": "XMLHttpRequest" },
                    signal: requestController.signal
                });
                if (!response.ok) throw new Error(`HTTP ${response.status}`);
                const data = await response.json();
                if (sequence !== requestSequence) return;
                const panel = panels.find(item => item.dataset.endpointViewPanel === data.view);
                if (!panel) return;
                const contentTarget = data.view === "table"
                    ? panel.querySelector("[data-endpoint-table-body]")
                    : panel;
                if (!contentTarget) throw new Error("Endpoint table body is unavailable");
                syncFilterControls(params);
                contentTarget.innerHTML = data.html;
                panel.dataset.endpointLoaded = "true";
                panels.filter(item => item !== panel).forEach(item => { item.dataset.endpointLoaded = "false"; });
                updatePagination(data);
                updateMetrics(data);
                updateBulkBar();
                if (window.lucide && typeof window.lucide.createIcons === "function") window.lucide.createIcons();
                if (pushHistory) window.history.pushState({}, "", url);
            } catch (error) {
                if (error.name !== "AbortError") {
                    pageSize = Number(root.dataset.endpointPageSizeValue) || 10;
                    currentPage = Number(root.dataset.endpointPage);
                    if (!Number.isFinite(currentPage)) currentPage = 1;
                    if (pageSizeControl) pageSizeControl.value = String(pageSize);
                    showToast("Nao foi possivel atualizar a lista de endpoints.");
                }
            }
        }

        function setView(view, fetchIfNeeded) {
            activeView = view;
            viewButtons.forEach(button => button.classList.toggle("is-active", button.dataset.endpointView === view));
            panels.forEach(panel => {
                const active = panel.dataset.endpointViewPanel === view;
                panel.hidden = !active;
                panel.classList.toggle("is-active", active);
            });
            if (fetchIfNeeded && serverPaginated) {
                const panel = panels.find(item => item.dataset.endpointViewPanel === view);
                if (panel && panel.dataset.endpointLoaded !== "true") loadResults(currentParameters(), true);
            }
            if (pagination) pagination.hidden = displayCount() <= pageSize;
            if (window.lucide && typeof window.lucide.createIcons === "function") window.lucide.createIcons();
        }

        function navigate(change, resetPage) {
            const params = currentParameters();
            if (resetPage) params.set("page", "1");
            Object.entries(change || {}).forEach(([key, value]) => {
                if (value === null) params.delete(key);
                else params.set(key, String(value));
            });
            loadResults(params, true);
        }

        if (serverPaginated) {
            updatePagination({ page: currentPage, page_count: Number(root.dataset.endpointPageCount), page_size: pageSize, result_count: resultCount });
            updateBulkBar();
            filterForm?.addEventListener("submit", function (event) {
                event.preventDefault();
                navigate({}, true);
            });
            const search = filterForm?.querySelector("input[name='q']");
            search?.addEventListener("input", function () {
                window.clearTimeout(searchTimer);
                searchTimer = window.setTimeout(() => navigate({}, true), 280);
            });
            document.querySelectorAll(".endpoint-quick-filters a, .endpoint-metric-card").forEach(function (link) {
                link.addEventListener("click", function (event) {
                    const target = new URL(link.href, window.location.href);
                    if (target.pathname !== window.location.pathname) return;
                    event.preventDefault();
                    const params = new URLSearchParams(target.search);
                    const current = currentParameters();
                    ["q", "status", "type", "sector", "os", "domain", "agent", "defender", "disk", "attention", "quick"].forEach(key => {
                        current.delete(key);
                        if (params.has(key)) current.set(key, params.get(key));
                    });
                    current.set("page", "1");
                    loadResults(current, true);
                });
            });
            window.addEventListener("popstate", function () {
                const params = new URLSearchParams(window.location.search);
                currentPage = Number(params.get("page")) || 1;
                pageSize = Number(params.get("page_size")) || 10;
                if (pageSizeControl) pageSizeControl.value = String(pageSize);
                setView(params.get("view") || "table", false);
                loadResults(params, false);
            });
        }

        viewButtons.forEach(function (button) {
            button.addEventListener("click", function () {
                const view = button.dataset.endpointView || "table";
                setView(view, false);
                if (serverPaginated) {
                    if (panels.find(item => item.dataset.endpointViewPanel === view)?.dataset.endpointLoaded === "true") {
                        const params = currentParameters();
                        params.set("view", view);
                        window.history.pushState({}, "", `${root.dataset.endpointUrl}?${params.toString()}`);
                    } else navigate({ view }, false);
                }
            });
        });

        if (selectAll) {
            selectAll.addEventListener("change", function () {
                pageRows().forEach(function (row) {
                    const input = row.querySelector("[data-endpoint-select]");
                    const id = input?.dataset.fleetEndpointId;
                    if (!input || !id) return;
                    input.checked = selectAll.checked;
                    if (selectAll.checked) selectedEndpoints.set(id, row.dataset.endpoint || id);
                    else selectedEndpoints.delete(id);
                });
                updateBulkBar();
                notifySelectionChanged();
            });
        }

        root.querySelector("[data-endpoint-selection-clear]")?.addEventListener("click", function () {
            selectedEndpoints.clear();
            updateBulkBar();
            notifySelectionChanged();
        });

        root.addEventListener("change", function (event) {
            const input = event.target.closest("[data-endpoint-select]");
            if (!input) return;
            const row = rowFromElement(input);
            const id = input.dataset.fleetEndpointId;
            if (!id) return;
            if (input.checked) selectedEndpoints.set(id, row?.dataset.endpoint || id);
            else selectedEndpoints.delete(id);
            updateBulkBar();
            notifySelectionChanged();
        });

        root.querySelector("[data-endpoint-page-size]")?.addEventListener("change", function () {
            const requestedSize = Number(pageSizeControl.value) || 10;
            if (serverPaginated) { pageSize = requestedSize; navigate({ page_size: requestedSize }, true); }
            else { pageSize = requestedSize; localPage = 1; root.__renderLocalEndpointPage?.(); }
        });
        [
            ["[data-endpoint-page-first]", () => 1],
            ["[data-endpoint-page-previous]", () => Math.max(1, (serverPaginated ? currentPage : localPage) - 1)],
            ["[data-endpoint-page-next]", () => (serverPaginated ? currentPage : localPage) + 1],
            ["[data-endpoint-page-last]", () => serverPaginated
                ? Number(root.dataset.endpointPageCount) || 1
                : Math.max(1, Math.ceil(displayCount() / pageSize))]
        ].forEach(function ([selector, getPage]) {
            root.querySelector(selector)?.addEventListener("click", function () {
                if (serverPaginated) navigate({ page: getPage() }, false);
                else { localPage = getPage(); root.__renderLocalEndpointPage?.(); }
            });
        });

        root.addEventListener("click", function (event) {
            const target = event.target;
            const row = rowFromElement(target);
            if (row && !target.closest("a, button, input, .endpoint-action-menu")) {
                if (row.dataset.detailUrl) window.location.href = row.dataset.detailUrl;
                return;
            }
            const menuButton = target.closest("[data-endpoint-menu-toggle]");
            if (menuButton) {
                event.stopPropagation();
                const popover = menuButton.parentElement.querySelector(".endpoint-action-popover");
                const willOpen = popover && popover.hidden;
                root.querySelectorAll(".endpoint-action-popover").forEach(item => { item.hidden = true; });
                if (popover) popover.hidden = !willOpen;
                return;
            }
            const copyButton = target.closest("[data-endpoint-copy]");
            if (copyButton) {
                event.preventDefault();
                event.stopPropagation();
                const type = copyButton.dataset.endpointCopy;
                copyValue(type === "ip" ? row?.dataset.ip : row?.dataset.endpoint, type === "ip" ? "IP" : "Hostname");
                return;
            }
            const actionButton = target.closest("[data-endpoint-action]");
            if (actionButton) {
                event.preventDefault();
                event.stopPropagation();
                runEndpointAction(actionButton.dataset.endpointAction, row);
                root.querySelectorAll(".endpoint-action-popover").forEach(item => { item.hidden = true; });
            }
        });

        document.addEventListener("click", function (event) {
            if (!event.target.closest(".endpoint-action-menu")) root.querySelectorAll(".endpoint-action-popover").forEach(item => { item.hidden = true; });
        });

        window.NightOwlEndpointSelection = {
            getIds: () => Array.from(selectedEndpoints.keys()),
            clear: function () {
                selectedEndpoints.clear();
                updateBulkBar();
                notifySelectionChanged();
            }
        };

        root.querySelectorAll("[data-bulk-endpoint-action]").forEach(function (button) {
            button.addEventListener("click", function () {
                const selected = Array.from(selectedEndpoints.entries());
                if (!selected.length) {
                    showToast("Selecione pelo menos um endpoint.");
                    return;
                }
                selected.forEach(([id, hostname]) => {
                    if (operational && typeof operational.runAction === "function") {
                        operational.runAction(button.dataset.bulkEndpointAction, {
                            endpoint: hostname,
                            toastOptions: { target: toast, timeout: 2800 },
                            description: `${button.textContent.trim()} aplicado em ${hostname}.`
                        });
                    }
                });
                showToast(`${button.textContent.trim()} aplicado em ${selected.length} endpoint(s).`);
            });
        });

        if (!serverPaginated) {
            const tableRows = Array.from(root.querySelectorAll("tr[data-endpoint-row]"));
            const renderLocalPage = function () {
                const size = Number(pageSizeControl?.value) || 10;
                const pages = Math.max(1, Math.ceil(tableRows.length / size));
                localPage = Math.max(1, Math.min(localPage, pages));
                tableRows.forEach((row, index) => { row.hidden = index < (localPage - 1) * size || index >= localPage * size; });
                if (pageSummary) pageSummary.textContent = tableRows.length ? `Exibindo ${(localPage - 1) * size + 1}-${Math.min(localPage * size, tableRows.length)} de ${tableRows.length} endpoints` : "Nenhum endpoint para exibir";
                if (pageCurrent) pageCurrent.textContent = `${tableRows.length ? localPage : 0} / ${tableRows.length ? pages : 0}`;
                if (pagination) pagination.hidden = tableRows.length <= size;
                [
                    ["[data-endpoint-page-first]", localPage <= 1],
                    ["[data-endpoint-page-previous]", localPage <= 1],
                    ["[data-endpoint-page-next]", localPage >= pages],
                    ["[data-endpoint-page-last]", localPage >= pages]
                ].forEach(([selector, disabled]) => {
                    const button = root.querySelector(selector);
                    if (button) button.disabled = disabled || tableRows.length === 0;
                });
                updateBulkBar();
            };
            root.__renderLocalEndpointPage = renderLocalPage;
            renderLocalPage();
        }
    }

    document.querySelectorAll("[data-endpoint-action]").forEach(function (button) {
        if (root && root.contains(button)) return;
        button.addEventListener("click", function () {
            const endpoint = button.dataset.endpoint || "Endpoints";
            if (operational && typeof operational.runAction === "function") {
                operational.runAction(button.dataset.endpointAction, { endpoint, toastOptions: { target: toast, timeout: 2800 } });
                return;
            }
            showToast(`${button.textContent.trim()} solicitado.`);
        });
    });
}());
