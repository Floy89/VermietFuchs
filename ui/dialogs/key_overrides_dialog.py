"""Dialoge für abweichende Umlageschlüssel eines Mieters."""

from __future__ import annotations

from PyQt6.QtWidgets import QComboBox, QDialog, QHBoxLayout, QLabel, QVBoxLayout, QWidget

from models.cost_type import CostType
from models.exceptions import ValidationError
from models.tenant import Tenant, TenantKeyOverride
from services.key_override_service import KeyOverrideService
from ui.dialogs.base_dialog import FormDialog
from ui.helpers import ErrorGuard, confirm, fill_combo, make_button, select_data
from ui.widgets.common import Column, ObjectTable


class KeyOverrideEditDialog(FormDialog):
    """Für eine Kostenart einen abweichenden Schlüssel wählen."""

    def __init__(
        self,
        service: KeyOverrideService,
        tenant: Tenant,
        cost_types: list[CostType],
        override: TenantKeyOverride | None = None,
        parent: QWidget | None = None,
    ) -> None:
        """
        Args:
            service: Service der abweichenden Schlüssel.
            tenant: Der Mieter.
            cost_types: Kostenarten, für die eine Abweichung möglich ist.
            override: Zu ändernde Abweichung; ``None`` legt eine neue an.
        """
        super().__init__("Abweichender Umlageschlüssel", parent)
        self._service = service
        self._tenant = tenant

        self._type_combo = QComboBox()
        self._type_combo.setMaxVisibleItems(18)
        fill_combo(self._type_combo, [(cost_type.name, cost_type) for cost_type in cost_types])
        self._key_combo = QComboBox()
        hint = QLabel(
            "Gilt nur für diesen Mieter und diese Kostenart. Alle anderen Mieter werden weiter nach "
            "dem Schlüssel abgerechnet, der an der Rechnung eingestellt ist."
        )
        hint.setObjectName("Muted")
        hint.setWordWrap(True)

        self.form.addRow("Kostenart", self._type_combo)
        self.form.addRow("Schlüssel laut Mietvertrag", self._key_combo)
        self.form.addRow(hint)

        self._type_combo.currentIndexChanged.connect(lambda *_: self._update_keys())
        if override:
            index = next((i for i, t in enumerate(cost_types) if t.id == override.cost_type_id), 0)
            self._type_combo.setCurrentIndex(index)
            self._type_combo.setEnabled(False)  # Die Kostenart einer Abweichung steht fest.
        self._update_keys()
        if override:
            select_data(self._key_combo, override.allocation_key)

    def _update_keys(self) -> None:
        """Bietet nur die für die gewählte Kostenart möglichen Schlüssel an."""
        cost_type = self._type_combo.currentData()
        if isinstance(cost_type, CostType):
            fill_combo(self._key_combo, [(key.label, key) for key in cost_type.override_keys()])

    def _apply(self) -> None:
        cost_type = self._type_combo.currentData()
        if not isinstance(cost_type, CostType) or cost_type.id is None or self._tenant.id is None:
            raise ValidationError("Bitte eine Kostenart wählen.")
        self._service.set_override(self._tenant.id, cost_type.id, self._key_combo.currentData())


class KeyOverridesDialog(QDialog):
    """Liste der abweichenden Umlageschlüssel eines Mieters."""

    def __init__(self, service: KeyOverrideService, tenant: Tenant, parent: QWidget | None = None) -> None:
        """
        Args:
            service: Service der abweichenden Schlüssel.
            tenant: Der Mieter, dessen Abweichungen angezeigt werden.
        """
        super().__init__(parent)
        self._service = service
        self._tenant = tenant
        self._types: list[CostType] = []
        self.setWindowTitle(f"Umlageschlüssel – {tenant.name}")
        self.resize(660, 420)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 16)
        layout.setSpacing(10)

        intro = QLabel(
            "Standard ist immer der Schlüssel, der an der jeweiligen Rechnung eingestellt ist. Hier "
            "tragen Sie nur ein, was im Mietvertrag dieses Mieters abweichend vereinbart ist.\n"
            "Hinweis: Mit abweichenden Schlüsseln ergänzen sich die Anteile der Mieter nicht mehr "
            "automatisch zu 100 %. Der Tab 'Abrechnung' zeigt je Rechnung, wie viel umgelegt wird, und "
            "warnt, wenn es mehr ist als der Rechnungsbetrag."
        )
        intro.setObjectName("Muted")
        intro.setWordWrap(True)
        layout.addWidget(intro)

        self._table: ObjectTable[TenantKeyOverride] = ObjectTable(
            [
                Column("Kostenart", self._type_name),
                Column("Schlüssel laut Mietvertrag", lambda o: o.allocation_key.label),
            ],
            stretch_column=0,
        )
        layout.addWidget(self._table, 1)

        self._add_button = make_button("Abweichung hinzufügen", "plus", "primary")
        self._edit_button = make_button("Bearbeiten", "edit")
        self._delete_button = make_button("Entfernen", "trash", "danger")
        close_button = make_button("Schließen")
        buttons = QHBoxLayout()
        for button in (self._add_button, self._edit_button, self._delete_button):
            buttons.addWidget(button)
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

    def _type_name(self, override: TenantKeyOverride) -> str:
        """Name der Kostenart einer Abweichung."""
        return next((t.name for t in self._types if t.id == override.cost_type_id), "")

    def _reload(self) -> None:
        assert self._tenant.id is not None
        with ErrorGuard(self):
            self._types = self._service.overridable_types()
            self._table.set_items(self._service.list_for_tenant(self._tenant.id))
        self._update_buttons()

    def _update_buttons(self) -> None:
        has_selection = self._table.selected_item() is not None
        self._edit_button.setEnabled(has_selection)
        self._delete_button.setEnabled(has_selection)

    def _add(self) -> None:
        if KeyOverrideEditDialog(self._service, self._tenant, self._types, parent=self).exec():
            self._reload()

    def _edit(self) -> None:
        override = self._table.selected_item()
        if override and KeyOverrideEditDialog(self._service, self._tenant, self._types, override, self).exec():
            self._reload()

    def _delete(self) -> None:
        override = self._table.selected_item()
        if override is None or override.id is None:
            return
        if not confirm(
            self,
            "Abweichung entfernen",
            f"Abweichung für '{self._type_name(override)}' entfernen?\nEs gilt dann wieder der Standard der Rechnung.",
            confirm_label="Entfernen",
        ):
            return
        with ErrorGuard(self):
            self._service.remove_override(override.id)
        self._reload()
