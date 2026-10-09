"""Basisklasse für alle Formular-Dialoge."""

from __future__ import annotations

import logging

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from models.exceptions import AppError

logger = logging.getLogger(__name__)


class FormDialog(QDialog):
    """Dialog mit Formular, Fehlerzeile und den Knöpfen Speichern/Abbrechen.

    Unterklassen

    1. fügen in ``__init__`` ihre Felder zu :attr:`form` hinzu und
    2. implementieren :meth:`_apply`: Eingaben auslesen und speichern.

    Wirft ``_apply`` einen :class:`AppError` (z. B. eine verletzte fachliche
    Regel), bleibt der Dialog offen und zeigt die Meldung direkt über den
    Knöpfen an. Der Benutzer kann korrigieren, ohne seine Eingaben zu
    verlieren.
    """

    def __init__(self, title: str, parent: QWidget | None = None, submit_label: str = "Speichern") -> None:
        """
        Args:
            title: Fenstertitel.
            parent: Elternfenster.
            submit_label: Beschriftung des Bestätigen-Knopfs.
        """
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setModal(True)
        self.setMinimumWidth(460)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(22, 20, 22, 18)
        outer.setSpacing(14)

        #: Das Formular-Layout, in das Unterklassen ihre Felder einfügen.
        self.form = QFormLayout()
        self.form.setLabelAlignment(Qt.AlignmentFlag.AlignLeft)
        self.form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        self.form.setHorizontalSpacing(16)
        self.form.setVerticalSpacing(10)
        outer.addLayout(self.form)

        self._error_label = QLabel()
        self._error_label.setObjectName("ErrorLabel")
        self._error_label.setWordWrap(True)
        self._error_label.hide()
        outer.addWidget(self._error_label)

        buttons = QDialogButtonBox()
        #: Die Knopfleiste; zusätzliche Knöpfe kommen über :meth:`add_action_button` hinzu.
        self.button_box = buttons
        self._submit_button = buttons.addButton(submit_label, QDialogButtonBox.ButtonRole.AcceptRole)
        self._submit_button.setProperty("variant", "primary")
        self._submit_button.setDefault(True)
        buttons.addButton("Abbrechen", QDialogButtonBox.ButtonRole.RejectRole)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        outer.addWidget(buttons)

    def add_action_button(self, button: QPushButton) -> None:
        """Fügt der Knopfleiste einen weiteren Knopf hinzu, der den Dialog nicht von selbst schließt."""
        self.button_box.addButton(button, QDialogButtonBox.ButtonRole.ActionRole)

    def _apply(self) -> None:
        """Liest die Eingaben aus und speichert sie. Von Unterklassen zu implementieren.

        Raises:
            AppError: Wenn die Eingaben ungültig sind oder das Speichern scheitert.
        """
        raise NotImplementedError

    def show_error(self, message: str) -> None:
        """Zeigt eine Fehlermeldung im Dialog an."""
        self._error_label.setText(message)
        self._error_label.show()

    def accept(self) -> None:
        """Speichert und schließt den Dialog - oder zeigt den Fehler an."""
        try:
            self._apply()
        except AppError as exc:
            logger.info("Eingabe abgelehnt: %s", exc)
            self.show_error(str(exc))
            return
        super().accept()
