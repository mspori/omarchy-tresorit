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
  property string statusText: "Checking…"
  property string account: ""
  property string restrictionState: ""
  property string driveMountPath: ""
  property var tresors: []
  property int filesLeft: 0
  property int errors: 0
  property string actionStatus: ""
  property string lastError: ""
  property string actionTresorId: ""
  property int desiredRunning: -1
  property int desiredTresorSync: -1

  readonly property bool active: desiredRunning === -1 ? running : desiredRunning === 1
  readonly property int refreshIntervalSec: intSetting("refreshIntervalSec", 30, 10, 3600)
  readonly property bool busy: statusProcess.running || actionProcess.running
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
    if (statusProcess.running || helperPath === "") return
    refreshing = true
    statusProcess.command = ["python3", helperPath, "status"]
    statusProcess.running = true
  }

  function applyStatus(raw) {
    var parsed = Model.parseStatus(raw)
    if (!parsed.ok && parsed.lastError) lastError = String(parsed.lastError)
    else if (actionProcess.running === false) lastError = ""

    installed = parsed.installed === true
    running = parsed.running === true
    authenticated = parsed.authenticated === true
    if (desiredRunning !== -1 && running === (desiredRunning === 1)) desiredRunning = -1
    statusText = String(parsed.statusText || (installed ? "Unavailable" : "Not installed"))
    account = String(parsed.account || "")
    restrictionState = String(parsed.restrictionState || "")
    driveMountPath = String(parsed.driveMountPath || "")
    tresors = parsed.tresors || []
    filesLeft = Math.max(0, Number(parsed.filesLeft || 0))
    errors = Math.max(0, Number(parsed.errors || 0))

    if (actionTresorId !== "") {
      for (var i = 0; i < tresors.length; i++) {
        if (String(tresors[i].id || "") === actionTresorId
            && tresors[i].synced === (desiredTresorSync === 1)) {
          clearTresorAction()
          break
        }
      }
    }
  }

  function runAction(arguments, label, tresorId, desired) {
    if (!installed || actionProcess.running || helperPath === "") return
    _actionOutput = ""
    _actionError = ""
    actionStatus = label || ""
    actionTresorId = tresorId || ""
    desiredTresorSync = desired === undefined ? -1 : desired
    actionProcess.command = ["python3", helperPath].concat(arguments)
    actionProcess.running = true
  }

  function toggleDaemon() {
    if (active) {
      desiredRunning = 0
      runAction(["stop"], "Stopping Tresorit…", "", -1)
    } else {
      desiredRunning = 1
      runAction(["start"], "Starting Tresorit…", "", -1)
    }
  }

  function toggleTresor(tresor) {
    if (!tresor || actionProcess.running || !running) return
    var id = String(tresor.id || tresor.name || "")
    if (id === "") return
    var enable = tresor.synced !== true
    if (enable && tresor.canStart !== true) {
      lastError = "Choose a local sync folder in the Tresorit app first"
      actionStatus = lastError
      statusClearTimer.restart()
      return
    }
    runAction(
      [enable ? "sync-start" : "sync-stop", id],
      enable ? "Starting tresor sync…" : "Stopping tresor sync…",
      id,
      enable ? 1 : 0
    )
  }

  function clearTresorAction() {
    actionTresorId = ""
    desiredTresorSync = -1
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

  function tresorCanToggle(tresor) {
    return running && tresor && (tresor.synced === true || tresor.canStart === true)
  }

  function openApp() {
    Quickshell.execDetached(["uwsm-app", "--", "gtk-launch", "tresorit"])
  }

  function openTresor(tresor) {
    var path = String((tresor && tresor.syncPath) || "")
    if (path !== "") Quickshell.execDetached(["uwsm-app", "--", "nautilus", path])
    else openApp()
  }

  Timer {
    interval: root.refreshIntervalSec * 1000
    repeat: true
    running: true
    triggeredOnStart: true
    onTriggered: root.refresh()
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
