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
  property string focusSection: "header"
  property int rowIndex: 0

  readonly property color foreground: bar ? bar.foreground : Color.foreground
  readonly property color urgent: bar ? bar.urgent : Color.urgent
  readonly property color dim: Qt.darker(foreground, 1.55)
  readonly property string fontFamily: bar ? bar.fontFamily : Style.font.family
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
    if (!tresorit.running) return "Stopped"
    if (!tresorit.authenticated) return "Login required"
    if (root.restricted) return tresorit.restrictionState
    if (tresorit.lastError !== "") return "Status unavailable"
    return Model.transferSummary(tresorit.filesLeft, tresorit.errors)
  }

  implicitWidth: button.implicitWidth
  implicitHeight: button.implicitHeight

  function ensureCursor() {
    if (!tresorit.authenticated || tresorit.tresors.length === 0) focusSection = "header"
    if (rowIndex >= tresorit.tresors.length) rowIndex = Math.max(0, tresorit.tresors.length - 1)
    if (rowIndex < 0) rowIndex = 0
  }

  function setHeaderCursor() {
    cursorActive = true
    focusSection = "header"
    if (panelFlick) panelFlick.contentY = 0
  }

  function setRowCursor(index) {
    cursorActive = true
    focusSection = "rows"
    rowIndex = index
    scrollCursorIntoView()
  }

  function moveCursor(dx, dy) {
    if (!cursorActive) {
      cursorActive = true
      ensureCursor()
      return
    }
    ensureCursor()
    if (dy === 0) return
    if (focusSection === "header") {
      if (dy > 0 && tresorit.tresors.length > 0) setRowCursor(0)
      return
    }
    if (dy < 0 && rowIndex === 0) setHeaderCursor()
    else setRowCursor(Math.max(0, Math.min(tresorit.tresors.length - 1, rowIndex + dy)))
  }

  function activateCursor() {
    if (!cursorActive) return
    if (focusSection === "header") {
      if (!tresorit.authenticated) tresorit.openApp()
      else if (tresorit.installed) tresorit.toggleDaemon()
      else tresorit.openApp()
    } else if (focusSection === "rows" && rowIndex < tresorit.tresors.length) {
      tresorit.openTresor(tresorit.tresors[rowIndex])
    }
  }

  function scrollCursorIntoView() {
    if (!panelFlick || focusSection !== "rows" || rowIndex >= rowColumn.children.length) return
    var item = rowColumn.children[rowIndex]
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
    focusSection = "header"
    rowIndex = 0
    if (panelFlick) panelFlick.contentY = 0
    tresorit.refresh()
    Qt.callLater(function() { keyCatcher.forceActiveFocus() })
  }

  Service {
    id: tresorit
    settings: root.settings
  }

  Connections {
    target: tresorit
    function onTresorsChanged() { root.ensureCursor() }
    function onAuthenticatedChanged() { root.ensureCursor() }
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
                 && root.focusSection === "rows" && root.rowIndex < tresorit.tresors.length)
          tresorit.toggleTresor(tresorit.tresors[root.rowIndex])
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
        ScrollBar.vertical: ScrollBar { policy: ScrollBar.AsNeeded }

        Column {
          id: column
          width: panelFlick.width
          spacing: Style.space(12)

          CursorSurface {
            id: header
            width: parent.width
            hasCursor: root.cursorActive && root.focusSection === "header"
            foreground: root.foreground
            implicitHeight: hero.implicitHeight + Style.space(8)

            MouseArea {
              anchors.fill: parent
              hoverEnabled: true
              onEntered: root.setHeaderCursor()
              onClicked: {
                if (!tresorit.authenticated) tresorit.openApp()
                else if (tresorit.installed) tresorit.toggleDaemon()
                else tresorit.openApp()
              }
              Accessible.role: Accessible.Button
              Accessible.name: tresorit.authenticated ? "Tresorit daemon control" : "Open Tresorit"
            }

            PanelHero {
              id: hero
              anchors.left: parent.left
              anchors.right: parent.right
              anchors.verticalCenter: parent.verticalCenter
              anchors.leftMargin: Style.space(4)
              anchors.rightMargin: Style.space(4)
              title: "Tresorit"
              meta: root.heroMeta
              detail: tresorit.authenticated ? String(tresorit.tresors.length) : ""
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
                  hasCursor: header.hasCursor
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

          Text {
            visible: tresorit.actionStatus !== "" || tresorit.lastError !== ""
            width: parent.width
            text: tresorit.actionStatus !== "" ? tresorit.actionStatus : tresorit.lastError
            color: tresorit.lastError !== "" && tresorit.actionStatus === "" ? root.urgent : root.dim
            font.family: root.fontFamily
            font.pixelSize: Style.font.bodySmall
            wrapMode: Text.WordWrap
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
            visible: tresorit.authenticated
            width: parent.width
            spacing: Style.space(10)

            PanelSectionHeader {
              text: "TRESORS"
              foreground: root.foreground
              fontFamily: root.fontFamily
            }

            Text {
              visible: tresorit.tresors.length === 0
              width: parent.width
              text: "No tresors found."
              color: root.dim
              font.family: root.fontFamily
              font.pixelSize: Style.font.body
              horizontalAlignment: Text.AlignHCenter
            }

            Column {
              id: rowColumn
              visible: tresorit.tresors.length > 0
              width: parent.width
              spacing: Style.space(5)

              Repeater {
                model: tresorit.tresors
                TresorRow {
                  required property var modelData
                  required property int index
                  width: rowColumn.width
                  tresor: modelData
                  rowNumber: index
                }
              }
            }
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

    hasCursor: root.cursorActive && root.focusSection === "rows" && root.rowIndex === rowNumber
    current: synced
    foreground: root.foreground
    implicitHeight: rowContent.implicitHeight + Style.space(12)

    MouseArea {
      anchors.left: parent.left
      anchors.right: parent.right
      anchors.rightMargin: Style.space(58)
      anchors.top: parent.top
      anchors.bottom: parent.bottom
      hoverEnabled: true
      cursorShape: Qt.PointingHandCursor
      onEntered: root.setRowCursor(tresorRow.rowNumber)
      onClicked: tresorit.openTresor(tresorRow.tresor)
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
          text: Model.tresorMeta(tresorRow.tresor)
          color: root.dim
          font.family: root.fontFamily
          font.pixelSize: Style.font.caption
          elide: Text.ElideMiddle
        }
      }

      ToggleSwitch {
        id: syncSwitch
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
              : "Choose a sync folder in Tresorit first")
          fontFamily: root.fontFamily
        }
      }
    }
  }
}
