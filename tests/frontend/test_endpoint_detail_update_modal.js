const assert = require("assert");
const fs = require("fs");
const path = require("path");

const root = path.resolve(__dirname, "..", "..");
const source = fs.readFileSync(path.join(root, "static", "js", "endpoint_detail.js"), "utf8");

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
contains(/data-force-downgrade/, "downgrade must require explicit advanced force control");
contains(/data-confirm-downgrade/, "downgrade must require an additional confirmation");
contains(/downgrade exige/i, "downgrade validation message must be explicit");
contains(/reason_code/, "API error handling must surface reason_code");
doesNotContain(/function\s+chooseUpdateRelease\s*\(/, "legacy release selector helper must not remain active");
doesNotContain(/window\.prompt\(/, "update flow must not use prompt-based release selection");

console.log("endpoint_detail update modal static checks passed");
