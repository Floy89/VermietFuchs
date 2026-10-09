"""Dialog zum Anlegen eines neuen Benutzers."""

from __future__ import annotations

from pathlib import Path

from PyQt6.QtWidgets import QFileDialog, QHBoxLayout, QLabel, QLineEdit, QWidget

import config
from models.exceptions import ValidationError
from services.user_service import UserService
from ui.dialogs.base_dialog import FormDialog
from ui.helpers import make_button
from utils.config_manager import UserEntry


class NewUserDialog(FormDialog):
    """Fragt Namen und Speicherort ab und legt Ordner + ``benutzer.db`` an."""

    def __init__(self, users: UserService, parent: QWidget | None = None) -> None:
        """
        Args:
            users: Service der Benutzerverwaltung.
        """
        super().__init__("Neuen Benutzer anlegen", parent, submit_label="Anlegen")
        self._users = users
        #: Der angelegte Benutzer; gesetzt, sobald der Dialog bestätigt wurde.
        self.created_entry: UserEntry | None = None

        self._name_edit = QLineEdit()
        self._name_edit.setPlaceholderText("z. B. Hausverwaltung_Meier")
        self._folder_edit = QLineEdit(str(Path.home() / "Documents" / config.APP_NAME))
        browse_button = make_button("Durchsuchen …", "folder")
        browse_button.clicked.connect(self._browse)
        folder_row = QHBoxLayout()
        folder_row.addWidget(self._folder_edit, 1)
        folder_row.addWidget(browse_button)

        self._preview = QLabel()
        self._preview.setObjectName("Muted")
        self._preview.setWordWrap(True)

        self.form.addRow("Benutzername", self._name_edit)
        self.form.addRow("Speicherort", folder_row)
        self.form.addRow("", self._preview)

        self._name_edit.textChanged.connect(self._update_preview)
        self._folder_edit.textChanged.connect(self._update_preview)
        self._update_preview()

    def _browse(self) -> None:
        """Öffnet die Ordnerauswahl (lokales Laufwerk, NAS, USB-Stick …)."""
        folder = QFileDialog.getExistingDirectory(self, "Speicherort wählen", self._folder_edit.text())
        if folder:
            self._folder_edit.setText(folder)

    def _update_preview(self) -> None:
        """Zeigt an, wo die Datenbankdatei angelegt wird."""
        name = self._name_edit.text().strip() or "<Benutzername>"
        folder = self._folder_edit.text().strip() or "<Speicherort>"
        target = Path(folder) / name / config.DATABASE_FILE_NAME
        self._preview.setText(f"Die Datenbank wird hier angelegt:\n{target}")

    def _apply(self) -> None:
        folder = self._folder_edit.text().strip()
        if not folder:
            raise ValidationError("Bitte einen Speicherort wählen.")
        self.created_entry = self._users.create_user(self._name_edit.text(), Path(folder))
