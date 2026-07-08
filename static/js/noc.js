(function () {
    var root = document.querySelector("[data-noc-root]");
    if (!root) return;

    var operational = window.NightOwlOperational;
    root.__nightowlOperational = operational ? operational.buildNocContext(root) : { alerts: [], events: [] };
    if (operational) {
        operational.applyMockAlertState(root);
        operational.initOperationalChrome(root, { countSelector: "[data-noc-open-drawer]", staleAfterSeconds: 90 });
    }

    var refreshEverySeconds = 30;
    var remaining = refreshEverySeconds;
    var countdown = document.querySelector("[data-noc-countdown]");
    var refreshButton = document.querySelector("[data-noc-refresh]");
    var autoButton = document.querySelector("[data-noc-auto]");
    var tvButton = document.querySelector("[data-noc-tv]");
    var soundButton = document.querySelector("[data-noc-sound]");
    var drawer = document.querySelector("[data-noc-drawer]");
    var drawerBackdrop = document.querySelector("[data-noc-drawer-backdrop]");
    var drawerClose = document.querySelector("[data-noc-drawer-close]");
    var toast = document.querySelector("[data-noc-toast]");
    var autoRefreshEnabled = true;
    var soundEnabled = false;
    var currentCard = null;

    function renderCountdown() {
        if (countdown) {
            countdown.textContent = autoRefreshEnabled ? remaining + "s" : "pausado";
        }
    }

    function showToast(message) {
        if (operational) {
            operational.showToast(message || "Ação registrada.", { target: toast, timeout: 3200 });
            return;
        }
        if (!toast) return;
        toast.textContent = message || "Ação registrada.";
        toast.hidden = false;
        clearTimeout(showToast.timer);
        showToast.timer = setTimeout(function () {
            toast.hidden = true;
        }, 3200);
    }

    function beep() {
        if (!soundEnabled || !window.AudioContext && !window.webkitAudioContext) return;
        var AudioContextCtor = window.AudioContext || window.webkitAudioContext;
        var context = new AudioContextCtor();
        var oscillator = context.createOscillator();
        var gain = context.createGain();
        oscillator.type = "sine";
        oscillator.frequency.value = 880;
        gain.gain.value = 0.035;
        oscillator.connect(gain);
        gain.connect(context.destination);
        oscillator.start();
        oscillator.stop(context.currentTime + 0.12);
    }

    function openDrawer(card) {
        if (!drawer) return;
        currentCard = card;
        drawer.querySelector("[data-noc-drawer-kind]").textContent = card.dataset.drawerKind || "Contexto";
        drawer.querySelector("[data-noc-drawer-title]").textContent = card.dataset.title || "Item do NOC";
        drawer.querySelector("[data-noc-drawer-subtitle]").textContent = card.dataset.subtitle || "Monitoramento ao vivo";
        drawer.querySelector("[data-noc-drawer-status]").textContent = card.dataset.status || "--";
        drawer.querySelector("[data-noc-drawer-endpoint]").textContent = card.dataset.endpoint || "--";
        drawer.querySelector("[data-noc-drawer-ip]").textContent = card.dataset.ip || "--";
        drawer.querySelector("[data-noc-drawer-user]").textContent = card.dataset.user || "--";
        drawer.querySelector("[data-noc-drawer-body]").textContent = card.dataset.body || "Sem resumo operacional.";
        var link = drawer.querySelector("[data-noc-drawer-url]");
        if (link) {
            link.href = card.dataset.url || "#";
            link.hidden = !card.dataset.url;
        }
        drawer.classList.add("is-open");
        drawer.setAttribute("aria-hidden", "false");
        if (drawerBackdrop) drawerBackdrop.hidden = false;
        if (window.lucide && typeof window.lucide.createIcons === "function") {
            window.lucide.createIcons();
        }
    }

    function closeDrawer() {
        if (!drawer) return;
        drawer.classList.remove("is-open");
        drawer.setAttribute("aria-hidden", "true");
        if (drawerBackdrop) drawerBackdrop.hidden = true;
    }

    function nocSummaryText() {
        var lines = ["Resumo NOC NightOwl"];
        document.querySelectorAll(".noc-summary-strip article, .noc-kpi").forEach(function (item) {
            var label = item.querySelector("span") ? item.querySelector("span").textContent.trim() : "";
            var value = item.querySelector("strong") ? item.querySelector("strong").textContent.trim() : "";
            if (label || value) {
                lines.push(label + ": " + value);
            }
        });
        return lines.join("\n");
    }

    if (refreshButton) {
        refreshButton.addEventListener("click", function () {
            window.location.reload();
        });
    }

    document.querySelectorAll("[data-noc-copy-summary]").forEach(function (button) {
        button.addEventListener("click", function () {
            if (operational) {
                operational.runAction("copy_summary", {
                    toastOptions: { target: toast, timeout: 3200 },
                    copyText: nocSummaryText(),
                    description: "Resumo do NOC copiado."
                });
            } else {
                showToast("Resumo do NOC preparado.");
            }
        });
    });

    if (autoButton) {
        autoButton.addEventListener("click", function () {
            autoRefreshEnabled = !autoRefreshEnabled;
            autoButton.classList.toggle("is-active", autoRefreshEnabled);
            autoButton.setAttribute("aria-pressed", autoRefreshEnabled ? "true" : "false");
            renderCountdown();
            showToast(autoRefreshEnabled ? "Auto-refresh ligado." : "Auto-refresh pausado.");
        });
    }

    if (tvButton) {
        tvButton.addEventListener("click", function () {
            document.body.classList.toggle("noc-tv-mode");
            var active = document.body.classList.contains("noc-tv-mode");
            tvButton.classList.toggle("is-active", active);
            tvButton.setAttribute("aria-pressed", active ? "true" : "false");
            showToast(active ? "Modo TV ativado." : "Modo TV desativado.");
        });
    }

    if (soundButton) {
        soundButton.addEventListener("click", function () {
            soundEnabled = !soundEnabled;
            soundButton.classList.toggle("is-active", soundEnabled);
            soundButton.setAttribute("aria-pressed", soundEnabled ? "true" : "false");
            soundButton.innerHTML = soundEnabled ? '<i data-lucide="volume-2"></i>Som ligado' : '<i data-lucide="volume-x"></i>Som desligado';
            if (window.lucide && typeof window.lucide.createIcons === "function") {
                window.lucide.createIcons();
            }
            showToast(soundEnabled ? "Som ligado para eventos críticos." : "Som desligado.");
            beep();
        });
    }

    document.querySelectorAll("[data-noc-open-drawer]").forEach(function (card) {
        card.addEventListener("click", function (event) {
            if (event.target.closest("a, button, form, input")) return;
            openDrawer(card);
        });
        card.addEventListener("keydown", function (event) {
            if (event.key === "Enter" || event.key === " ") {
                event.preventDefault();
                openDrawer(card);
            }
        });
    });

    document.querySelectorAll("[data-noc-feedback]").forEach(function (button) {
        button.addEventListener("click", function (event) {
            event.preventDefault();
            event.stopPropagation();
            if (operational) {
                var card = button.closest("[data-noc-open-drawer]") || currentCard;
                operational.runAction(button.dataset.operationalAction || button.textContent || "execute_check", {
                    toastOptions: { target: toast, timeout: 3200 },
                    card: card,
                    endpoint: card ? card.dataset.endpoint : "",
                    description: button.dataset.nocFeedback || "Ação operacional mockada no NOC."
                });
            } else {
                showToast(button.dataset.nocFeedback || "Ação registrada.");
            }
            beep();
        });
    });

    document.querySelectorAll("[data-noc-root] form[action*='alert-acknowledge'], [data-noc-root] form[action*='alert-mute']").forEach(function (form) {
        form.addEventListener("submit", function (event) {
            event.preventDefault();
            event.stopPropagation();
            var card = form.closest("[data-noc-open-drawer]");
            var action = form.action.indexOf("mute") >= 0 ? "mute_alert" : "acknowledge_alert";
            if (operational) {
                operational.runAction(action, {
                    toastOptions: { target: toast, timeout: 3200 },
                    card: card,
                    endpoint: card ? card.dataset.endpoint : ""
                });
            } else {
                showToast(action === "mute_alert" ? "Alerta silenciado." : "Alerta reconhecido.");
            }
        });
    });

    document.querySelectorAll("[data-noc-root] a[href*='tickets']").forEach(function (link) {
        link.addEventListener("click", function (event) {
            event.preventDefault();
            event.stopPropagation();
            var card = link.closest("[data-noc-open-drawer]");
            if (operational) {
                operational.runAction("create_ticket", {
                    toastOptions: { target: toast, timeout: 3200 },
                    card: card,
                    endpoint: card ? card.dataset.endpoint : ""
                });
            } else {
                showToast("Chamado mockado criado.");
            }
        });
    });

    if (drawerClose) drawerClose.addEventListener("click", closeDrawer);
    if (drawerBackdrop) drawerBackdrop.addEventListener("click", closeDrawer);
    document.addEventListener("keydown", function (event) {
        if (event.key === "Escape") closeDrawer();
    });

    renderCountdown();
    window.setInterval(function () {
        if (!autoRefreshEnabled) {
            renderCountdown();
            return;
        }
        remaining -= 1;
        if (remaining <= 0) {
            window.location.reload();
            return;
        }
        renderCountdown();
    }, 1000);
}());
