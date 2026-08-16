import QtQuick
import Quickshell
import Quickshell.Io
import "Model.js" as Model

Item {
  id: root

  property var settings: ({})

  property bool installed: false
  property bool running: false
  property bool authenticated: false
  property bool refreshing: false
  property bool filePollingActive: false
  property string statusText: "Checking…"
  property string account: ""
  property string accountKey: ""
  property string restrictionState: ""
  property string driveMountPath: ""
  property var tresors: []
  property var activeFiles: []
  property var completedFiles: []
  property int filesLeft: 0
  property int errors: 0
  property string actionStatus: ""
  property string lastError: ""
  property string actionTresorId: ""
  property string actionTresorStatus: ""
  property int desiredRunning: -1
  property int desiredTresorSync: -1
  property int desiredTresorLinked: -1
  property var queuedAction: null

  readonly property bool active: desiredRunning === -1 ? running : desiredRunning === 1
  readonly property int refreshIntervalSec: intSetting("refreshIntervalSec", 30, 10, 3600)
  readonly property int fileHistoryLimit: intSetting("fileHistoryLimit", 50, 10, 200)
  readonly property bool actionBlocked: actionProcess.running || queuedAction !== null
  readonly property bool busy: actionBlocked
  readonly property bool processBusy: actionBlocked || statusProcess.running || fileStatusProcess.running
  readonly property string helperPath: localPath(Qt.resolvedUrl("status.py"))

  property string _actionOutput: ""
  property string _actionError: ""

  function localPath(url) {
    var value = String(url || "")
    if (value.indexOf("file://") === 0) value = value.substring(7)
    return decodeURIComponent(value)
  }

  function setting(name, fallback) {
    var value = settings ? settings[name] : undefined
    return value === undefined || value === null ? fallback : value
  }

  function intSetting(name, fallback, minimum, maximum) {
    var value = parseInt(String(setting(name, fallback)), 10)
    if (!isFinite(value)) value = fallback
    return Math.max(minimum, Math.min(maximum, value))
  }

  function elide(text) {
    var value = String(text || "").replace(/\s+/g, " ").trim()
    return value.length > 160 ? value.substring(0, 157) + "…" : value
  }

  function refresh() {
    if (processBusy || helperPath === "") return
    refreshing = true
    statusProcess.command = statusCommand()
    statusProcess.running = true
  }

  function statusCommand() {
    return ["python3", helperPath, "status", "--history-limit", String(fileHistoryLimit)]
  }

  function pollFiles() {
    if (!filePollingActive || processBusy || helperPath === "") return
    fileStatusProcess.command = statusCommand()
    fileStatusProcess.running = true
  }

  function applyStatus(raw) {
    var parsed = Model.parseStatus(raw)
    if (parsed.lastError) lastError = String(parsed.lastError)
    else if (actionProcess.running === false) lastError = ""
    if (parsed.snapshotValid === false) return

    installed = parsed.installed === true
    running = parsed.running === true
    authenticated = parsed.authenticated === true
    if (desiredRunning !== -1 && running === (desiredRunning === 1)) desiredRunning = -1
    statusText = String(parsed.statusText || (installed ? "Unavailable" : "Not installed"))
    account = String(parsed.account || "")
    accountKey = String(parsed.accountKey || "")
    restrictionState = String(parsed.restrictionState || "")
    driveMountPath = String(parsed.driveMountPath || "")
    var nextTresors = parsed.tresors || []
    if (JSON.stringify(tresors) !== JSON.stringify(nextTresors)) tresors = nextTresors
    var nextActiveFiles = parsed.activeFiles || []
    if (JSON.stringify(activeFiles) !== JSON.stringify(nextActiveFiles)) activeFiles = nextActiveFiles
    var nextCompletedFiles = parsed.completedFiles || []
    if (JSON.stringify(completedFiles) !== JSON.stringify(nextCompletedFiles)) completedFiles = nextCompletedFiles
    filesLeft = Math.max(0, Number(parsed.filesLeft || 0))
    errors = Math.max(0, Number(parsed.errors || 0))

    if (actionTresorId !== "" && !actionProcess.running) {
      for (var i = 0; i < tresors.length; i++) {
        if (String(tresors[i].id || "") !== actionTresorId) continue
        var syncReached = desiredTresorSync !== -1
          && tresors[i].synced === (desiredTresorSync === 1)
        var linkedReached = desiredTresorLinked !== -1
          && (String(tresors[i].linkedPath || "") !== "") === (desiredTresorLinked === 1)
        if (syncReached || linkedReached) {
          clearTresorAction()
          break
        }
      }
    }
  }

  function runAction(actionArguments, label, tresorId, desired, tresorStatus) {
    if (!installed || actionBlocked || helperPath === "") return false
    _actionOutput = ""
    _actionError = ""
    actionStatus = label || ""
    actionTresorId = tresorId || ""
    actionTresorStatus = tresorStatus || ""
    desiredTresorSync = desired === undefined ? -1 : desired
    desiredTresorLinked = -1
    if (statusProcess.running || fileStatusProcess.running) {
      var queuedArguments = []
      for (var i = 0; i < actionArguments.length; i++)
        queuedArguments.push(String(actionArguments[i]))
      queuedAction = queuedArguments
      return true
    }
    launchAction(actionArguments)
    return true
  }

  function launchAction(actionArguments) {
    var command = ["python3", helperPath]
    for (var i = 0; i < actionArguments.length; i++)
      command.push(String(actionArguments[i]))
    actionProcess.command = command
    actionProcess.running = true
  }

  function launchQueuedAction() {
    if (queuedAction === null || actionProcess.running
        || statusProcess.running || fileStatusProcess.running) return
    var pending = queuedAction
    queuedAction = null
    launchAction(pending)
  }

  function toggleDaemon() {
    if (!installed || actionBlocked) return
    if (active) {
      desiredRunning = 0
      runAction(["stop"], "Stopping Tresorit…", "", -1)
    } else {
      desiredRunning = 1
      runAction(["start"], "Starting Tresorit…", "", -1)
    }
  }

  function toggleTresor(tresor) {
    if (!tresor) return "invalid-target"
    return requestTresorSync(String(tresor.id || tresor.name || ""), tresor.synced !== true)
  }

  function findTresor(id) {
    var target = String(id || "")
    for (var i = 0; i < tresors.length; i++) {
      if (String(tresors[i].id || "") === target) return tresors[i]
    }
    return null
  }

  function rejectAction(message, code) {
    lastError = ""
    actionStatus = message
    statusClearTimer.restart()
    return code
  }

  function requestTresorSync(id, enable) {
    if (!installed) return "not-installed"
    if (actionBlocked) return "busy"
    if (!running) return "daemon-stopped"
    var tresor = findTresor(id)
    if (!tresor) return "invalid-target"
    if (tresor.synced === enable) return "unchanged"
    if (enable && tresor.canStart !== true)
      return rejectAction("Choose a local sync folder first", "needs-folder")
    if (!enable && tresor.canStop !== true)
      return rejectAction("The current sync folder could not be safely remembered", "state-unavailable")
    var targetId = String(tresor.id || tresor.name || "")
    if (targetId === "") return "invalid-target"
    var launched = runAction(
      [enable ? "sync-start" : "sync-stop", targetId],
      enable ? "Starting tresor sync…" : "Stopping tresor sync…",
      targetId,
      enable ? 1 : 0,
      enable ? "Starting sync…" : "Stopping sync…"
    )
    return launched ? "queued" : "busy"
  }

  function setTresorFolder(id, path, expectedAccountKey, confirmedOperation) {
    if (!installed) return "not-installed"
    if (actionBlocked) return rejectAction("Another Tresorit action is already running", "busy")
    if (!running) return rejectAction("Start Tresorit before choosing a sync folder", "daemon-stopped")
    var tresor = findTresor(id)
    if (!tresor)
      return rejectAction("That tresor is no longer available for setup", "invalid-target")
    var targetId = String(tresor.id || tresor.name || "")
    var localFolder = String(path || "")
    var accountFingerprint = String(expectedAccountKey || "")
    var operation = String(confirmedOperation || "")
    if (targetId === "" || localFolder === "" || accountFingerprint === ""
        || (operation !== "start" && operation !== "move"))
      return rejectAction("The selected sync setup is no longer valid", "invalid-target")
    var launched = runAction(
      [operation === "move" ? "sync-move" : "sync-start-at", targetId, localFolder, accountFingerprint],
      operation === "move" ? "Changing tresor folder…" : "Starting tresor sync…",
      targetId,
      1,
      operation === "move" ? "Changing folder…" : "Starting sync…"
    )
    return launched ? "queued" : "busy"
  }

  function forgetTresorFolder(id, expectedAccountKey) {
    if (!installed) return "not-installed"
    if (actionBlocked) return rejectAction("Another Tresorit action is already running", "busy")
    if (!running) return rejectAction("Start Tresorit before forgetting a linked folder", "daemon-stopped")
    var tresor = findTresor(id)
    if (!tresor) return rejectAction("That tresor is no longer available", "invalid-target")
    if (tresor.synced === true)
      return rejectAction("Stop syncing this tresor before forgetting its folder", "still-synced")
    if (String(tresor.linkedPath || "") === "") return "unchanged"
    var accountFingerprint = String(expectedAccountKey || "")
    if (accountFingerprint === "")
      return rejectAction("The current Tresorit account could not be verified", "invalid-account")
    var launched = runAction(
      ["forget-path", String(tresor.id || ""), accountFingerprint],
      "Forgetting linked folder…",
      String(tresor.id || ""),
      -1,
      "Forgetting folder…"
    )
    if (launched) desiredTresorLinked = 0
    return launched ? "queued" : "busy"
  }

  function clearTresorAction() {
    actionTresorId = ""
    actionTresorStatus = ""
    desiredTresorSync = -1
    desiredTresorLinked = -1
  }

  function tresorActionStatus(tresor) {
    if (actionTresorId === "" || String((tresor && tresor.id) || "") !== actionTresorId)
      return ""
    return actionTresorStatus
  }

  function tresorIsBusy(tresor) {
    return actionProcess.running && actionTresorId !== ""
      && String((tresor && tresor.id) || "") === actionTresorId
  }

  function tresorIsSynced(tresor) {
    if (!tresor) return false
    if (String(tresor.id || "") === actionTresorId && desiredTresorSync !== -1)
      return desiredTresorSync === 1
    return tresor.synced === true
  }

  function tresorIsLinked(tresor) {
    if (!tresor) return false
    if (String(tresor.id || "") === actionTresorId && desiredTresorLinked !== -1)
      return desiredTresorLinked === 1
    return String(tresor.linkedPath || "") !== ""
  }

  function tresorCanToggle(tresor) {
    if (!running || !tresor) return false
    return tresor.synced === true ? tresor.canStop === true : tresor.canStart === true
  }

  function login() {
    if (!installed || !running || authenticated || helperPath === "") return
    Quickshell.execDetached([
      "uwsm-app",
      "--",
      "xdg-terminal-exec",
      "--app-id=org.omarchy.terminal",
      "--title=Tresorit Login",
      "--hold",
      "--",
      "python3",
      helperPath,
      "login"
    ])
  }

  function openTresor(tresor) {
    var path = String((tresor && (tresor.syncPath
      || (tresor.linkedPathUsable === true ? tresor.linkedPath : ""))) || "")
    if (path !== "") Quickshell.execDetached(["uwsm-app", "--", "xdg-open", path])
  }

  function openFile(file) {
    var path = String((file && file.canOpen === true && file.localPath) || "")
    if (path !== "") Quickshell.execDetached(["uwsm-app", "--", "xdg-open", path])
  }

  Timer {
    interval: root.refreshIntervalSec * 1000
    repeat: true
    running: true
    triggeredOnStart: true
    onTriggered: root.refresh()
  }

  Timer {
    interval: 2000
    repeat: true
    running: root.filePollingActive
    triggeredOnStart: true
    onTriggered: root.pollFiles()
  }

  Timer {
    id: delayedRefresh
    interval: 900
    repeat: false
    onTriggered: root.refresh()
  }

  Timer {
    id: settleTimer
    property int ticks: 0
    interval: 1400
    repeat: true
    onTriggered: {
      ticks += 1
      root.refresh()
      if (ticks >= 5) {
        stop()
        ticks = 0
        root.desiredRunning = -1
        root.clearTresorAction()
      }
    }
  }

  Timer {
    id: statusClearTimer
    interval: 2500
    repeat: false
    onTriggered: root.actionStatus = ""
  }

  Process {
    id: statusProcess
    running: false
    command: []
    stdout: StdioCollector { id: statusStdout; waitForEnd: true }
    stderr: StdioCollector { id: statusStderr; waitForEnd: true }
    onExited: function(exitCode) {
      root.refreshing = false
      if (exitCode === 0) root.applyStatus(statusStdout.text)
      else root.lastError = root.elide(statusStderr.text || statusStdout.text || "Could not read Tresorit status")
      root.launchQueuedAction()
    }
  }

  Process {
    id: fileStatusProcess
    running: false
    command: []
    stdout: StdioCollector { id: fileStatusStdout; waitForEnd: true }
    stderr: StdioCollector { id: fileStatusStderr; waitForEnd: true }
    onExited: function(exitCode) {
      if (exitCode === 0) root.applyStatus(fileStatusStdout.text)
      else root.lastError = root.elide(
        fileStatusStderr.text || fileStatusStdout.text || "Could not read Tresorit status"
      )
      root.launchQueuedAction()
    }
  }

  Process {
    id: actionProcess
    running: false
    command: []
    stdout: StdioCollector { id: actionStdout; waitForEnd: true }
    stderr: StdioCollector { id: actionStderr; waitForEnd: true }
    onExited: function(exitCode) {
      root._actionOutput = String(actionStdout.text || "")
      root._actionError = String(actionStderr.text || "")
      if (exitCode !== 0) {
        root.desiredRunning = -1
        root.clearTresorAction()
        root.lastError = root.elide(root._actionError || root._actionOutput || "Tresorit command failed")
        root.actionStatus = root.lastError
      } else {
        root.lastError = ""
        statusClearTimer.restart()
      }
      settleTimer.ticks = 0
      settleTimer.restart()
      delayedRefresh.restart()
    }
  }
}
