function defaultStatus() {
  return {
    ok: true,
    snapshotValid: false,
    installed: false,
    running: false,
    authenticated: false,
    statusText: "Unavailable",
    account: "",
    accountKey: "",
    restrictionState: "",
    driveMountPath: "",
    tresors: [],
    activeFiles: [],
    completedFiles: [],
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
    parsed.activeFiles = Array.isArray(parsed.activeFiles) ? parsed.activeFiles : []
    parsed.completedFiles = Array.isArray(parsed.completedFiles) ? parsed.completedFiles : []
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

function tresorGroups(tresors) {
  var synced = []
  var notSynced = []
  var rows = Array.isArray(tresors) ? tresors : []
  for (var i = 0; i < rows.length; i++) {
    if (rows[i] && rows[i].synced === true) synced.push(rows[i])
    else if (rows[i]) notSynced.push(rows[i])
  }
  return { synced: synced, notSynced: notSynced }
}

function tresorMeta(tresor, actionStatus) {
  if (!tresor) return ""
  var syncPath = String(tresor.syncPath || "")
  var linkedPath = String(tresor.linkedPath || syncPath)
  var folderPath = tresor.synced === true ? syncPath : linkedPath
  var trimmedPath = folderPath.replace(/\/+$/, "")
  var parts = trimmedPath.split("/")
  var folderName = parts.length > 0 ? parts[parts.length - 1] : ""
  var state = "Not synced on this device"
  if (tresor.synced === true && folderName !== "") state = "Synced to “" + folderName + "”"
  else if (folderName !== "" && tresor.linkedPathUsable === false)
    state = "Previous folder “" + folderName + "” unavailable"
  else if (folderName !== "") state = "Linked to “" + folderName + "” · not synced"

  var activeAction = String(actionStatus || "").trim()
  if (activeAction !== "") return activeAction

  var failures = Math.max(0, Number(tresor.errors || 0))
  var pending = Math.max(0, Number(tresor.filesLeft || 0))
  if (failures > 0) return state + " · " + failures + (failures === 1 ? " error" : " errors")
  if (pending > 0) return state + " · " + pending + (pending === 1 ? " file left" : " files left")
  var transferState = String(tresor.status || "").trim()
  if (transferState !== "" && transferState.toLowerCase() !== "idle"
      && transferState.toLowerCase() !== "unknown") return state + " · " + transferState
  return state
}

if (typeof module !== "undefined") {
  module.exports = {
    defaultStatus: defaultStatus,
    parseStatus: parseStatus,
    transferSummary: transferSummary,
    tresorGroups: tresorGroups,
    tresorMeta: tresorMeta
  }
}
