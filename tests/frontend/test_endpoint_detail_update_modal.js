const assert = require("assert");
const fs = require("fs");
const path = require("path");

const root = path.resolve(__dirname, "..", "..");
const source = fs.readFileSync(path.join(root, "static", "js", "endpoint_detail.js"), "utf8");
const template = fs.readFileSync(path.join(root, "templates", "dashboard", "endpoint_detail.html"), "utf8");

function contains(pattern, message) {
  assert(pattern.test(source), message);
}

function doesNotContain(pattern, message) {
  assert(!pattern.test(source), message);
}

contains(/function\s+openUpdateModal\s*\(/, "update_agent must open a dedicated modal");
contains(/function\s+submitUpdateFromModal\s*\(/, "modal submit handler must exist");
contains(/body\.set\("release_id",\s*options\.releaseId\)/, "job payload must include explicit release_id");
contains(/body\.set\("force",\s*options\.force\s*\?\s*"true"\s*:\s*"false"\)/, "job payload must include explicit force flag when provided");
contains(/openUpdateModal\(origin\)/, "update_agent action must route through the modal");
contains(/data-update-release-select/, "modal must render an internal release selector");
contains(/data-update-channel-select/, "modal must render a channel selector");
contains(/data-update-channel-save/, "channel change must require its separate explicit save action");
contains(/function\s+saveUpdateChannel\s*\(/, "channel save handler must exist");
contains(/data-force-downgrade/, "downgrade must require explicit advanced force control");
contains(/data-confirm-downgrade/, "downgrade must require an additional confirmation");
contains(/downgrade exige/i, "downgrade validation message must be explicit");
contains(/reason_code/, "API error handling must surface reason_code");
doesNotContain(/function\s+chooseUpdateRelease\s*\(/, "legacy release selector helper must not remain active");
const updateFlowStart = source.indexOf("function openUpdateModal(");
const updateFlowEnd = source.indexOf("function runEndpointAction(", updateFlowStart);
assert(updateFlowStart >= 0 && updateFlowEnd > updateFlowStart, "update flow must be locatable independently from operational actions");
assert(!/window\.prompt\(/.test(source.slice(updateFlowStart, updateFlowEnd)), "update flow must not use prompt-based release selection");
const saveFlowStart = source.indexOf("function saveUpdateChannel(");
const saveFlowEnd = source.indexOf("function submitUpdateFromModal(", saveFlowStart);
assert(saveFlowStart >= 0 && saveFlowEnd > saveFlowStart, "channel save flow must be independently testable");
const saveFlow = source.slice(saveFlowStart, saveFlowEnd);
assert(saveFlow.includes('"/update-policy/"'), "channel change must use the existing update policy contract");
assert(saveFlow.includes('body.set("update_channel", selectedChannel)'), "channel change must submit the selected policy channel");
assert(saveFlow.includes("fetchRealEndpointPayload()"), "successful channel change must reload authoritative release options");
assert(!saveFlow.includes("createRealJob("), "saving a channel must never create an update job");
assert(source.includes('if (!release.eligible)'), "manual update submission must reject releases blocked by backend eligibility");
assert(source.includes('if (release.revoked || release.status === "revoked")'), "revoked releases must remain visibly blocked in the selector details");
assert(source.includes('release.source_channel || release.channel'), "release details must distinguish the signed artifact channel");
assert(template.includes("endpoint_detail.js' %}?v=rc47-channel-update-1"), "endpoint detail JavaScript cache version must be bumped");
assert(template.includes("endpoint_actions_jobs.css' %}?v=rc47-channel-update-1"), "endpoint detail stylesheet cache version must be bumped");

console.log("endpoint_detail update modal static checks passed");
