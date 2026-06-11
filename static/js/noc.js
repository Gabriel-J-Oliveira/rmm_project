(function () {
    var refreshEverySeconds = 30;
    var remaining = refreshEverySeconds;
    var countdown = document.querySelector("[data-noc-countdown]");
    var refreshButton = document.querySelector("[data-noc-refresh]");

    function renderCountdown() {
        if (countdown) {
            countdown.textContent = remaining + "s";
        }
    }

    if (refreshButton) {
        refreshButton.addEventListener("click", function () {
            window.location.reload();
        });
    }

    renderCountdown();
    window.setInterval(function () {
        remaining -= 1;
        if (remaining <= 0) {
            window.location.reload();
            return;
        }
        renderCountdown();
    }, 1000);
}());
