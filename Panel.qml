import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import Quickshell
import Quickshell.Io
import qs.Commons
import qs.Ui
import "Model.js" as Model

Panel {
  id: root
  moduleName: "michaelspori.tresorit"
  ipcTarget: "michaelspori.tresorit"
  manageIpc: false

  property bool cursorActive: false
  property int selectedTabIndex: 0
  property string focusSection: "header"
  property int rowIndex: 0
  property int fileRowIndex: 0
  property int reconciledActiveFileCount: 0
  property string focusedTresorId: ""
  property string focusedFileKey: ""
  property var pendingTresor: null
  property string pendingSyncPath: ""
  property string pendingAccountKey: ""
  property string pendingFolderOperation: ""
  property string _folderPickerOutput: ""
  property string _folderPickerError: ""
  property string _confirmSyncError: ""
  property bool _fileReconcilePending: false

  readonly property color foreground: bar ? bar.foreground : Color.foreground
  readonly property color urgent: bar ? bar.urgent : Color.urgent
  readonly property color dim: Qt.darker(foreground, 1.55)
  readonly property string fontFamily: bar ? bar.fontFamily : Style.font.family
  readonly property var tresorGroups: Model.tresorGroups(tresorit.tresors)
  readonly property var syncedTresors: tresorGroups.synced || []
  readonly property var notSyncedTresors: tresorGroups.notSynced || []
  readonly property var displayTresors: syncedTresors.concat(notSyncedTresors)
  readonly property var activeFiles: tresorit.activeFiles || []
  readonly property var completedFiles: tresorit.completedFiles || []
  readonly property var displayFiles: activeFiles.concat(completedFiles)
  onDisplayFilesChanged: scheduleFileReconcile()
  onDisplayTresorsChanged: {
    reconcileTresorRows()
    ensureCursor()
  }
  readonly property bool restricted: tresorit.restrictionState !== ""
    && tresorit.restrictionState.toLowerCase() !== "normal"
  readonly property color iconColor: tresorit.errors > 0 || restricted
      ? urgent
      : (tresorit.authenticated && tresorit.active ? foreground : dim)
  readonly property color barIconColor: tresorit.errors > 0 || restricted
    ? urgent
    : (tresorit.authenticated && tresorit.active ? barForeground : Qt.darker(barForeground, 1.55))
  readonly property string heroMeta: {
    if (!tresorit.installed) return "CLI not installed"
    if (tresorit.actionStatus !== "") return tresorit.actionStatus
    if (tresorit.lastError !== "") return tresorit.lastError
    if (!tresorit.running) return "Stopped"
    if (!tresorit.authenticated) return "Login required"
    if (root.restricted) return tresorit.restrictionState
    return Model.transferSummary(tresorit.filesLeft, tresorit.errors)
  }

  implicitWidth: button.implicitWidth
  implicitHeight: button.implicitHeight

  function ensureCursor() {
    if (!tresorit.authenticated) {
      focusSection = "header"
      focusedTresorId = ""
      focusedFileKey = ""
      return
    }
    if ((selectedTabIndex === 0 && focusSection === "files")
        || (selectedTabIndex === 1 && focusSection === "rows")) {
      focusSection = "tabs"
    }
    if ((focusSection === "rows" && displayTresors.length === 0)
        || (focusSection === "files" && displayFiles.length === 0)) {
      focusSection = "tabs"
    }
    if (focusSection === "rows" && focusedTresorId !== "") {
      for (var i = 0; i < displayTresors.length; i++) {
        if (String(displayTresors[i].id || "") === focusedTresorId) {
          rowIndex = i
          return
        }
      }
    }
    if (focusSection === "files" && focusedFileKey !== "") {
      for (var fileIndex = 0; fileIndex < displayFiles.length; fileIndex++) {
        if (String(displayFiles[fileIndex].key || "") === focusedFileKey) {
          fileRowIndex = fileIndex
          return
        }
      }
    }
    if (rowIndex >= displayTresors.length) rowIndex = Math.max(0, displayTresors.length - 1)
    if (rowIndex < 0) rowIndex = 0
    if (fileRowIndex >= displayFiles.length) fileRowIndex = Math.max(0, displayFiles.length - 1)
    if (fileRowIndex < 0) fileRowIndex = 0
    if (focusSection === "rows" && rowIndex < displayTresors.length)
      focusedTresorId = String(displayTresors[rowIndex].id || "")
    if (focusSection === "files" && fileRowIndex < displayFiles.length)
      focusedFileKey = String(displayFiles[fileRowIndex].key || "")
  }

  function reconcileTresorRows() {
    var desired = displayTresors

    for (var targetIndex = 0; targetIndex < desired.length; targetIndex++) {
      var tresor = desired[targetIndex]
      var stableId = String(tresor.id || "")
      var currentIndex = -1

      for (var candidateIndex = targetIndex; candidateIndex < tresorRowModel.count; candidateIndex++) {
        if (String(tresorRowModel.get(candidateIndex).stableId || "") === stableId) {
          currentIndex = candidateIndex
          break
        }
      }

      if (currentIndex < 0) {
        tresorRowModel.insert(targetIndex, { "stableId": stableId, "tresor": tresor })
      } else {
        if (currentIndex !== targetIndex) tresorRowModel.move(currentIndex, targetIndex, 1)
        tresorRowModel.setProperty(targetIndex, "tresor", tresor)
      }
    }

    while (tresorRowModel.count > desired.length)
      tresorRowModel.remove(tresorRowModel.count - 1)
  }

  function reconcileFileRows(model, desired) {
    for (var targetIndex = 0; targetIndex < desired.length; targetIndex++) {
      var file = desired[targetIndex]
      var stableKey = String(file.key || "")
      var currentIndex = -1

      for (var candidateIndex = targetIndex; candidateIndex < model.count; candidateIndex++) {
        if (String(model.get(candidateIndex).stableKey || "") === stableKey) {
          currentIndex = candidateIndex
          break
        }
      }

      if (currentIndex < 0) {
        model.insert(targetIndex, { "stableKey": stableKey, "file": file })
      } else {
        if (currentIndex !== targetIndex) model.move(currentIndex, targetIndex, 1)
        model.setProperty(targetIndex, "file", file)
      }
    }

    while (model.count > desired.length) model.remove(model.count - 1)
  }

  function reconcileFiles() {
    reconcileFileRows(fileRowModel, displayFiles)
    reconciledActiveFileCount = activeFiles.length
    ensureCursor()
  }

  function scheduleFileReconcile() {
    if (_fileReconcilePending) return
    _fileReconcilePending = true
    Qt.callLater(function() {
      _fileReconcilePending = false
      reconcileFiles()
    })
  }

  function selectTab(index) {
    var nextIndex = Math.max(0, Math.min(1, index))
    if (selectedTabIndex === nextIndex && focusSection === "tabs") return
    selectedTabIndex = nextIndex
    focusSection = "tabs"
    focusedTresorId = ""
    focusedFileKey = ""
    if (panelFlick) panelFlick.contentY = 0
    if (nextIndex === 1) tresorit.refresh()
  }

  function setHeaderCursor() {
    cursorActive = true
    focusSection = "header"
    focusedTresorId = ""
    focusedFileKey = ""
    if (panelFlick) panelFlick.contentY = 0
  }

  function setRowCursor(index) {
    cursorActive = true
    focusSection = "rows"
    rowIndex = index
    focusedTresorId = index >= 0 && index < displayTresors.length
      ? String(displayTresors[index].id || "") : ""
    focusedFileKey = ""
    scrollCursorIntoView()
  }

  function setFileCursor(index) {
    cursorActive = true
    focusSection = "files"
    fileRowIndex = index
    focusedFileKey = index >= 0 && index < displayFiles.length
      ? String(displayFiles[index].key || "") : ""
    focusedTresorId = ""
    scrollCursorIntoView()
  }

  function moveCursor(dx, dy) {
    if (!cursorActive) {
      cursorActive = true
      ensureCursor()
      return
    }
    ensureCursor()
    if (dx !== 0 && tresorit.authenticated) {
      selectTab(dx > 0 ? 1 : 0)
      return
    }
    if (dy === 0) return
    if (focusSection === "header") {
      if (dy > 0 && tresorit.authenticated) focusSection = "tabs"
      return
    }
    if (focusSection === "tabs") {
      if (dy < 0) setHeaderCursor()
      else if (selectedTabIndex === 0 && displayTresors.length > 0) setRowCursor(0)
      else if (selectedTabIndex === 1 && displayFiles.length > 0) setFileCursor(0)
      return
    }
    if (focusSection === "rows") {
      if (dy < 0 && rowIndex === 0) focusSection = "tabs"
      else setRowCursor(Math.max(0, Math.min(displayTresors.length - 1, rowIndex + dy)))
      return
    }
    if (focusSection === "files") {
      if (dy < 0 && fileRowIndex === 0) focusSection = "tabs"
      else setFileCursor(Math.max(0, Math.min(displayFiles.length - 1, fileRowIndex + dy)))
    }
  }

  function activateCursor() {
    if (!cursorActive) return
    if (focusSection === "header") {
      if (tresorit.installed) tresorit.toggleDaemon()
      else tresorit.openApp()
    } else if (focusSection === "rows" && rowIndex < displayTresors.length) {
      activateTresorRow(displayTresors[rowIndex])
    } else if (focusSection === "files" && fileRowIndex < displayFiles.length) {
      tresorit.openFile(displayFiles[fileRowIndex])
    }
  }

  function activateTresorRow(tresor) {
    if (!tresor) return
    if (tresor.synced === true || tresor.linkedPathUsable === true) tresorit.openTresor(tresor)
    else chooseSyncFolder(tresor)
  }

  function activeFileMeta(file) {
    if (!file) return ""
    var parts = []
    var tresorName = String(file.tresorName || "").trim()
    var status = String(file.status || "").trim()
    var progress = String(file.progress || "").trim()
    if (tresorName !== "") parts.push(tresorName)
    if (status !== "" && status !== "-") parts.push(status)
    if (progress !== "" && progress !== "-" && progress !== status) parts.push(progress)
    return parts.join(" · ")
  }

  function completedFileMeta(file) {
    if (!file) return ""
    var parts = []
    var tresorName = String(file.tresorName || "").trim()
    if (tresorName !== "") parts.push(tresorName)
    var timestamp = String(file.completedAt || "")
    var completed = new Date(timestamp)
    if (timestamp !== "" && !isNaN(completed.getTime()))
      parts.push(Qt.formatDateTime(completed, "dd.MM.yyyy · HH:mm"))
    return parts.join(" · ")
  }

  function chooseSyncFolder(tresor) {
    if (!tresor || !tresorit.running || tresorit.busy
        || folderPickerProcess.running || confirmSyncProcess.running) return
    pendingTresor = tresor
    pendingSyncPath = ""
    pendingAccountKey = tresorit.accountKey
    pendingFolderOperation = tresor.synced === true ? "move" : "start"
    _folderPickerOutput = ""
    _folderPickerError = ""
    var currentPath = String(tresor.syncPath || tresor.linkedPath || "")
    var pickerPath = currentPath !== "" && (tresor.synced === true || tresor.linkedPathUsable === true)
      ? currentPath.replace(/\/+$/, "") + "/"
      : Quickshell.env("HOME") + "/"
    folderPickerProcess.command = [
      "zenity",
      "--file-selection",
      "--directory",
      "--title=Choose a sync folder for " + String(tresor.name || "tresor"),
      "--filename=" + pickerPath
    ]
    root.close()
    folderPickerProcess.running = true
  }

  function toggleOrChooseTresor(tresor) {
    if (!tresor) return
    if (tresor.synced !== true && tresor.canStart !== true) chooseSyncFolder(tresor)
    else tresorit.toggleTresor(tresor)
  }

  function clearPendingSync() {
    pendingTresor = null
    pendingSyncPath = ""
    pendingAccountKey = ""
    pendingFolderOperation = ""
  }

  function scrollCursorIntoView() {
    if (!panelFlick) return
    var item = null
    if (focusSection === "rows") item = tresorRepeater.itemAt(rowIndex)
    else if (focusSection === "files") item = fileRepeater.itemAt(fileRowIndex)
    if (!item) return
    Qt.callLater(function() {
      if (!item) return
      var point = item.mapToItem(panelFlick.contentItem, 0, 0)
      var top = point.y
      var bottom = top + item.height
      if (top < panelFlick.contentY) panelFlick.contentY = Math.max(0, top - Style.space(6))
      else if (bottom > panelFlick.contentY + panelFlick.height)
        panelFlick.contentY = Math.min(panelFlick.contentHeight - panelFlick.height, bottom + Style.space(6) - panelFlick.height)
    })
  }

  onOpenedChanged: if (opened) {
    cursorActive = false
    selectedTabIndex = 0
    focusSection = "header"
    rowIndex = 0
    fileRowIndex = 0
    focusedTresorId = ""
    focusedFileKey = ""
    if (panelFlick) panelFlick.contentY = 0
    tresorit.refresh()
    Qt.callLater(function() { keyCatcher.forceActiveFocus() })
  }

  Service {
    id: tresorit
    settings: root.settings
    filePollingActive: root.opened && root.selectedTabIndex === 1
  }

  ListModel {
    id: tresorRowModel
    dynamicRoles: true
  }

  ListModel {
    id: fileRowModel
    dynamicRoles: true
  }

  Process {
    id: folderPickerProcess
    running: false
    command: []
    stdout: StdioCollector {
      id: folderPickerStdout
      waitForEnd: true
      onStreamFinished: root._folderPickerOutput = text
    }
    stderr: StdioCollector {
      id: folderPickerStderr
      waitForEnd: true
      onStreamFinished: root._folderPickerError = text
    }
    onExited: function(exitCode) {
      var stdout = String(folderPickerStdout.text || root._folderPickerOutput || "")
      var stderr = String(folderPickerStderr.text || root._folderPickerError || "")
      if (exitCode === 0 && root.pendingTresor) {
        var path = stdout.replace(/\r?\n$/, "")
        if (path !== "") {
          root.pendingSyncPath = path
          root._confirmSyncError = ""
          confirmSyncProcess.command = [
            "zenity",
            "--question",
            "--no-markup",
            "--default-cancel",
            "--title=" + (root.pendingFolderOperation === "move" ? "Change sync folder?" : "Start tresor sync?"),
            "--ok-label=" + (root.pendingFolderOperation === "move" ? "Move" : "Sync"),
            "--cancel-label=Cancel",
            "--text=" + (root.pendingFolderOperation === "move"
              ? "Move “" + String(root.pendingTresor.name || "tresor") + "” sync to:\n"
                + path + "\n\nThe current sync will be stopped and restarted. Existing folder contents can be merged and uploaded."
              : "Sync “" + String(root.pendingTresor.name || "tresor") + "” to:\n"
                + path + "\n\nExisting folder contents can be merged and uploaded.")
          ]
          confirmSyncProcess.running = true
          return
        }
      } else if (exitCode !== 1) {
        tresorit.rejectAction(
          tresorit.elide(stderr || "Could not open the folder chooser"),
          "folder-picker-failed"
        )
      }
      root.clearPendingSync()
      root.open()
    }
  }

  Process {
    id: confirmSyncProcess
    running: false
    command: []
    stdout: StdioCollector { waitForEnd: true }
    stderr: StdioCollector {
      id: confirmSyncStderr
      waitForEnd: true
      onStreamFinished: root._confirmSyncError = text
    }
    onExited: function(exitCode) {
      var stderr = String(confirmSyncStderr.text || root._confirmSyncError || "")
      if (exitCode === 0 && root.pendingTresor && root.pendingSyncPath !== "") {
        tresorit.setTresorFolder(
          String(root.pendingTresor.id || ""),
          root.pendingSyncPath,
          root.pendingAccountKey,
          root.pendingFolderOperation
        )
      } else if (exitCode !== 1) {
        tresorit.rejectAction(
          tresorit.elide(stderr || "Could not confirm the sync folder"),
          "folder-confirmation-failed"
        )
      }
      root.clearPendingSync()
      root.open()
    }
  }

  Connections {
    target: tresorit
    function onAuthenticatedChanged() { root.ensureCursor() }
  }

  Component.onCompleted: {
    reconcileTresorRows()
    reconcileFiles()
  }

  IpcHandler {
    target: root.ipcTarget
    function open(): void { root.open() }
    function close(): void { root.close() }
    function show(): void { root.open() }
    function hide(): void { root.close() }
    function toggle(): void { root.toggle() }
    function refresh(): string { tresorit.refresh(); return "ok" }
    function status(): string { return tresorit.statusText }
    function startSync(id: string): string { return tresorit.requestTresorSync(id, true) }
    function stopSync(id: string): string { return tresorit.requestTresorSync(id, false) }
  }

  BarIconButton {
    id: button
    anchors.fill: parent
    bar: root.bar
    iconComponent: Component {
      Item {
        TresoritIcon {
          anchors.centerIn: parent
          iconSize: Style.space(12)
          color: root.barIconColor
          opacity: tresorit.active ? 1.0 : 0.6
        }
      }
    }
    onPressed: function(buttonCode) {
      if (buttonCode === Qt.RightButton) tresorit.refresh()
      else if (buttonCode === Qt.MiddleButton) tresorit.openApp()
      else root.toggle()
    }
  }

  KeyboardPanel {
    id: panel
    anchorItem: button
    owner: root
    bar: root.bar
    open: root.opened
    focusTarget: keyCatcher
    contentWidth: panel.fittedContentWidth(Style.space(400))
    contentHeight: panel.fittedContentHeight(column.implicitHeight, Style.space(580))

    PanelKeyCatcher {
      id: keyCatcher
      anchors.fill: parent
      onMoveRequested: function(dx, dy) { root.moveCursor(dx, dy) }
      onActivateRequested: root.activateCursor()
      onCloseRequested: root.close()
      onTabRequested: function(direction) { root.switchPanel(direction) }
      onTextKey: function(text) {
        if (text === "r" || text === "R") tresorit.refresh()
        else if (text === "o" || text === "O") tresorit.openApp()
        else if ((text === "s" || text === "S") && root.cursorActive
                 && root.focusSection === "rows" && root.rowIndex < root.displayTresors.length)
          root.toggleOrChooseTresor(root.displayTresors[root.rowIndex])
        else if ((text === "f" || text === "F") && root.cursorActive
                 && root.focusSection === "rows" && root.rowIndex < root.displayTresors.length)
          root.chooseSyncFolder(root.displayTresors[root.rowIndex])
      }

      Flickable {
        id: panelFlick
        anchors.fill: parent
        contentWidth: width
        contentHeight: column.implicitHeight
        clip: true
        boundsBehavior: Flickable.StopAtBounds
        flickableDirection: Flickable.VerticalFlick
        interactive: contentHeight > height
        ScrollBar.vertical: ScrollBar { policy: ScrollBar.AlwaysOff }

        Column {
          id: column
          width: panelFlick.width
          spacing: Style.space(14)

          Item {
            id: header
            width: parent.width
            implicitHeight: hero.implicitHeight
            readonly property bool switchHasCursor: root.cursorActive
              && root.focusSection === "header" && tresorit.installed

            PanelHero {
              id: hero
              anchors.left: parent.left
              anchors.right: parent.right
              anchors.verticalCenter: parent.verticalCenter
              anchors.leftMargin: Style.space(4)
              anchors.rightMargin: Style.space(4)
              title: "Tresorit"
              meta: root.heroMeta
              foreground: root.foreground
              fontFamily: root.fontFamily
              iconOpacity: tresorit.active ? 1.0 : 0.55
              iconComponent: Component {
                TresoritIcon { iconSize: Style.font.display; color: root.iconColor }
              }
              trailingControl: Component {
                ToggleSwitch {
                  visible: tresorit.installed
                  checked: tresorit.active
                  busy: tresorit.busy
                  hasCursor: header.switchHasCursor
                  foreground: root.foreground
                  onHovered: function(on) { if (on) root.setHeaderCursor() }
                  onToggled: tresorit.toggleDaemon()
                  Accessible.role: Accessible.CheckBox
                  Accessible.name: "Tresorit daemon running"
                  PanelToolTip {
                    visible: parent.containsMouse
                    text: tresorit.active ? "Stop Tresorit" : "Start Tresorit"
                    fontFamily: root.fontFamily
                  }
                }
              }
            }
          }

          Row {
            id: tabSwitch
            visible: tresorit.authenticated
            width: parent.width
            spacing: Style.spacing.md
            readonly property real cellWidth: (width - spacing) / 2

            Button {
              width: tabSwitch.cellWidth
              text: "Tresors"
              selected: root.selectedTabIndex === 0
              hasCursor: root.cursorActive && root.focusSection === "tabs"
                && root.selectedTabIndex === 0
              bordered: true
              foreground: root.foreground
              fontFamily: root.fontFamily
              fontSize: Style.font.bodySmall
              verticalPadding: Style.spacing.controlPaddingY
              onClicked: {
                root.cursorActive = true
                root.selectTab(0)
              }
              onHovered: function(isHovered) {
                if (isHovered) {
                  root.cursorActive = true
                  root.focusSection = "tabs"
                }
              }
            }

            Button {
              width: tabSwitch.cellWidth
              text: "Files"
              selected: root.selectedTabIndex === 1
              hasCursor: root.cursorActive && root.focusSection === "tabs"
                && root.selectedTabIndex === 1
              bordered: true
              foreground: root.foreground
              fontFamily: root.fontFamily
              fontSize: Style.font.bodySmall
              verticalPadding: Style.spacing.controlPaddingY
              onClicked: {
                root.cursorActive = true
                root.selectTab(1)
              }
              onHovered: function(isHovered) {
                if (isHovered) {
                  root.cursorActive = true
                  root.focusSection = "tabs"
                }
              }
            }
          }

          PanelSeparator {
            visible: tresorit.authenticated
            foreground: root.foreground
          }

          CursorSurface {
            visible: !tresorit.authenticated
            width: parent.width
            foreground: root.foreground
            implicitHeight: loginRow.implicitHeight + Style.space(12)

            MouseArea {
              anchors.fill: parent
              hoverEnabled: true
              cursorShape: Qt.PointingHandCursor
              onClicked: tresorit.openApp()
            }

            RowLayout {
              id: loginRow
              anchors.left: parent.left
              anchors.right: parent.right
              anchors.verticalCenter: parent.verticalCenter
              anchors.leftMargin: Style.space(10)
              anchors.rightMargin: Style.space(10)
              spacing: Style.space(8)

              Text {
                text: tresorit.installed ? "Open Tresorit to sign in" : "Install or open Tresorit"
                color: root.foreground
                font.family: root.fontFamily
                font.pixelSize: Style.font.body
                Layout.fillWidth: true
              }
              PanelActionButton {
                iconText: "󰐕"
                tooltipText: "Open Tresorit"
                foreground: root.foreground
                fontFamily: root.fontFamily
                onClicked: tresorit.openApp()
              }
            }
          }

          Column {
            visible: tresorit.authenticated && root.selectedTabIndex === 0
            width: parent.width
            spacing: Style.space(10)

            Text {
              visible: root.displayTresors.length === 0
              width: parent.width
              text: "No tresors found."
              color: root.dim
              font.family: root.fontFamily
              font.pixelSize: Style.font.body
              horizontalAlignment: Text.AlignHCenter
            }

            Column {
              id: rowColumn
              visible: root.displayTresors.length > 0
              width: parent.width
              spacing: Style.space(10)

              Repeater {
                id: tresorRepeater
                model: tresorRowModel
                Column {
                  required property var tresor
                  required property int index
                  width: rowColumn.width
                  spacing: Style.space(10)
                  readonly property bool startsNotSynced: index === root.syncedTresors.length
                  readonly property bool startsSection: index === 0 || startsNotSynced

                  PanelSeparator {
                    visible: startsNotSynced && root.syncedTresors.length > 0
                    height: visible ? implicitHeight : 0
                    foreground: root.foreground
                  }

                  PanelSectionHeader {
                    visible: startsSection
                    height: visible ? implicitHeight : 0
                    text: index < root.syncedTresors.length ? "SYNCED" : "NOT SYNCED"
                    foreground: root.foreground
                    fontFamily: root.fontFamily
                  }

                  TresorRow {
                    width: parent.width
                    tresor: parent.tresor
                    rowNumber: parent.index
                  }
                }
              }
            }
          }

          Column {
            visible: tresorit.authenticated && root.selectedTabIndex === 1
            width: parent.width
            spacing: Style.space(10)

            Text {
              visible: root.displayFiles.length === 0
              width: parent.width
              text: "No file activity observed yet."
              color: root.dim
              font.family: root.fontFamily
              font.pixelSize: Style.font.body
              horizontalAlignment: Text.AlignHCenter
            }

            Column {
              id: fileRowItems
              visible: root.displayFiles.length > 0
              width: parent.width
              spacing: Style.space(10)

              Repeater {
                id: fileRepeater
                model: fileRowModel

                Column {
                  required property var file
                  required property int index
                  width: fileRowItems.width
                  spacing: Style.space(10)
                  readonly property bool startsCompleted: index === root.reconciledActiveFileCount
                  readonly property bool startsSection: index === 0 || startsCompleted
                  readonly property bool transferring: index < root.reconciledActiveFileCount

                  PanelSeparator {
                    visible: startsCompleted && root.reconciledActiveFileCount > 0
                    height: visible ? implicitHeight : 0
                    foreground: root.foreground
                  }

                  PanelSectionHeader {
                    visible: startsSection
                    height: visible ? implicitHeight : 0
                    text: parent.transferring ? "SYNCING" : "RECENTLY SYNCED"
                    foreground: root.foreground
                    fontFamily: root.fontFamily
                  }

                  FileRow {
                    width: parent.width
                    file: parent.file
                    transferring: parent.transferring
                    rowNumber: parent.index
                  }
                }
              }
            }
          }
        }
      }
    }
  }

  component FileRow: CursorSurface {
    id: fileRow
    property var file: null
    property bool transferring: false
    property int rowNumber: 0
    readonly property real progressPercent: file && file.progressPercent !== undefined
      && file.progressPercent !== null && isFinite(Number(file.progressPercent))
      ? Number(file.progressPercent) : -1

    hasCursor: root.cursorActive && root.focusSection === "files" && root.fileRowIndex === rowNumber
    current: transferring
    foreground: root.foreground
    implicitHeight: fileContent.implicitHeight + Style.space(12)

    MouseArea {
      anchors.fill: parent
      hoverEnabled: true
      cursorShape: fileRow.file && fileRow.file.canOpen === true
        ? Qt.PointingHandCursor : Qt.ArrowCursor
      onEntered: root.setFileCursor(fileRow.rowNumber)
      onClicked: tresorit.openFile(fileRow.file)
    }

    RowLayout {
      id: fileContent
      anchors.left: parent.left
      anchors.right: parent.right
      anchors.verticalCenter: parent.verticalCenter
      anchors.leftMargin: Style.space(10)
      anchors.rightMargin: Style.space(10)
      spacing: Style.space(8)

      Text {
        text: "󰈔"
        color: root.foreground
        font.family: root.fontFamily
        font.pixelSize: Style.font.icon
        Layout.alignment: Qt.AlignVCenter
      }

      ColumnLayout {
        Layout.fillWidth: true
        spacing: Style.space(2)

        Text {
          Layout.fillWidth: true
          text: String((fileRow.file && fileRow.file.fileName) || "Unnamed file")
          color: root.foreground
          font.family: root.fontFamily
          font.pixelSize: Style.font.body
          elide: Text.ElideMiddle
        }

        Text {
          Layout.fillWidth: true
          text: fileRow.transferring
            ? root.activeFileMeta(fileRow.file) : root.completedFileMeta(fileRow.file)
          color: root.dim
          font.family: root.fontFamily
          font.pixelSize: Style.font.caption
          elide: Text.ElideRight
        }

        Rectangle {
          visible: fileRow.transferring && fileRow.progressPercent >= 0
          Layout.fillWidth: true
          Layout.preferredHeight: Style.space(2)
          color: Qt.rgba(root.dim.r, root.dim.g, root.dim.b, 0.28)
          radius: height / 2

          Rectangle {
            width: parent.width * Math.max(0, Math.min(100, fileRow.progressPercent)) / 100
            height: parent.height
            color: root.foreground
            radius: parent.radius
          }
        }
      }
    }
  }

  component TresorRow: CursorSurface {
    id: tresorRow
    property var tresor: null
    property int rowNumber: 0
    readonly property bool synced: tresorit.tresorIsSynced(tresor)
    readonly property bool canResume: !synced && tresor && tresor.canStart === true
    readonly property bool linked: synced || (tresor && String(tresor.linkedPath || "") !== "")
    readonly property bool hasToggle: synced || canResume

    hasCursor: root.cursorActive && root.focusSection === "rows" && root.rowIndex === rowNumber
    current: synced
    foreground: root.foreground
    implicitHeight: rowContent.implicitHeight + Style.space(12)

    MouseArea {
      anchors.left: parent.left
      anchors.right: parent.right
      anchors.rightMargin: Style.space(tresorRow.hasToggle ? 96 : 58)
      anchors.top: parent.top
      anchors.bottom: parent.bottom
      hoverEnabled: true
      cursorShape: Qt.PointingHandCursor
      onEntered: root.setRowCursor(tresorRow.rowNumber)
      onClicked: root.activateTresorRow(tresorRow.tresor)
    }

    RowLayout {
      id: rowContent
      anchors.left: parent.left
      anchors.right: parent.right
      anchors.verticalCenter: parent.verticalCenter
      anchors.leftMargin: Style.space(10)
      anchors.rightMargin: Style.space(6)
      spacing: Style.space(8)

      Text {
        text: synced ? "󰉖" : "󰝦"
        color: tresor && Number(tresor.errors || 0) > 0 ? root.urgent : root.foreground
        font.family: root.fontFamily
        font.pixelSize: Style.font.icon
        Layout.alignment: Qt.AlignVCenter
      }

      ColumnLayout {
        Layout.fillWidth: true
        spacing: Style.space(1)
        Text {
          Layout.fillWidth: true
          text: String((tresorRow.tresor && tresorRow.tresor.name) || "Unnamed tresor")
          color: root.foreground
          font.family: root.fontFamily
          font.pixelSize: Style.font.body
          elide: Text.ElideRight
        }
        Text {
          Layout.fillWidth: true
          text: Model.tresorMeta(tresorRow.tresor, tresorit.tresorActionStatus(tresorRow.tresor))
          color: root.dim
          font.family: root.fontFamily
          font.pixelSize: Style.font.caption
          elide: Text.ElideMiddle
        }
      }

      PanelActionButton {
        iconText: tresorRow.linked ? "󰣞" : "󰉋"
        tooltipText: tresorit.running
          ? (tresorRow.linked ? "Change the sync folder" : "Choose a local sync folder")
          : "Start Tresorit first"
        foreground: root.foreground
        fontFamily: root.fontFamily
        Layout.alignment: Qt.AlignVCenter
        onHovered: function(on) { if (on) root.setRowCursor(tresorRow.rowNumber) }
        onClicked: root.chooseSyncFolder(tresorRow.tresor)
      }

      ToggleSwitch {
        id: syncSwitch
        visible: tresorRow.synced || (tresorRow.tresor && tresorRow.tresor.canStart === true)
        checked: tresorRow.synced
        busy: tresorit.tresorIsBusy(tresorRow.tresor)
        enabled: tresorit.tresorCanToggle(tresorRow.tresor)
        hasCursor: false
        foreground: root.foreground
        Layout.alignment: Qt.AlignVCenter
        onHovered: function(on) { if (on) root.setRowCursor(tresorRow.rowNumber) }
        onToggled: tresorit.toggleTresor(tresorRow.tresor)
        Accessible.role: Accessible.CheckBox
        Accessible.name: "Sync " + String((tresorRow.tresor && tresorRow.tresor.name) || "tresor")
        PanelToolTip {
          visible: parent.containsMouse
          text: tresorRow.synced
            ? (tresorRow.tresor && tresorRow.tresor.canStop
              ? "Stop syncing this tresor"
              : "Sync folder state is unavailable")
            : (tresorRow.tresor && tresorRow.tresor.canStart
              ? "Resume syncing to its previous folder"
              : "Choose a local sync folder")
          fontFamily: root.fontFamily
        }
      }
    }
  }
}
