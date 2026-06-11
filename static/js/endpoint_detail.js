(function () {
    const input = document.getElementById("softwareSearch");
    const table = document.getElementById("softwareTable");
    const chips = Array.from(document.querySelectorAll(".software-chip"));
    const copyButtons = Array.from(document.querySelectorAll("[data-copy]"));

    let activeCategory = "all";

    function applySoftwareFilters() {
        if (!table) {
            return;
        }

        const term = input ? input.value.trim().toLowerCase() : "";
        const rows = Array.from(table.querySelectorAll("tbody tr"));

        rows.forEach(function (row) {
            const text = row.textContent.toLowerCase();
            const category = row.getAttribute("data-category") || "other";
            const matchesText = term.length === 0 || text.includes(term);
            const matchesCategory = activeCategory === "all" || category === activeCategory;
            row.hidden = !(matchesText && matchesCategory);
        });
    }

    if (input) {
        input.addEventListener("input", applySoftwareFilters);
    }

    chips.forEach(function (chip) {
        chip.addEventListener("click", function () {
            activeCategory = chip.getAttribute("data-category") || "all";
            chips.forEach(function (item) {
                item.classList.toggle("active", item === chip);
            });
            applySoftwareFilters();
        });
    });

    copyButtons.forEach(function (button) {
        button.addEventListener("click", async function () {
            const value = button.getAttribute("data-copy") || "";
            if (!value.trim()) {
                return;
            }

            try {
                await navigator.clipboard.writeText(value);
                const original = button.textContent;
                button.textContent = "Copiado";
                button.classList.add("copied");

                window.setTimeout(function () {
                    button.textContent = original;
                    button.classList.remove("copied");
                }, 1200);
            } catch (error) {
                button.classList.add("copy-failed");
            }
        });
    });
})();
