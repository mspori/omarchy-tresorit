function defaultStatus() {
  return {
    ok: true,
    installed: false,
    running: false,
    authenticated: false,
    statusText: "Unavailable",
    account: "",
    restrictionState: "",
    driveMountPath: "",
    tresors: [],
    filesLeft: 0,
    errors: 0,
    lastError: ""
  }
}

function parseStatus(raw) {
  var text = String(raw || "").trim()
  if (text === "") return defaultStatus()
  try {
    var parsed = JSON.parse(text)
    if (!parsed || typeof parsed !== "object") return defaultStatus()
    parsed.tresors = Array.isArray(parsed.tresors) ? parsed.tresors : []
    return parsed
  } catch (error) {
    var failed = defaultStatus()
    failed.ok = false
    failed.lastError = "Failed to parse Tresorit status"
    return failed
  }
}

function transferSummary(filesLeft, errors) {
  var pending = Math.max(0, Number(filesLeft || 0))
  var failures = Math.max(0, Number(errors || 0))
  if (failures > 0) return failures + (failures === 1 ? " sync error" : " sync errors")
  if (pending > 0) return pending + (pending === 1 ? " file left" : " files left")
  return "Up to date"
}

function tresorMeta(tresor) {
  if (!tresor) return ""
  var failures = Math.max(0, Number(tresor.errors || 0))
  var pending = Math.max(0, Number(tresor.filesLeft || 0))
  if (failures > 0) return failures + (failures === 1 ? " error" : " errors")
  if (pending > 0) return pending + (pending === 1 ? " file left" : " files left")
  if (tresor.synced !== true) return "Not synced on this device"
  var state = String(tresor.status || "").trim()
  if (state !== "" && state.toLowerCase() !== "idle") return state
  return String(tresor.syncPath || "Synced")
}

if (typeof module !== "undefined") {
  module.exports = {
    defaultStatus: defaultStatus,
    parseStatus: parseStatus,
    transferSummary: transferSummary,
    tresorMeta: tresorMeta
  }
}

