"""Dialoge zur Verwaltung der Kostenarten (Katalog ansehen, eigene anlegen)."""

from __future__ import annotations

from dataclasses import replace

from PyQt6.QtWidgets import QComboBox, QDialog, QHBoxLayout, QLabel, QLineEdit, QVBoxLayout, QWidget

from models.cost_type import CostType
from models.enums import AllocationKey, MeterType
from services.cost_type_service import CostTypeService
from ui.dialogs.base_dialog import FormDialog
from ui.helpers import ErrorGuard, confirm, fill_combo, make_button, select_data
from ui.widgets.common import Column, ObjectTable

#: Schlüssel, die für eigene Kostenarten als Standard wählbar sind.
_CUSTOM_KEYS = (
    AllocationKey.AREA,
    AllocationKey.PERSONS,
    AllocationKey.UNITS,
    AllocationKey.CONSUMPTION,
    AllocationKey.DIRECT,
)


class CostTypeEditDialog(FormDialog):
    """Eigene Kostenart anlegen oder bearbeiten."""

    def __init__(
        self,
        service: CostTypeService,
        cost_type: CostType | None = None,
        parent: QWidget | None = None,
    ) -> None:
        """
        Args:
            service: Service der Kostenarten.
            cost_type: Zu bearbeitende Kostenart; ``None`` legt eine neue an.
        """
        super().__init__("Kostenart bearbeiten" if cost_type else "Neue Kostenart", parent)
        self._service = service
        self._cost_type = cost_type

        self._name_edit = QLineEdit(cost_type.name if cost_type else "")
        self._name_edit.setPlaceholderText("z. B. Wartung Hebeanlage")
        self._meter_combo = QComboBox()
        fill_combo(self._meter_combo, [("Kein Zähler", None), *[(meter.label, meter) for meter in MeterType]])
        self._key_combo = QComboBox()
        fill_combo(self._key_combo, [(key.label, key) for key in _CUSTOM_KEYS])
        hint = QLabel(
            "Der Standard-Schlüssel wird bei neuen Kostenpositionen vorgeschlagen und kann dort "
            "geändert werden. 'Verbrauch' setzt einen Zähler voraus.\n"
            "Hinweis: Umlegen dürfen Sie nur Kosten, die im Mietvertrag vereinbart sind."
        )
        hint.setObjectName("Muted")
        hint.setWordWrap(True)

        self.form.addRow("Bezeichnung", self._name_edit)
        self.form.addRow("Zähler", self._meter_combo)
        self.form.addRow("Standard-Schlüssel", self._key_combo)
        self.form.addRow(hint)

        if cost_type:
            select_data(self._meter_combo, cost_type.meter_type)
            select_data(self._key_combo, cost_type.default_key)

    def _apply(self) -> None:
        values = {
            "name": self._name_edit.text(),
            "meter_type": self._meter_combo.currentData(),
            "default_key": self._key_combo.currentData(),
        }
        cost_type = replace(self._cost_type, **values) if self._cost_type else CostType(**values)
        self._service.save_type(cost_type)


class CostTypeManagerDialog(QDialog):
    """Übersicht aller Kostenarten mit Verwaltung der eigenen Einträge."""

    def __init__(self, service: CostTypeService, parent: QWidget | None = None) -> None:
        """
        Args:
            service: Service der Kostenarten.
        """
        super().__init__(parent)
        self._service = service
        self.setWindowTitle("Kostenarten")
        self.resize(860, 620)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 16)
        layout.setSpacing(10)

        intro = QLabel(
            "Der Katalog enthält die Betriebskostenarten nach § 2 der Betriebskostenverordnung. "
            "Eigene Kostenarten können Sie hier ergänzen."
        )
        intro.setObjectName("Muted")
        intro.setWordWrap(True)
        layout.addWidget(intro)

        self._table: ObjectTable[CostType] = ObjectTable(
            [
                Column("Kostenart", lambda t: t.name),
                Column("Grundlage", lambda t: t.legal_basis),
                Column("Standard-Schlüssel", lambda t: t.default_key.label),
                Column("Zähler", lambda t: t.meter_type.label if t.meter_type else "–"),
                Column(
                    "Herkunft",
                    lambda t: "Katalog" if t.is_builtin else "Eigene",
                    color=lambda t: None if t.is_builtin else "primary",
                ),
            ],
            stretch_column=0,
        )
        layout.addWidget(self._table, 1)

        self._add_button = make_button("Neue Kostenart", "plus", "primary")
        self._edit_button = make_button("Bearbeiten", "edit")
        self._delete_button = make_button("Löschen", "trash", "danger")
        close_button = make_button("Schließen")
        buttons = QHBoxLayout()
        buttons.addWidget(self._add_button)
        buttons.addWidget(self._edit_button)
        buttons.addWidget(self._delete_button)
        buttons.addStretch(1)
        buttons.addWidget(close_button)
        layout.addLayout(buttons)

        self._add_button.clicked.connect(self._add)
        self._edit_button.clicked.connect(self._edit)
        self._delete_button.clicked.connect(self._delete)
        close_button.clicked.connect(self.accept)
        self._table.selection_changed.connect(self._update_buttons)
        self._table.item_activated.connect(lambda *_: self._edit())
        self._reload()

    def _reload(self) -> None:
        """Lädt die Liste neu."""
        with ErrorGuard(self):
            self._table.set_items(self._service.list_types())
        self._update_buttons()

    def _update_buttons(self) -> None:
        """Nur eigene Kostenarten lassen sich bearbeiten und löschen."""
        selected = self._table.selected_item()
        editable = selected is not None and not selected.is_builtin
        self._edit_button.setEnabled(editable)
        self._delete_button.setEnabled(editable)

    def _add(self) -> None:
        if CostTypeEditDialog(self._service, parent=self).exec():
            self._reload()

    def _edit(self) -> None:
        selected = self._table.selected_item()
        if selected is None or selected.is_builtin:
            return
        if CostTypeEditDialog(self._service, selected, self).exec():
            self._reload()

    def _delete(self) -> None:
        selected = self._table.selected_item()
        if selected is None or selected.id is None:
            return
        if not confirm(self, "Kostenart löschen", f"Kostenart '{selected.name}' wirklich löschen?"):
            return
        with ErrorGuard(self):
            self._service.delete_type(selected.id)
        self._reload()
