(function () {
    var refreshEverySeconds = 60;
    var remaining = refreshEverySeconds;
    var countdown = document.querySelector("[data-refresh-countdown]");
    var refreshButton = document.querySelector("[data-refresh-now]");

    function updateCountdown() {
        if (countdown) {
            countdown.textContent = remaining + "s";
        }
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

    document.querySelectorAll(".alert-toast").forEach(function (toast, index) {
        window.setTimeout(function () {
            toast.classList.add("is-hidden");
        }, 6500 + (index * 900));
        window.setTimeout(function () {
            if (toast.parentNode) {
                toast.parentNode.removeChild(toast);
            }
        }, 7200 + (index * 900));
    });
}());
