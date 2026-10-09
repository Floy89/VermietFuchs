"""Hilfen für Oberflächen-Tests: Drag-and-drop von Dateien nachstellen."""

from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import QMimeData, QPoint, QPointF, Qt, QUrl
from PyQt6.QtGui import QDragEnterEvent, QDropEvent
from PyQt6.QtWidgets import QApplication, QWidget


def drop_files(widget: QWidget, paths: list[Path]) -> bool:
    """Stellt nach, dass Dateien aus dem Explorer auf ``widget`` gezogen und losgelassen werden.

    Die Ereignisse gehen - wie bei einem echten Drag-and-drop - an genau das
    Widget unter dem Mauszeiger, also z. B. an ein Eingabefeld und nicht an
    den Dialog darum herum. Anschließend werden ausstehende Ereignisse
    verarbeitet, weil die Anwendung auf einen Drop zeitversetzt reagiert.

    Returns:
        ``True``, wenn das Ziel die Dateien angenommen hat.
    """
    mime = QMimeData()
    mime.setUrls([QUrl.fromLocalFile(str(path)) for path in paths])
    position = QPoint(5, 5)
    buttons, modifiers = Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier

    enter = QDragEnterEvent(position, Qt.DropAction.CopyAction, mime, buttons, modifiers)
    QApplication.sendEvent(widget, enter)
    accepted = enter.isAccepted()
    if accepted:
        drop = QDropEvent(QPointF(position), Qt.DropAction.CopyAction, mime, buttons, modifiers)
        QApplication.sendEvent(widget, drop)
    QApplication.processEvents()
    return accepted
