const fs = require("node:fs");
const path = require("node:path");
const root = path.resolve(__dirname, "..", "..");
const template = fs.readFileSync(path.join(root, "templates/dashboard/endpoint_detail.html"), "utf8");
const base = fs.readFileSync(path.join(root, "templates/base.html"), "utf8");
const js = fs.readFileSync(path.join(root, "static/js/endpoint_detail.js"), "utf8");
const css = fs.readFileSync(path.join(root, "static/css/endpoint_detail_refinement.css"), "utf8");

function assert(condition, message) {
  if (!condition) throw new Error(message);
}

assert(template.includes("css/endpoint_detail_refinement.css' %}?v=20261110-2"), "detail refinement stylesheet uses the latest cache key");
assert(template.includes("js/endpoint_detail.js' %}?v=20261109-1"), "endpoint detail script uses a fresh cache key");
assert(!base.includes("endpoint_detail_refinement.css"), "refinement stylesheet must not be global");
assert(!template.includes("block icon_script"), "endpoint template must inherit the Lucide loader when terminal is enabled");
assert(base.includes("unpkg.com/lucide@latest/dist/umd/lucide.min.js"), "base page provides Lucide to the endpoint detail");

for (const action of ["update_agent", "repair_agent"]) {
  const button = template.slice(template.indexOf(`data-endpoint-action="${action}"`));
  assert(button.slice(0, button.indexOf("</button>")).includes("<i data-lucide="), `${action} keeps its icon`);
  assert(button.slice(0, button.indexOf("</button>")).includes("<span>"), `${action} has visible text`);
}

const agentRender = js.slice(js.indexOf('setSlot("agent-body"'), js.indexOf('setSlotBadge("security-badge"'));
assert(agentRender.includes('className: "endpoint-agent-last-update"'), "last update job occupies the next grid cell after lifecycle");
assert(!agentRender.includes("Proximo heartbeat"), "next heartbeat is removed from the overview card only");
assert(!agentRender.includes("Rollout/motivo"), "rollout reason is removed from the overview card only");

const inventoryRender = js.slice(js.indexOf('setSlot("inventory-body"'), js.indexOf('setSlot("disks-body"'));
assert(inventoryRender.includes('className: "endpoint-inventory-primary-capacity"'), "primary disk capacity has its own grid cell");
assert(inventoryRender.includes('className: "endpoint-inventory-sync-fact"'), "inventory synchronization sits beside disk capacity");
assert(!inventoryRender.includes('Última sincronização do inventário"') || !inventoryRender.includes("endpoint-fact-wide"),
  "inventory synchronization must not span the full card width");

assert(css.split(/\r?\n/).filter(line => line.trim().startsWith(".") || line.trim().startsWith("*")).length === 0,
  "refinement selectors are scoped beneath page-endpoint-detail");
assert(css.includes(".endpoint-detail-shell .panel:hover"), "non-interactive endpoint cards have no decorative hover");
assert(css.includes(".endpoint-fact-list > div {"), "fact tiles have one page-scoped visual rule");
assert(css.includes("--endpoint-detail-tile-surface") && css.includes("--endpoint-detail-tile-border"), "fact tile surfaces use page-scoped tokens");
const factTileRule = css.slice(css.indexOf(".endpoint-detail-shell .endpoint-fact-list > div,"), css.indexOf(".endpoint-detail-shell .endpoint-fact-list > div:hover,"));
assert(factTileRule && !factTileRule.includes("!important"), "the canonical fact tile rule does not rely on important overrides");
assert(css.includes("text-transform: none;") && css.includes("font-weight: 600;"),
  "fact labels use normal capitalization and subordinate weight");
assert(css.includes(".endpoint-event-row:hover"), "interactive event rows retain their hover affordance");
assert(css.includes("::-webkit-scrollbar-thumb:hover"), "scrollbar hover is retained only on the scrollbar");
assert(css.includes("scrollbar-width: thin"), "Firefox scrollbar styling is present");
assert(!css.includes("height: 280px") && !css.includes("height: 320px"), "refinement does not impose fixed card heights");

console.log("endpoint detail refinement isolation and presentation checks passed");
