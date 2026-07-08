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
        const selectAll = root.querySelector("[data-endpoint-select-all]");
        const rowChecks = Array.from(root.querySelectorAll("[data-endpoint-select]"));
        const tableRows = Array.from(root.querySelectorAll("tr[data-endpoint-row]"));

        function setView(view) {
            viewButtons.forEach(function (button) {
                button.classList.toggle("is-active", button.dataset.endpointView === view);
            });
            panels.forEach(function (panel) {
                const active = panel.dataset.endpointViewPanel === view;
                panel.hidden = !active;
                panel.classList.toggle("is-active", active);
            });
            if (window.lucide && typeof window.lucide.createIcons === "function") {
                window.lucide.createIcons();
            }
        }

        function updateBulkBar() {
            const checked = rowChecks.filter(function (input) { return input.checked; });
            if (bulkBar) bulkBar.hidden = checked.length === 0;
            if (selectedCount) selectedCount.textContent = String(checked.length);
            tableRows.forEach(function (row) {
                const checkbox = row.querySelector("[data-endpoint-select]");
                row.classList.toggle("is-selected", Boolean(checkbox && checkbox.checked));
            });
            if (selectAll) {
                selectAll.checked = checked.length > 0 && checked.length === rowChecks.length;
                selectAll.indeterminate = checked.length > 0 && checked.length < rowChecks.length;
            }
        }

        viewButtons.forEach(function (button) {
            button.addEventListener("click", function () {
                setView(button.dataset.endpointView || "table");
            });
        });

        if (selectAll) {
            selectAll.addEventListener("change", function () {
                rowChecks.forEach(function (input) {
                    input.checked = selectAll.checked;
                });
                updateBulkBar();
            });
        }

        rowChecks.forEach(function (input) {
            input.addEventListener("change", updateBulkBar);
        });

        tableRows.forEach(function (row) {
            row.addEventListener("click", function (event) {
                if (event.target.closest("a, button, input, .endpoint-action-menu")) {
                    return;
                }
                if (row.dataset.detailUrl) {
                    window.location.href = row.dataset.detailUrl;
                }
            });
        });

        root.querySelectorAll("[data-endpoint-menu-toggle]").forEach(function (button) {
            button.addEventListener("click", function (event) {
                event.stopPropagation();
                const popover = button.parentElement.querySelector(".endpoint-action-popover");
                const willOpen = popover && popover.hidden;
                root.querySelectorAll(".endpoint-action-popover").forEach(function (item) {
                    item.hidden = true;
                });
                if (popover) popover.hidden = !willOpen;
            });
        });

        root.addEventListener("click", function (event) {
            const copyButton = event.target.closest("[data-endpoint-copy]");
            if (copyButton) {
                event.preventDefault();
                event.stopPropagation();
                const row = rowFromElement(copyButton);
                const type = copyButton.dataset.endpointCopy;
                copyValue(type === "ip" ? row?.dataset.ip : row?.dataset.endpoint, type === "ip" ? "IP" : "Hostname");
                return;
            }

            const actionButton = event.target.closest("[data-endpoint-action]");
            if (actionButton) {
                event.preventDefault();
                event.stopPropagation();
                const row = rowFromElement(actionButton);
                runEndpointAction(actionButton.dataset.endpointAction, row);
                root.querySelectorAll(".endpoint-action-popover").forEach(function (item) {
                    item.hidden = true;
                });
                return;
            }

            const ticketLink = event.target.closest("a[href*='tickets']");
            if (ticketLink) {
                event.preventDefault();
                event.stopPropagation();
                const row = rowFromElement(ticketLink);
                runEndpointAction("create_ticket", row, `Chamado mockado criado para ${row ? row.dataset.endpoint : "endpoint"}.`);
                root.querySelectorAll(".endpoint-action-popover").forEach(function (item) {
                    item.hidden = true;
                });
            }
        });

        root.querySelectorAll("[data-bulk-endpoint-action]").forEach(function (button) {
            button.addEventListener("click", function () {
                const checkedRows = rowChecks
                    .filter(function (input) { return input.checked; })
                    .map(function (input) { return rowFromElement(input); })
                    .filter(Boolean);
                if (!checkedRows.length) {
                    showToast("Selecione pelo menos um endpoint.");
                    return;
                }
                checkedRows.forEach(function (row) {
                    runEndpointAction(button.dataset.bulkEndpointAction, row, `${button.textContent.trim()} aplicado em lote.`);
                });
                showToast(`${button.textContent.trim()} aplicado em ${checkedRows.length} endpoint(s).`);
            });
        });

        document.addEventListener("click", function (event) {
            if (!event.target.closest(".endpoint-action-menu")) {
                root.querySelectorAll(".endpoint-action-popover").forEach(function (item) {
                    item.hidden = true;
                });
            }
        });

        updateBulkBar();
    }

    document.querySelectorAll("[data-endpoint-action]").forEach(function (button) {
        if (root && root.contains(button)) return;
        button.addEventListener("click", function () {
            const endpoint = button.dataset.endpoint || "Endpoints";
            if (operational && typeof operational.runAction === "function") {
                operational.runAction(button.dataset.endpointAction, {
                    endpoint,
                    toastOptions: { target: toast, timeout: 2800 }
                });
                return;
            }
            showToast(`${button.textContent.trim()} solicitado.`);
        });
    });
}());
