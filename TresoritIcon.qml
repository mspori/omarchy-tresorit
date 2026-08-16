import QtQuick
import qs.Commons

Item {
  id: root

  property color color: Color.foreground
  property real iconSize: Style.font.icon

  implicitWidth: iconSize
  implicitHeight: iconSize

  Text {
    anchors.centerIn: parent
    text: "󰅟"
    color: root.color
    font.family: Style.font.family
    font.pixelSize: root.iconSize
  }
}

