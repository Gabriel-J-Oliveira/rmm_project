const fs = require("node:fs");
const path = require("node:path");
const root = path.resolve(__dirname, "..", "..");
const template = fs.readFileSync(path.join(root, "templates/dashboard/endpoint_detail.html"), "utf8");
const base = fs.readFileSync(path.join(root, "templates/base.html"), "utf8");
const js = fs.readFileSync(path.join(root, "static/js/endpoint_detail.js"), "utf8");
const css = fs.readFileSync(path.join(root, "static/css/endpoint_detail_refinement.css"), "utf8");
const dashboardCss = fs.readFileSync(path.join(root, "static/css/nightowl.css"), "utf8");

function assert(condition, message) {
  if (!condition) throw new Error(message);
}

assert(template.includes("css/endpoint_detail_refinement.css' %}?v=rc47-05-04b-1"), "detail refinement stylesheet uses the combined RC47-05/04B cache key");
assert(template.includes("js/endpoint_detail.js' %}?v=rc47-04b-endpoint-policies-2"), "endpoint detail script uses a fresh cache key");
assert(!base.includes("endpoint_detail_refinement.css"), "refinement stylesheet must not be global");
assert(template.includes("block icon_script"), "endpoint detail overrides the global icon loader");
assert(template.includes("vendor/lucide/1.71.0/lucide.min.js"), "endpoint detail uses the pinned local Lucide build");
assert(!template.includes("unpkg.com/lucide"), "endpoint detail does not load executable icon code remotely");
assert(base.includes("block icon_script"), "base page keeps the global icon loader as an overridable extension point");

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
assert(css.includes(".endpoint-monitoring .panel:hover") && css.includes("[data-endpoint-tab-panel=\"inventory\"] > .panel:hover"),
  "monitoring and inventory panels explicitly keep a stable hover surface");
assert(css.includes(".endpoint-live-selectors select:focus-visible") && css.includes(".endpoint-live-selectors select:disabled"),
  "live selectors define keyboard focus and disabled states");
assert(css.includes('[data-endpoint-tab-panel="inventory"] .panel:hover') && css.includes("background: var(--endpoint-detail-surface) !important;"),
  "inventory cards override the later important hover rule");
assert(css.includes(".endpoint-fact-list > div {"), "fact tiles have one page-scoped visual rule");
assert(css.includes("--endpoint-detail-tile-surface") && css.includes("--endpoint-detail-tile-border"), "fact tile surfaces use page-scoped tokens");
assert(css.includes("--endpoint-detail-tile-padding: 8px 9px;") && /gap:\s*8px;\s*padding:\s*0;/.test(css), "fact tiles keep their spacing while the list container has no outer padding");
assert(!dashboardCss.includes("body.page-pulse .pulse-health-card:hover"), "dashboard summary cards have no decorative hover state");
assert(/body\.page-pulse \.pulse-health-card\.is-active\s*\{[^}]*border-color:[^}]*background:/s.test(dashboardCss), "active dashboard filter remains visually identified without hover");
assert(!/body\.page-pulse \.pulse-health-card\.is-active\s*\{[^}]*transform:/s.test(dashboardCss), "dashboard summary cards do not lift when active");
const factTileRule = css.slice(css.indexOf(".endpoint-detail-shell .endpoint-fact-list > div,"), css.indexOf(".endpoint-detail-shell .endpoint-fact-list > div:hover,"));
assert(factTileRule && !factTileRule.includes("!important"), "the canonical fact tile rule does not rely on important overrides");
assert(css.includes("text-transform: none;") && css.includes("font-weight: 600;"),
  "fact labels use normal capitalization and subordinate weight");
assert(css.includes(".endpoint-event-row:hover") && css.includes("background: var(--endpoint-detail-inner-surface) !important;"),
  "event cards keep their neutral surface on hover");
assert(css.includes(".endpoint-alert-item:hover") && css.includes("background: rgba(6, 7, 13, .22) !important;"),
  "alert cards keep their original surface on hover");
assert(css.includes(".endpoint-job-item:hover") && css.includes("background: var(--sidebar) !important;"),
  "job cards keep their normal surface on hover");
assert(css.includes(".endpoint-actions-danger:hover") && css.includes("border-color: var(--critical) !important;"),
  "critical action card keeps its fixed risk border without a hover change");
assert(css.includes("font-size: 12.5px;") && css.includes("font-size: 14px;") && css.includes("font-size: 13px;"),
  "only internal labels, values, and supporting text receive the modest type increase");
assert(css.includes("::-webkit-scrollbar-thumb:hover"), "scrollbar hover is retained only on the scrollbar");
assert(css.includes("scrollbar-width: thin"), "Firefox scrollbar styling is present");
assert(!css.includes("height: 280px") && !css.includes("height: 320px"), "refinement does not impose fixed card heights");

console.log("endpoint detail refinement isolation and presentation checks passed");
