"""Baustein für Dialoge: Belege (PDF/Foto) einer Rechnung auflisten, hinzufügen, öffnen, entfernen."""

from __future__ import annotations

import atexit
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path

from PyQt6.QtCore import QUrl, pyqtSignal
from PyQt6.QtGui import QDesktopServices
from PyQt6.QtWidgets import QFileDialog, QHBoxLayout, QListWidget, QVBoxLayout, QWidget

from models.attachment import ATTACHMENT_EXTENSIONS
from models.exceptions import ValidationError
from services.attachment_service import AttachmentService, read_attachment_file
from ui.helpers import ErrorGuard, make_button
from ui.widgets.common import muted_label
from utils.formatters import format_file_size

#: Ordner für Belege, die zum Ansehen oder Auslesen vorübergehend als Datei gebraucht werden.
_temp_root: Path | None = None


def attachment_file_filter() -> str:
    """Dateifilter für den Öffnen-Dialog (PDF und Bilder)."""
    patterns = " ".join(f"*{extension}" for extension in ATTACHMENT_EXTENSIONS)
    return f"Belege ({patterns})"


def _write_temp_file(file_name: str, data: bytes) -> Path:
    """Schreibt Belegdaten in eine temporäre Datei, die beim Beenden der Anwendung gelöscht wird."""
    global _temp_root
    if _temp_root is None:
        _temp_root = Path(tempfile.mkdtemp(prefix="nebenkosten_belege_"))
        atexit.register(shutil.rmtree, _temp_root, True)
    # Eigener Unterordner je Datei: gleiche Dateinamen überschreiben sich nicht.
    target = Path(tempfile.mkdtemp(dir=_temp_root)) / Path(file_name).name
    try:
        target.write_bytes(data)
    except OSError as exc:
        raise ValidationError(f"Der Beleg kann nicht zum Öffnen bereitgestellt werden: {exc}") from exc
    return target


@dataclass
class _Entry:
    """Eine Zeile der Liste: gespeicherter Beleg oder noch nicht gespeicherte Datei."""

    file_name: str
    size_bytes: int
    #: ID des gespeicherten Belegs; ``None`` = wird erst beim Speichern der Rechnung angelegt.
    attachment_id: int | None = None
    #: Dateidaten einer noch nicht gespeicherten Datei.
    data: bytes | None = None


