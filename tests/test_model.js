const assert = require("node:assert/strict")
const Model = require("../Model.js")

const status = Model.parseStatus(JSON.stringify({
  ok: true,
  installed: true,
  tresors: [{ name: "Projects" }]
}))
assert.equal(status.installed, true)
assert.equal(status.tresors.length, 1)

const malformed = Model.parseStatus("not json")
assert.equal(malformed.ok, false)
assert.equal(malformed.snapshotValid, false)
assert.equal(malformed.tresors.length, 0)

assert.equal(Model.transferSummary(0, 0), "Up to date")
assert.equal(Model.transferSummary(2, 0), "2 files left")
assert.equal(Model.transferSummary(0, 1), "1 sync error")

const groups = Model.tresorGroups([
  { id: "cloud", synced: false },
  { id: "local", synced: true },
  { id: "linked", synced: false }
])
assert.deepEqual(groups.synced.map(row => row.id), ["local"])
assert.deepEqual(groups.notSynced.map(row => row.id), ["cloud", "linked"])

assert.equal(Model.tresorMeta({ synced: false }), "Not synced on this device")
assert.equal(
  Model.tresorMeta({ synced: false, linkedPath: "/sync/Archive", linkedPathUsable: true }),
  "Linked to “Archive” · not synced"
)
assert.equal(
  Model.tresorMeta({ synced: false, linkedPath: "/sync/Archive", linkedPathUsable: false }),
  "Previous folder “Archive” unavailable"
)
assert.equal(
  Model.tresorMeta({ synced: true, status: "syncing", filesLeft: 4, syncPath: "/sync/Projects" }),
  "Synced to “Projects” · 4 files left"
)
assert.equal(
  Model.tresorMeta({ synced: true, status: "idle", syncPath: "/sync/Projects" }),
  "Synced to “Projects”"
)
assert.equal(
  Model.tresorMeta({ synced: false, linkedPath: "/sync/Projects" }, "Starting sync…"),
  "Linked to “Projects” · not synced · Starting sync…"
)
assert.equal(
  Model.tresorMeta({ synced: true, filesLeft: 4, syncPath: "/sync/Projects" }, "Stopping sync…"),
  "Synced to “Projects” · Stopping sync…"
)
