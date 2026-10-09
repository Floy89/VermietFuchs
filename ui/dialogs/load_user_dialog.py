"""Startdialog: Benutzer auswählen, anlegen, einbinden oder aus der Liste entfernen."""

from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import QSize, Qt
from PyQt6.QtWidgets import (
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QVBoxLayout,
    QWidget,
)

import config
from app_context import AppContext
from services.user_service import UserService
from ui import icons, styles
from ui.dialogs.new_user_dialog import NewUserDialog
from ui.helpers import ErrorGuard, confirm, make_button
from utils.config_manager import UserEntry

_ENTRY_ROLE = Qt.ItemDataRole.UserRole


class LoadUserDialog(QDialog):
    """Zeigt die Benutzer aus der ``config.json`` und öffnet den gewählten.

    Nach ``exec()`` steht in :attr:`context` der fertige Anwendungskontext -
    oder ``None``, wenn der Dialog abgebrochen wurde.
    """

    def __init__(self, users: UserService, parent: QWidget | None = None) -> None:
        """
        Args:
            users: Service der Benutzerverwaltung.
        """
        super().__init__(parent)
        self._users = users
        #: Kontext des geöffneten Benutzers (``None`` bei Abbruch).
        self.context: AppContext | None = None

        self.setWindowTitle(config.APP_NAME)
        self.setMinimumSize(620, 460)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 22, 24, 20)
        layout.setSpacing(12)

        title_row = QHBoxLayout()
        logo = QLabel()
        logo.setPixmap(icons.pixmap("home", 34, styles.color("primary")))
        title = QLabel("Benutzer wählen")
        title.setObjectName("PageTitle")
        title_row.addWidget(logo)
        title_row.addWidget(title, 1)
        layout.addLayout(title_row)

        hint = QLabel(
            "Jeder Benutzer hat eine eigene Datenbankdatei (benutzer.db). "
            "Sie kann auf einem beliebigen Laufwerk, NAS oder USB-Stick liegen."
        )
        hint.setObjectName("Muted")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        self._list = QListWidget()
        self._list.setIconSize(QSize(22, 22))
        self._list.itemDoubleClicked.connect(lambda *_: self._open_selected())
        self._list.currentItemChanged.connect(lambda *_: self._update_buttons())
        layout.addWidget(self._list, 1)

        self._open_button = make_button("Öffnen", variant="primary")
        self._open_button.setDefault(True)
        self._new_button = make_button("Neuer Benutzer …", "plus")
        self._register_button = make_button(
            "Datenbank einbinden …", "folder", tooltip="Eine vorhandene benutzer.db in die Liste aufnehmen"
        )
        self._remove_button = make_button(
            "Aus Liste entfernen", "trash", "danger", "Entfernt nur den Listeneintrag - die Datenbank bleibt erhalten"
        )
        self._open_button.clicked.connect(self._open_selected)
        self._new_button.clicked.connect(self._create_user)
        self._register_button.clicked.connect(self._register_existing)
        self._remove_button.clicked.connect(self._remove_selected)

        buttons = QHBoxLayout()
        buttons.addWidget(self._new_button)
        buttons.addWidget(self._register_button)
        buttons.addWidget(self._remove_button)
        buttons.addStretch(1)
        buttons.addWidget(self._open_button)
        layout.addLayout(buttons)

        self._reload(select_name=self._safe_last_user())

    # ------------------------------------------------------------------ #
    # Liste
    # ------------------------------------------------------------------ #
    def _safe_last_user(self) -> str | None:
        """Zuletzt verwendeter Benutzer; Fehler beim Lesen werden hier ignoriert."""
        with ErrorGuard(self):
            return self._users.last_user_name()
        return None

    def _reload(self, select_name: str | None = None) -> None:
        """Liest die Benutzerliste neu ein und wählt optional einen Eintrag aus."""
        self._list.clear()
        entries: list[UserEntry] = []
        with ErrorGuard(self):
            entries = self._users.list_users()

        for entry in entries:
            reachable = entry.database_exists
            status = "" if reachable else "   –   nicht erreichbar"
            item = QListWidgetItem(f"{entry.name}{status}\n{entry.database_path}")
            item.setIcon(icons.icon("user", styles.color("primary" if reachable else "disabled")))
            item.setData(_ENTRY_ROLE, entry)
            self._list.addItem(item)
            if select_name and entry.name == select_name:
                self._list.setCurrentItem(item)

        if self._list.currentItem() is None and self._list.count() > 0:
            self._list.setCurrentRow(0)
        self._update_buttons()

    def _selected_entry(self) -> UserEntry | None:
        """Der in der Liste markierte Benutzer."""
        item = self._list.currentItem()
        return item.data(_ENTRY_ROLE) if item else None

    def _update_buttons(self) -> None:
        """Öffnen/Entfernen sind nur mit einer Auswahl möglich."""
        has_selection = self._selected_entry() is not None
        self._open_button.setEnabled(has_selection)
        self._remove_button.setEnabled(has_selection)

    # ------------------------------------------------------------------ #
    # Aktionen
    # ------------------------------------------------------------------ #
    def _open_selected(self) -> None:
        """Öffnet die Datenbank des gewählten Benutzers und schließt den Dialog."""
        entry = self._selected_entry()
        if entry is None:
            return
        with ErrorGuard(self) as guard:
            self.context = self._users.open_user(entry)
        if guard.ok:
            self.accept()

    def _create_user(self) -> None:
        """Legt einen neuen Benutzer an und markiert ihn in der Liste."""
        dialog = NewUserDialog(self._users, self)
        if dialog.exec() and dialog.created_entry:
            self._reload(select_name=dialog.created_entry.name)

    def _register_existing(self) -> None:
        """Nimmt eine vorhandene ``benutzer.db`` in die Liste auf."""
        file_name, _ = QFileDialog.getOpenFileName(
            self, "Datenbank auswählen", "", f"Benutzer-Datenbank ({config.DATABASE_FILE_NAME});;Alle Dateien (*)"
        )
        if not file_name:
            return
        entry: UserEntry | None = None
        with ErrorGuard(self):
            entry = self._users.register_existing(Path(file_name))
        if entry:
            self._reload(select_name=entry.name)

    def _remove_selected(self) -> None:
        """Entfernt den gewählten Benutzer aus der Liste (nicht von der Festplatte)."""
        entry = self._selected_entry()
        if entry is None:
            return
        if not confirm(
            self,
            "Benutzer entfernen",
            f"Benutzer '{entry.name}' aus der Liste entfernen?\n\n"
            f"Die Datenbank bleibt erhalten und kann später wieder eingebunden werden:\n{entry.database_path}",
            confirm_label="Entfernen",
        ):
            return
        with ErrorGuard(self):
            self._users.remove_user(entry.name)
        self._reload()