class AttachmentsEditor(QWidget):
    """Liste der Belege einer Rechnung mit den Aktionen Hinzufügen, Öffnen, Auslesen, Entfernen.

    Änderungen werden nur vorgemerkt und erst mit :meth:`commit` gespeichert -
    also dann, wenn der Benutzer die Rechnung speichert. Bricht er den Dialog
    ab, bleibt alles beim Alten. So funktioniert der Baustein auch für eine
    neue Rechnung, die noch gar keine ID hat.

    Signals:
        scan_requested: Der markierte Beleg soll ausgelesen werden; Argument
            ist der Pfad einer (ggf. temporären) Datei mit seinem Inhalt.
    """

    scan_requested = pyqtSignal(object)

    def __init__(
        self,
        service: AttachmentService,
        cost_id: int | None,
        scan_available: bool = False,
        parent: QWidget | None = None,
        bundle_id: int | None = None,
    ) -> None:
        """
        Args:
            service: Service der Belege.
            cost_id: Die Rechnung, deren Belege angezeigt werden; ``None`` bei einer neuen.
            bundle_id: Stattdessen die Sammelrechnung, deren Belege angezeigt werden.
            scan_available: ``True`` zeigt die Schaltfläche "Auslesen" (Belegerkennung eingerichtet).
        """
        super().__init__(parent)
        self._service = service
        self._entries: list[_Entry] = []
        self._removed_ids: list[int] = []
        stored = (
            service.list_for_bundle(bundle_id)
            if bundle_id is not None
            else service.list_for_cost(cost_id) if cost_id is not None else []
        )
        self._entries = [_Entry(a.file_name, a.size_bytes, attachment_id=a.id) for a in stored]

        self._list = QListWidget()
        self._list.setMaximumHeight(86)
        self._hint = muted_label("Rechnung als PDF oder Foto hierher ziehen – oder „Hinzufügen …“.")
        self._add_button = make_button("Hinzufügen …", "plus")
        self._open_button = make_button("Öffnen", "eye", tooltip="Beleg im Standardprogramm anzeigen")
        self._scan_button = make_button("Auslesen", "camera", tooltip="Werte aus dem markierten Beleg übernehmen")
        self._scan_button.setVisible(scan_available)
        self._remove_button = make_button("Entfernen", "trash", "danger")

        buttons = QHBoxLayout()
        buttons.setContentsMargins(0, 0, 0, 0)
        for button in (self._add_button, self._open_button, self._scan_button):
            buttons.addWidget(button)
        buttons.addStretch(1)
        buttons.addWidget(self._remove_button)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        layout.addWidget(self._list)
        layout.addWidget(self._hint)
        layout.addLayout(buttons)

        self._add_button.clicked.connect(self._choose_files)
        self._open_button.clicked.connect(self._open_selected)
        self._scan_button.clicked.connect(self._request_scan)
        self._remove_button.clicked.connect(self._remove_selected)
        self._list.itemDoubleClicked.connect(lambda *_: self._open_selected())
        self._list.itemSelectionChanged.connect(self._update_buttons)
        self._refresh()

    # ------------------------------------------------------------------ #
    # Öffentliche Schnittstelle
    # ------------------------------------------------------------------ #
    def file_names(self) -> list[str]:
        """Die Dateinamen aller Belege, wie sie nach dem Speichern vorhanden wären."""
        return [entry.file_name for entry in self._entries]

    def add_file(self, path: Path) -> bool:
        """Merkt eine Datei als neuen Beleg vor.

        Returns:
            ``True``, wenn die Datei angenommen wurde (sonst wurde der Grund angezeigt).
        """
        data = b""
        with ErrorGuard(self) as guard:
            data = read_attachment_file(path)
        if not guard.ok:
            return False
        self._entries.append(_Entry(path.name, len(data), data=data))
        self._refresh()
        self._list.setCurrentRow(len(self._entries) - 1)
        return True

    def commit(self, cost_id: int | None = None, bundle_id: int | None = None) -> None:
        """Speichert die vorgemerkten Änderungen für die (jetzt gespeicherte) Rechnung bzw. Sammelrechnung.

        Raises:
            AppError: Wenn ein Beleg nicht gespeichert werden kann.
        """
        for attachment_id in self._removed_ids:
            self._service.delete(attachment_id)
        self._removed_ids.clear()
        for entry in self._entries:
            if entry.attachment_id is None and entry.data is not None:
                if bundle_id is not None:
                    saved = self._service.add_to_bundle(bundle_id, entry.file_name, entry.data)
                else:
                    assert cost_id is not None
                    saved = self._service.add(cost_id, entry.file_name, entry.data)
                entry.attachment_id = saved.id
                entry.data = None
        self._refresh()

    # ------------------------------------------------------------------ #
    # Interna
    # ------------------------------------------------------------------ #
    def _refresh(self) -> None:
        """Baut die Liste neu auf."""
        row = self._list.currentRow()
        self._list.clear()
        for entry in self._entries:
            suffix = "" if entry.attachment_id is not None else "  ·  wird mit der Rechnung gespeichert"
            self._list.addItem(f"{entry.file_name}  ·  {format_file_size(entry.size_bytes)}{suffix}")
        self._list.setVisible(bool(self._entries))
        self._hint.setVisible(not self._entries)
        if self._entries:
            self._list.setCurrentRow(min(max(row, 0), len(self._entries) - 1))
        self._update_buttons()

    def _selected(self) -> _Entry | None:
        row = self._list.currentRow()
        return self._entries[row] if 0 <= row < len(self._entries) else None

    def _update_buttons(self) -> None:
        has_selection = self._selected() is not None
        for button in (self._open_button, self._scan_button, self._remove_button):
            button.setEnabled(has_selection)

    def _choose_files(self) -> None:
        """Öffnet den Dateidialog; mehrere Dateien auf einmal sind möglich."""
        file_names, _ = QFileDialog.getOpenFileNames(self, "Belege auswählen", "", attachment_file_filter())
        for file_name in file_names:
            self.add_file(Path(file_name))

    def _remove_selected(self) -> None:
        entry = self._selected()
        if entry is None:
            return
        if entry.attachment_id is not None:
            self._removed_ids.append(entry.attachment_id)
        self._entries.remove(entry)
        self._refresh()

    def _materialize(self, entry: _Entry) -> Path:
        """Stellt den Inhalt eines Belegs als Datei bereit (für Anzeige oder Erkennung)."""
        data = entry.data
        if data is None and entry.attachment_id is not None:
            data = self._service.load(entry.attachment_id).file_data
        return _write_temp_file(entry.file_name, data or b"")

    def _open_selected(self) -> None:
        """Zeigt den markierten Beleg im Standardprogramm des Betriebssystems."""
        entry = self._selected()
        if entry is None:
            return
        with ErrorGuard(self):
            path = self._materialize(entry)
            if not QDesktopServices.openUrl(QUrl.fromLocalFile(str(path))):
                raise ValidationError("Für diesen Dateityp ist kein Programm zum Anzeigen eingerichtet.")

    def _request_scan(self) -> None:
        entry = self._selected()
        if entry is None:
            return
        with ErrorGuard(self):
            self.scan_requested.emit(self._materialize(entry))
