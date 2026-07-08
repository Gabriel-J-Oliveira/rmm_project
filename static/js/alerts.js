(function () {
    var root = document.querySelector("[data-alerts-root]");
    var refreshEverySeconds = 60;
    var remaining = refreshEverySeconds;
    var countdown = document.querySelector("[data-refresh-countdown]");
    var refreshButton = document.querySelector("[data-refresh-now]");
    var toast = document.querySelector("[data-alert-action-toast]");
    var operational = window.NightOwlOperational;

    function updateCountdown() {
        if (countdown) {
            countdown.textContent = remaining + "s";
        }
    }

    function showToast(message) {
        if (operational) {
            operational.showToast(message, { target: toast });
            return;
        }
        if (!toast) {
            return;
        }
        toast.textContent = message;
        toast.hidden = false;
        toast.classList.add("is-visible");
        window.clearTimeout(showToast.timer);
        showToast.timer = window.setTimeout(function () {
            toast.classList.remove("is-visible");
            toast.hidden = true;
        }, 2800);
    }

    if (refreshButton) {
        refreshButton.addEventListener("click", function () {
            window.location.reload();
        });
    }

    document.querySelectorAll("[data-confirm]").forEach(function (element) {
        element.addEventListener("click", function (event) {
            if (!window.confirm(element.getAttribute("data-confirm"))) {
                event.preventDefault();
            }
        });
    });

    updateCountdown();
    window.setInterval(function () {
        remaining -= 1;
        if (remaining <= 0) {
            window.location.reload();
            return;
        }
        updateCountdown();
    }, 1000);

    document.querySelectorAll(".alert-toast").forEach(function (toastElement, index) {
        window.setTimeout(function () {
            toastElement.classList.add("is-hidden");
        }, 6500 + (index * 900));
        window.setTimeout(function () {
            if (toastElement.parentNode) {
                toastElement.parentNode.removeChild(toastElement);
            }
        }, 7200 + (index * 900));
    });

    if (!root) {
        return;
    }

    root.__nightowlAlerts = operational ? operational.collectAlertItems(root) : [];
    if (operational) {
        operational.applyMockAlertState(root);
        operational.initOperationalChrome(root, { countSelector: "[data-alert-card]", staleAfterSeconds: 180 });
    }

    var queue = root.querySelector("[data-alert-queue]");
    var selectAll = root.querySelector("[data-alert-select-all]");
    var selectedCount = root.querySelector("[data-alert-selected-count]");
    var drawer = root.querySelector("[data-alert-drawer]");
    var drawerBackdrop = root.querySelector("[data-alert-drawer-backdrop]");
    var drawerClose = root.querySelector("[data-alert-drawer-close]");
    var currentCard = null;

    function alertCards() {
        if (!queue) {
            return [];
        }
        return Array.prototype.slice.call(queue.querySelectorAll("[data-alert-card]"));
    }

    function selectedBoxes() {
        return Array.prototype.slice.call(root.querySelectorAll("[data-alert-select]:checked"));
    }

    function refreshSelectedCount() {
        var total = selectedBoxes().length;
        if (selectedCount) {
            selectedCount.textContent = String(total);
        }
        if (selectAll) {
            var boxes = root.querySelectorAll("[data-alert-select]");
            selectAll.checked = boxes.length > 0 && total === boxes.length;
            selectAll.indeterminate = total > 0 && total < boxes.length;
        }
    }

    function clearGroupHeadings() {
        if (!queue) {
            return;
        }
        queue.querySelectorAll(".alert-group-heading").forEach(function (heading) {
            heading.remove();
        });
    }

    function applyGroupedView() {
        if (!queue) {
            return;
        }
        clearGroupHeadings();
        var cards = alertCards().sort(function (a, b) {
            return (a.dataset.endpoint || "").localeCompare(b.dataset.endpoint || "");
        });
        var currentEndpoint = "";
        cards.forEach(function (card) {
            queue.appendChild(card);
            if (card.dataset.endpoint !== currentEndpoint) {
                currentEndpoint = card.dataset.endpoint;
                var heading = document.createElement("div");
                var title = document.createElement("span");
                var subtitle = document.createElement("small");
                heading.className = "alert-group-heading";
                title.textContent = currentEndpoint;
                subtitle.textContent = "Problemas agrupados por endpoint";
                heading.appendChild(title);
                heading.appendChild(subtitle);
                queue.insertBefore(heading, card);
            }
        });
    }

    function applyView(view) {
        if (!queue) {
            return;
        }
        clearGroupHeadings();
        queue.classList.toggle("view-compact", view === "compact");
        queue.classList.toggle("view-grouped", view === "grouped");
        if (view === "grouped") {
            applyGroupedView();
        }
        root.querySelectorAll("[data-alert-view]").forEach(function (button) {
            button.classList.toggle("is-active", button.dataset.alertView === view);
        });
        window.localStorage.setItem("nightowl-alert-view", view);
    }

    function setDrawerText(selector, value) {
        var element = root.querySelector(selector);
        if (element) {
            element.textContent = value || "--";
        }
    }

    function openDrawer(card) {
        if (!drawer || !card) {
            return;
        }
        currentCard = card;
        alertCards().forEach(function (item) {
            item.classList.toggle("is-selected", item === card);
        });
        setDrawerText("[data-alert-drawer-title]", card.dataset.title);
        setDrawerText("[data-alert-drawer-subtitle]", card.dataset.endpoint + " · " + card.dataset.openTime);
        setDrawerText("[data-alert-drawer-status]", card.dataset.status);
        setDrawerText("[data-alert-drawer-severity]", card.dataset.severity);
        setDrawerText("[data-alert-drawer-type]", card.dataset.type);
        setDrawerText("[data-alert-drawer-owner]", card.dataset.owner);
        setDrawerText("[data-alert-drawer-sla]", card.dataset.sla);
        setDrawerText("[data-alert-drawer-description]", card.dataset.description);
        setDrawerText("[data-alert-drawer-endpoint]", card.dataset.endpoint);
        setDrawerText("[data-alert-drawer-ticket]", card.dataset.ticket);
        setDrawerText("[data-alert-drawer-recurrence]", card.dataset.recurrence);
        setDrawerText("[data-alert-drawer-events]", card.dataset.events);

        var endpointLink = root.querySelector("[data-alert-drawer-endpoint-url]");
        if (endpointLink) {
            endpointLink.href = card.dataset.endpointUrl || "#";
        }

        drawer.classList.add("is-open");
        drawer.setAttribute("aria-hidden", "false");
        if (drawerBackdrop) {
            drawerBackdrop.hidden = false;
        }
    }

    function closeDrawer() {
        if (!drawer) {
            return;
        }
        drawer.classList.remove("is-open");
        drawer.setAttribute("aria-hidden", "true");
        if (drawerBackdrop) {
            drawerBackdrop.hidden = true;
        }
    }

    root.querySelectorAll("[data-alert-select]").forEach(function (checkbox) {
        checkbox.addEventListener("change", refreshSelectedCount);
    });

    if (selectAll) {
        selectAll.addEventListener("change", function () {
            root.querySelectorAll("[data-alert-select]").forEach(function (checkbox) {
                checkbox.checked = selectAll.checked;
            });
            refreshSelectedCount();
        });
    }

    root.querySelectorAll("[data-bulk-action]").forEach(function (button) {
        button.addEventListener("click", function () {
            var total = selectedBoxes().length;
            if (!total) {
                showToast("Selecione pelo menos um alerta.");
                return;
            }
            if (operational) {
                selectedBoxes().forEach(function (checkbox) {
                    var card = checkbox.closest("[data-alert-card]");
                    operational.runAction(button.dataset.bulkAction, {
                        toastOptions: { target: toast },
                        card: card,
                        endpoint: card ? card.dataset.endpoint : "",
                        description: button.dataset.bulkAction + " aplicado em lote."
                    });
                });
            } else {
                showToast(button.dataset.bulkAction + " aplicado em " + total + " alerta(s).");
            }
        });
    });

    root.querySelectorAll("[data-alert-view]").forEach(function (button) {
        button.addEventListener("click", function () {
            applyView(button.dataset.alertView);
        });
    });

    alertCards().forEach(function (card) {
        card.querySelectorAll("[data-alert-open]").forEach(function (button) {
            button.addEventListener("click", function (event) {
                event.preventDefault();
                openDrawer(card);
            });
        });

        card.addEventListener("click", function (event) {
            if (event.target.closest("a, button, input, textarea, select, details, summary, form")) {
                return;
            }
            openDrawer(card);
        });

        var assignButton = card.querySelector("[data-assign-me]");
        if (assignButton) {
            assignButton.addEventListener("click", function () {
                var owner = "Gabriel Oliveira";
                var ownerLabel = card.querySelector("[data-owner-label]");
                card.dataset.owner = owner;
                if (ownerLabel) {
                    ownerLabel.textContent = owner;
                }
                if (currentCard === card) {
                    setDrawerText("[data-alert-drawer-owner]", owner);
                }
                showToast("Alerta atribuído a você.");
                if (operational) {
                    operational.addMockEvent({
                        title: "Alerta atribuído",
                        eventType: "alert.assigned_mocked",
                        category: "alerts",
                        source: "User",
                        severity: "info",
                        endpoint: card.dataset.endpoint || "",
                        description: "Responsável mockado atualizado para " + owner + "."
                    });
                }
            });
        }
    });

    root.querySelectorAll("form[action*='alert-acknowledge'], form[action*='alert-resolve'], form[action*='alert-mute']").forEach(function (form) {
        form.addEventListener("submit", function (event) {
            event.preventDefault();
            event.stopPropagation();
            var card = form.closest("[data-alert-card]");
            var action = "acknowledge_alert";
            if (form.action.indexOf("resolve") >= 0) {
                action = "resolve_alert";
            } else if (form.action.indexOf("mute") >= 0) {
                action = "mute_alert";
            }
            if (operational) {
                operational.runAction(action, {
                    toastOptions: { target: toast },
                    card: card,
                    endpoint: card ? card.dataset.endpoint : ""
                });
            } else {
                showToast("Ação registrada.");
            }
        });
    });

    root.querySelectorAll("a[href*='tickets']").forEach(function (link) {
        link.addEventListener("click", function (event) {
            event.preventDefault();
            event.stopPropagation();
            var card = link.closest("[data-alert-card]");
            if (operational) {
                operational.runAction("create_ticket", {
                    toastOptions: { target: toast },
                    card: card,
                    endpoint: card ? card.dataset.endpoint : ""
                });
            } else {
                showToast("Chamado mockado criado.");
            }
        });
    });

    root.querySelectorAll("form[action*='alert-comment']").forEach(function (form) {
        form.addEventListener("submit", function (event) {
            event.preventDefault();
            event.stopPropagation();
            var card = form.closest("[data-alert-card]");
            var textarea = form.querySelector("textarea");
            var note = textarea ? textarea.value.trim() : "";
            if (!note) {
                showToast("Informe uma observacao para salvar.");
                return;
            }
            if (operational) {
                operational.runAction("add_note", {
                    toastOptions: { target: toast },
                    card: card,
                    endpoint: card ? card.dataset.endpoint : "",
                    note: note,
                    description: note
                });
            } else {
                showToast("Observacao salva no mock operacional.");
            }
            if (textarea) {
                textarea.value = "";
            }
        });
    });

    if (drawerClose) {
        drawerClose.addEventListener("click", closeDrawer);
    }

    if (drawerBackdrop) {
        drawerBackdrop.addEventListener("click", closeDrawer);
    }

    document.addEventListener("keydown", function (event) {
        if (event.key === "Escape") {
            closeDrawer();
        }
    });

    root.querySelectorAll("[data-drawer-action]").forEach(function (button) {
        button.addEventListener("click", function () {
            if (operational) {
                operational.runAction(button.dataset.drawerAction, {
                    toastOptions: { target: toast },
                    card: currentCard,
                    endpoint: currentCard ? currentCard.dataset.endpoint : "",
                    description: button.dataset.drawerAction + " registrado para o alerta selecionado."
                });
                if (currentCard) {
                    setDrawerText("[data-alert-drawer-status]", currentCard.dataset.status);
                    setDrawerText("[data-alert-drawer-ticket]", currentCard.dataset.ticket);
                }
            } else {
                showToast(button.dataset.drawerAction + " registrado para o alerta selecionado.");
            }
        });
    });

    var noteSave = root.querySelector("[data-alert-note-save]");
    if (noteSave) {
        noteSave.addEventListener("click", function () {
            var noteField = root.querySelector("[data-alert-drawer-note]");
            var note = noteField ? noteField.value.trim() : "";
            if (!note) {
                showToast("Informe uma observacao para salvar.");
                return;
            }
            if (operational) {
                operational.runAction("add_note", {
                    toastOptions: { target: toast },
                    card: currentCard,
                    endpoint: currentCard ? currentCard.dataset.endpoint : "",
                    note: note,
                    description: note
                });
            } else {
                showToast("Observacao salva no mock operacional.");
            }
            if (noteField) {
                noteField.value = "";
            }
            return;
            showToast("Observação salva no mock operacional.");
            if (operational) {
                operational.addMockEvent({
                    title: "Observação operacional salva",
                    eventType: "alert.note_saved_mocked",
                    category: "alerts",
                    source: "User",
                    severity: "info",
                    endpoint: currentCard ? currentCard.dataset.endpoint : "",
                    description: "Observação mockada registrada na Central de Alertas."
                });
            }
        });
    }

    applyView(window.localStorage.getItem("nightowl-alert-view") || "comfortable");
    refreshSelectedCount();
}());
