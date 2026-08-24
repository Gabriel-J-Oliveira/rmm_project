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

    function csrfToken(form) {
        const input = form ? form.querySelector("input[name='csrfmiddlewaretoken']") : null;
        return input ? input.value : "";
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

    const deploymentModal = document.querySelector("[data-deployment-modal]");
    const deploymentBackdrop = document.querySelector("[data-deployment-backdrop]");
    const deploymentForm = document.querySelector("[data-deployment-form]");
    if (deploymentModal && deploymentForm) {
        const releaseOptionsNode = document.getElementById("deployment-release-options");
        const releaseOptions = releaseOptionsNode ? JSON.parse(releaseOptionsNode.textContent || "[]") : [];
        const channelSelect = deploymentForm.querySelector("[data-deployment-channel]");
        const releaseSelect = deploymentForm.querySelector("[data-deployment-release]");
        const releaseField = deploymentForm.querySelector("[data-deployment-release-field]");
        const versionLabel = deploymentForm.querySelector("[data-deployment-version]");
        const channelLabel = deploymentForm.querySelector("[data-deployment-channel-label]");
        const errorBox = deploymentForm.querySelector("[data-deployment-error]");
        const commandBox = deploymentForm.querySelector("[data-deployment-command-box]");
        const commandOutput = deploymentForm.querySelector("[data-deployment-command]");
        const resultBox = deploymentForm.querySelector("[data-deployment-result]");
        const expiresOutput = deploymentForm.querySelector("[data-deployment-expires]");
        const statusOutput = deploymentForm.querySelector("[data-deployment-status]");
        const submitButton = deploymentForm.querySelector("[data-deployment-submit]");

        function setDeploymentError(message) {
            if (!errorBox) return;
            errorBox.textContent = message || "";
            errorBox.hidden = !message;
        }

        function openDeploymentModal() {
            deploymentModal.hidden = false;
            if (deploymentBackdrop) deploymentBackdrop.hidden = false;
            setDeploymentError("");
            if (window.lucide && typeof window.lucide.createIcons === "function") {
                window.lucide.createIcons();
            }
        }

        function closeDeploymentModal() {
            deploymentModal.hidden = true;
            if (deploymentBackdrop) deploymentBackdrop.hidden = true;
        }

        function selectedRelease() {
            return releaseOptions.find(function (release) {
                return release.id === releaseSelect.value;
            }) || null;
        }

        function updateReleaseChoices() {
            const channel = channelSelect.value;
            channelLabel.textContent = channel;
            releaseSelect.innerHTML = "";
            if (channel === "development") {
                const developmentReleases = releaseOptions.filter(function (release) {
                    return release.channel === "development";
                });
                developmentReleases.forEach(function (release) {
                    const option = document.createElement("option");
                    option.value = release.id;
                    option.textContent = `${release.version} (${release.status}${release.paused ? ", paused" : ""})`;
                    releaseSelect.appendChild(option);
                });
                releaseField.hidden = false;
                versionLabel.textContent = selectedRelease()?.version || "-";
            } else {
                releaseField.hidden = true;
                const stableRelease = releaseOptions.find(function (release) {
                    return release.channel === "stable" && release.status === "published" && !release.paused;
                });
                versionLabel.textContent = stableRelease ? stableRelease.version : "stable elegivel atual";
            }
            if (commandBox) {
                commandBox.hidden = true;
                commandBox.dataset.copyValue = "";
            }
            if (resultBox) resultBox.hidden = true;
        }

        document.querySelectorAll("[data-deployment-open]").forEach(function (button) {
            button.addEventListener("click", openDeploymentModal);
        });
        document.querySelectorAll("[data-deployment-close]").forEach(function (button) {
            button.addEventListener("click", closeDeploymentModal);
        });
        if (deploymentBackdrop) deploymentBackdrop.addEventListener("click", closeDeploymentModal);
        channelSelect.addEventListener("change", updateReleaseChoices);
        releaseSelect.addEventListener("change", function () {
            versionLabel.textContent = selectedRelease()?.version || "-";
            if (commandBox) commandBox.hidden = true;
            if (resultBox) resultBox.hidden = true;
        });

        deploymentForm.addEventListener("submit", async function (event) {
            event.preventDefault();
            setDeploymentError("");
            const payload = new FormData(deploymentForm);
            if (channelSelect.value !== "development") {
                payload.delete("release_id");
            }
            if (submitButton) submitButton.disabled = true;
            try {
                const response = await fetch(deploymentForm.dataset.createUrl, {
                    method: "POST",
                    headers: { "X-CSRFToken": csrfToken(deploymentForm) },
                    body: payload,
                    credentials: "same-origin"
                });
                const data = await response.json();
                if (!response.ok || data.status !== "ok") {
                    throw new Error(data.detail || data.error || "Nao foi possivel gerar o comando.");
                }
                const deployment = data.deployment || {};
                commandOutput.textContent = deployment.command || "";
                commandBox.dataset.copyValue = deployment.command || "";
                commandBox.hidden = false;
                resultBox.hidden = false;
                expiresOutput.textContent = deployment.expires_at || "-";
                statusOutput.textContent = "Aguardando instalacao";
                versionLabel.textContent = deployment.release_version || versionLabel.textContent;
                showToast("Comando de instalacao gerado.");
            } catch (error) {
                setDeploymentError(error.message || "Falha ao gerar comando.");
            } finally {
                if (submitButton) submitButton.disabled = false;
            }
        });

        deploymentForm.querySelectorAll("[data-deployment-copy]").forEach(function (button) {
            button.addEventListener("click", function () {
                copyValue(commandBox?.dataset.copyValue || "", "Comando PowerShell");
            });
        });

        updateReleaseChoices();
    }
}());
