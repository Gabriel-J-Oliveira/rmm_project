(function () {
    var root = document.querySelector("[data-events-root]");

    if (!root) {
        return;
    }

    var form = root.querySelector("[data-events-filter-form]");
    var categoryInput = root.querySelector("[data-events-category-input]");
    var timeline = root.querySelector("[data-events-timeline]");
    var table = root.querySelector("[data-events-table]");
    var drawer = root.querySelector("[data-event-drawer]");
    var backdrop = root.querySelector("[data-event-drawer-backdrop]");
    var toast = root.querySelector("[data-event-toast]");
    var operational = window.NightOwlOperational;
    var currentPayload = "";

    if (operational) {
        operational.renderStoredEvents(root);
    }

    root.__nightowlEvents = operational ? operational.collectEventItems(root) : [];
    if (operational) {
        operational.initOperationalChrome(root, { countSelector: "[data-event-card]", staleAfterSeconds: 240 });
    }

    function eventCards() {
        return Array.prototype.slice.call(root.querySelectorAll("[data-event-card]"));
    }

    function showToast(message) {
        if (operational) {
            operational.showToast(message, { target: toast, timeout: 2600 });
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
        }, 2600);
    }

    function setText(selector, value) {
        var element = root.querySelector(selector);
        if (element) {
            element.textContent = value || "--";
        }
    }

    function setField(name, value) {
        setText('[data-event-field="' + name + '"]', value);
    }

    function buildPayload(card) {
        return JSON.stringify({
            id: card.dataset.eventId || null,
            event_type: card.dataset.eventType || null,
            category: card.dataset.eventCategory || null,
            severity: card.dataset.eventSeverity || null,
            origin: card.dataset.eventOrigin || null,
            endpoint: card.dataset.eventEndpoint || null,
            actor: card.dataset.eventActor || null,
            timestamp: card.dataset.eventTimestamp || null,
            source: "nightowl.rmm.events",
            related: {
                endpoint_url: card.dataset.eventEndpointUrl || null,
                alert_url: card.dataset.eventAlertUrl || null
            },
            message: card.dataset.eventDescription || ""
        }, null, 2);
    }

    function openDrawer(card) {
        if (!drawer || !card) {
            return;
        }
        eventCards().forEach(function (item) {
            item.classList.toggle("is-selected", item === card);
        });
        setText("[data-event-drawer-title]", card.dataset.eventTitle);
        setText("[data-event-drawer-subtitle]", card.dataset.eventType + " · " + card.dataset.eventRelative);
        setField("timestamp", card.dataset.eventTimestamp);
        setField("type", card.dataset.eventType);
        setField("origin", card.dataset.eventOrigin);
        setField("severity", card.dataset.eventSeverity);
        setField("endpoint", card.dataset.eventEndpoint);
        setField("actor", card.dataset.eventActor);
        setField("description", card.dataset.eventDescription);

        var endpointLink = root.querySelector("[data-event-link-endpoint]");
        var alertLink = root.querySelector("[data-event-link-alert]");
        var emptyLink = root.querySelector("[data-event-link-empty]");
        var hasAnyLink = false;
        var endpointName = card.dataset.eventEndpoint || "";
        var endpointUrl = card.dataset.eventEndpointUrl || (endpointName && endpointName !== "Sem endpoint" ? "/endpoints/?q=" + encodeURIComponent(endpointName) : "");
        var relatedUrl = card.dataset.eventAlertUrl || "";
        var relatedLabel = "Ver alerta relacionado";
        if (!relatedUrl && card.dataset.eventCategory === "alerts") {
            relatedUrl = "/alerts/?q=" + encodeURIComponent(endpointName || card.dataset.eventTitle || "");
        }
        if (!relatedUrl && card.dataset.eventCategory === "jobs") {
            relatedUrl = "/events/?category=jobs&q=" + encodeURIComponent(endpointName || card.dataset.eventType || "");
            relatedLabel = "Ver job relacionado";
        }

        if (endpointLink) {
            endpointLink.hidden = !endpointUrl;
            endpointLink.href = endpointUrl || "#";
            hasAnyLink = hasAnyLink || Boolean(endpointUrl);
        }
        if (alertLink) {
            alertLink.hidden = !relatedUrl;
            alertLink.href = relatedUrl || "#";
            alertLink.innerHTML = '<i data-lucide="' + (relatedLabel.indexOf("job") >= 0 ? "briefcase-business" : "alert-triangle") + '"></i>' + relatedLabel;
            hasAnyLink = hasAnyLink || Boolean(relatedUrl);
        }
        if (emptyLink) {
            emptyLink.hidden = hasAnyLink;
        }

        currentPayload = buildPayload(card);
        setText("[data-event-payload]", currentPayload);
        drawer.classList.add("is-open");
        drawer.setAttribute("aria-hidden", "false");
        if (backdrop) {
            backdrop.hidden = false;
        }
    }

    function closeDrawer() {
        if (!drawer) {
            return;
        }
        drawer.classList.remove("is-open");
        drawer.setAttribute("aria-hidden", "true");
        if (backdrop) {
            backdrop.hidden = true;
        }
    }

    function setView(view) {
        var showTable = view === "table";
        if (timeline) {
            timeline.hidden = showTable;
        }
        if (table) {
            table.hidden = !showTable;
        }
        root.querySelectorAll("[data-events-view]").forEach(function (button) {
            button.classList.toggle("is-active", button.dataset.eventsView === view);
        });
        window.localStorage.setItem("nightowl-events-view", view);
    }

    root.querySelectorAll("[data-event-category]").forEach(function (button) {
        button.addEventListener("click", function () {
            if (categoryInput) {
                categoryInput.value = button.dataset.eventCategory || "all";
            }
            if (form) {
                form.submit();
            }
        });
    });

    root.querySelectorAll("[data-events-view]").forEach(function (button) {
        button.addEventListener("click", function () {
            setView(button.dataset.eventsView);
        });
    });

    eventCards().forEach(function (card) {
        card.addEventListener("click", function (event) {
            if (event.target.closest("a")) {
                return;
            }
            openDrawer(card);
        });
        card.querySelectorAll("[data-event-open]").forEach(function (button) {
            button.addEventListener("click", function (event) {
                event.preventDefault();
                openDrawer(card);
            });
        });
    });

    if (timeline) {
        timeline.addEventListener("click", function (event) {
            var card = event.target.closest("[data-event-card]");
            if (!card || event.target.closest("a")) {
                return;
            }
            openDrawer(card);
        });
    }

    root.querySelectorAll("[data-event-table-row]").forEach(function (row) {
        row.addEventListener("click", function () {
            var card = eventCards()[Number(row.dataset.eventTableRow)];
            openDrawer(card);
        });
    });

    root.querySelectorAll("[data-event-drawer-close]").forEach(function (button) {
        button.addEventListener("click", closeDrawer);
    });

    if (backdrop) {
        backdrop.addEventListener("click", closeDrawer);
    }

    document.addEventListener("keydown", function (event) {
        if (event.key === "Escape") {
            closeDrawer();
        }
    });

    window.addEventListener("nightowl:event-created", function () {
        if (operational) {
            operational.renderStoredEvents(root);
        }
    });

    root.querySelectorAll("[data-event-copy]").forEach(function (button) {
        button.addEventListener("click", function () {
            if (navigator.clipboard && currentPayload) {
                navigator.clipboard.writeText(currentPayload).then(function () {
                    if (operational) {
                        operational.runAction("copy_summary", {
                            toastOptions: { target: toast, timeout: 2600 },
                            description: "Payload técnico copiado no frontend."
                        });
                    } else {
                        showToast("Detalhes do evento copiados.");
                    }
                }).catch(function () {
                    showToast("Não foi possível copiar automaticamente.");
                });
                return;
            }
            if (operational) {
                operational.runAction("copy_summary", {
                    toastOptions: { target: toast, timeout: 2600 },
                    description: "Payload técnico disponível no drawer."
                });
            } else {
                showToast("Detalhes disponíveis no payload técnico.");
            }
        });
    });

    root.querySelectorAll("[data-events-export]").forEach(function (button) {
        button.addEventListener("click", function () {
            if (operational) {
                operational.runAction("export_events", {
                    toastOptions: { target: toast, timeout: 2600 },
                    description: "Exportação mockada de eventos solicitada na tela de auditoria."
                });
            } else {
                showToast("Exportação de eventos preparada no mock. Backend real entra na próxima etapa.");
            }
        });
    });

    setView(window.localStorage.getItem("nightowl-events-view") || "timeline");
}());
