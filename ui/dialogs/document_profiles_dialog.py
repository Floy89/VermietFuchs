"""Dialoge für das, was die Anwendung beim Auslesen von Belegen je Absender gelernt hat."""

from __future__ import annotations

from dataclasses import replace
from decimal import Decimal

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QComboBox,
    QDialog,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QVBoxLayout,
    QWidget,
)

from models.cost_type import CostType
from models.document import FIELD_LABELS, LEARNABLE_FIELDS, DocumentProfile
from services.document_service import DocumentService
from ui.dialogs.base_dialog import FormDialog
from ui.helpers import ErrorGuard, confirm, fill_combo, make_button, select_data
from ui.widgets.common import Column, ObjectTable
from utils.formatters import format_date

#: Unter dieser Rolle steht am Listeneintrag der Name des Felds.
_FIELD_ROLE = Qt.ItemDataRole.UserRole

#: Zur Auswahl angebotene Steuersätze (``None`` = nichts vorgeben).
_VAT_CHOICES: tuple[tuple[str, Decimal | None], ...] = (
    ("– nicht vorgeben –", None),
    ("0 %", Decimal("0")),
    ("7 %", Decimal("7")),
    ("19 %", Decimal("19")),
)


def _issuer_source(profile: DocumentProfile) -> str:
    """Woran der Absender erkannt wird, lesbar."""
    kind, _, value = profile.issuer_key.partition(":")
    label = {"ust": "USt-IdNr.", "web": "Internetadresse", "name": "Firmenname"}.get(kind, "Merkmal")
    return f"{label} {value}"


class DocumentProfileEditDialog(FormDialog):
    """Die Vorgaben eines gelernten Absenders von Hand ändern."""

    def __init__(
        self,
        service: DocumentService,
        profile: DocumentProfile,
        cost_types: list[CostType],
        parent: QWidget | None = None,
    ) -> None:
        """
        Args:
            service: Service für das Auslesen von Belegen.
            profile: Das zu ändernde Profil.
            cost_types: Auswählbare Kostenarten.
        """
        super().__init__(f"Gelernter Absender – {profile.issuer_name or profile.issuer_key}", parent)
        self._service = service
        self._profile = profile

        self._name_edit = QLineEdit(profile.issuer_name)
        self._type_combo = QComboBox()
        self._type_combo.setMaxVisibleItems(18)
        fill_combo(self._type_combo, [("– nicht vorgeben –", None)] + [(t.name, t) for t in cost_types])
        if profile.cost_type_id is not None:
            index = next((i + 1 for i, t in enumerate(cost_types) if t.id == profile.cost_type_id), 0)
            self._type_combo.setCurrentIndex(index)
        self._key_combo = QComboBox()
        self._description_edit = QLineEdit(profile.description)
        self._unit_edit = QLineEdit(profile.consumption_unit)
        self._unit_edit.setPlaceholderText("z. B. m³ oder kWh")
        self._vat_combo = QComboBox()
        fill_combo(self._vat_combo, list(_VAT_CHOICES))
        if profile.vat_percent is not None:
            # Von Hand vergleichen: findData() erkennt gleiche Decimal-Werte nicht als gleich.
            index = next((i for i, (_, value) in enumerate(_VAT_CHOICES) if value == profile.vat_percent), -1)
            if index < 0:
                self._vat_combo.addItem(f"{profile.vat_percent} %", profile.vat_percent)
                index = self._vat_combo.count() - 1
            self._vat_combo.setCurrentIndex(index)
        hint = QLabel(
            "Diese Werte schlägt das Programm beim nächsten Beleg dieses Absenders vor. Speichern Sie "
            "eine Rechnung aus einem solchen Beleg, wird übernommen, was Sie dort gewählt haben."
        )
        hint.setObjectName("Muted")
        hint.setWordWrap(True)

        self.form.addRow("Absender", self._name_edit)
        self.form.addRow("Erkannt an", QLabel(_issuer_source(profile)))
        self.form.addRow("Kostenart", self._type_combo)
        self.form.addRow("Umlageschlüssel", self._key_combo)
        self.form.addRow("Beschreibung", self._description_edit)
        self.form.addRow("Einheit des Verbrauchs", self._unit_edit)
        self.form.addRow("Umsatzsteuer der Splits", self._vat_combo)
        self.form.addRow(hint)

        self._type_combo.currentIndexChanged.connect(lambda *_: self._update_keys())
        self._update_keys()
        if profile.allocation_key is not None:
            select_data(self._key_combo, profile.allocation_key)

    def _update_keys(self) -> None:
        """Bietet nur die für die gewählte Kostenart zulässigen Schlüssel an."""
        current = self._key_combo.currentData()
        cost_type = self._type_combo.currentData()
        keys = cost_type.allowed_keys() if isinstance(cost_type, CostType) else ()
        fill_combo(self._key_combo, [("– nicht vorgeben –", None)] + [(key.label, key) for key in keys])
        if current is not None and current in keys:
            select_data(self._key_combo, current)

    def _apply(self) -> None:
        cost_type = self._type_combo.currentData()
        self._service.save_profile(
            replace(
                self._profile,
                issuer_name=self._name_edit.text().strip(),
                cost_type_id=cost_type.id if isinstance(cost_type, CostType) else None,
                allocation_key=self._key_combo.currentData(),
                description=self._description_edit.text().strip(),
                consumption_unit=self._unit_edit.text().strip(),
                vat_percent=self._vat_combo.currentData(),
            )
        )


class DocumentProfilesDialog(QDialog):
    """Liste der gelernten Absender: ansehen, ändern, einzelne Fundstellen oder ganze Absender vergessen."""

    def __init__(self, service: DocumentService, cost_types: list[CostType], parent: QWidget | None = None) -> None:
        """
        Args:
            service: Service für das Auslesen von Belegen.
            cost_types: Alle Kostenarten (für die Anzeige und das Ändern).
        """
        super().__init__(parent)
        self._service = service
        self._cost_types = cost_types
        self._type_names = {t.id: t.name for t in cost_types}
        self.setWindowTitle("Gelernte Absender")
        self.resize(860, 560)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 16)
        layout.setSpacing(10)

        intro = QLabel(
            "Beim Speichern einer Rechnung, die mit „Beleg auslesen …“ gefüllt wurde, merkt sich das "
            "Programm je Absender, was Sie gewählt und korrigiert haben. Führt etwas in die Irre, "
            "können Sie es hier ändern oder vergessen - es gilt dann wieder die allgemeine Erkennung."
        )
        intro.setObjectName("Muted")
        intro.setWordWrap(True)
        layout.addWidget(intro)

        self._table: ObjectTable[DocumentProfile] = ObjectTable(
            [
                Column("Absender", lambda p: p.issuer_name or p.issuer_key),
                Column("Kostenart", lambda p: self._type_names.get(p.cost_type_id, "") if p.cost_type_id else ""),
                Column("Schlüssel", lambda p: p.allocation_key.label if p.allocation_key else ""),
                Column("Beschreibung", lambda p: p.description),
                Column("Belege", lambda p: str(p.times_used), lambda p: p.times_used, True),
                Column("Zuletzt", lambda p: format_date(p.updated_at.date()), lambda p: p.updated_at.isoformat()),
            ],
            stretch_column=3,
        )
        layout.addWidget(self._table, 1)

        details = QGroupBox("Gelernte Fundstellen im Beleg")
        details_layout = QVBoxLayout(details)
        self._source_label = QLabel()
        self._source_label.setObjectName("Muted")
        self._anchor_list = QListWidget()
        self._anchor_list.setFixedHeight(190)
        self._forget_anchor_button = make_button("Fundstelle vergessen", "trash")
        anchor_buttons = QHBoxLayout()
        anchor_buttons.addStretch(1)
        anchor_buttons.addWidget(self._forget_anchor_button)
        details_layout.addWidget(self._source_label)
        details_layout.addWidget(self._anchor_list)
        details_layout.addLayout(anchor_buttons)
        layout.addWidget(details)

        self._edit_button = make_button("Bearbeiten", "edit")
        self._forget_button = make_button("Absender vergessen", "trash", "danger")
        self._forget_all_button = make_button("Alles vergessen", "trash")
        close_button = make_button("Schließen")
        buttons = QHBoxLayout()
        for button in (self._edit_button, self._forget_button, self._forget_all_button):
            buttons.addWidget(button)
        buttons.addStretch(1)
        buttons.addWidget(close_button)
        layout.addLayout(buttons)

        self._empty_label = QLabel(
            "Noch nichts gelernt. Lesen Sie im Rechnungsdialog einen Beleg mit „Beleg auslesen …“ aus "
            "und speichern Sie die Rechnung."
        )
        self._empty_label.setObjectName("Muted")
        self._empty_label.setWordWrap(True)
        layout.insertWidget(2, self._empty_label)

        self._edit_button.clicked.connect(self._edit)
        self._forget_button.clicked.connect(self._forget)
        self._forget_all_button.clicked.connect(self._forget_all)
        self._forget_anchor_button.clicked.connect(self._forget_anchor)
        close_button.clicked.connect(self.accept)
        self._table.selection_changed.connect(self._show_details)
        self._table.item_activated.connect(lambda *_: self._edit())
        self._anchor_list.currentRowChanged.connect(lambda *_: self._update_buttons())
        self._reload()

    # ------------------------------------------------------------------ #
    def _reload(self, select_id: int | None = None) -> None:
        profiles: list[DocumentProfile] = []
        with ErrorGuard(self):
            profiles = self._service.profiles()
        self._table.set_items(profiles)
        self._empty_label.setVisible(not profiles)
        self._table.setVisible(bool(profiles))
        for row, profile in enumerate(profiles):
            if profile.id == select_id or (select_id is None and row == 0):
                self._table.selectRow(row)
                break
        self._show_details()

    def _show_details(self) -> None:
        """Zeigt Erkennungsmerkmal und Fundstellen des gewählten Absenders."""
        self._anchor_list.clear()
        profile = self._table.selected_item()
        if profile is None:
            self._source_label.setText("Kein Absender ausgewählt.")
        else:
            facts = [f"Erkannt an: {_issuer_source(profile)}"]
            if profile.consumption_unit:
                facts.append(f"Einheit: {profile.consumption_unit}")
            if profile.vat_percent is not None:
                facts.append(f"Umsatzsteuer: {profile.vat_percent} %")
            self._source_label.setText(" · ".join(facts))
            for field_name in LEARNABLE_FIELDS:
                anchor = profile.anchors.get(field_name)
                if anchor is None:
                    continue
                kind = "number" if field_name in ("amount", "consumption") else "date"
                item = QListWidgetItem(f"{FIELD_LABELS[field_name]}: {anchor.describe(kind)}")
                item.setData(_FIELD_ROLE, field_name)
                self._anchor_list.addItem(item)
            if not self._anchor_list.count():
                self._anchor_list.addItem("Keine Fundstellen - es gilt die allgemeine Erkennung.")
        self._update_buttons()

    def _update_buttons(self) -> None:
        has_profile = self._table.selected_item() is not None
        self._edit_button.setEnabled(has_profile)
        self._forget_button.setEnabled(has_profile)
        self._forget_all_button.setEnabled(self._table.model().rowCount() > 0)
        item = self._anchor_list.currentItem()
        self._forget_anchor_button.setEnabled(has_profile and item is not None and item.data(_FIELD_ROLE) is not None)

    def _edit(self) -> None:
        profile = self._table.selected_item()
        if profile is not None and DocumentProfileEditDialog(self._service, profile, self._cost_types, self).exec():
            self._reload(profile.id)

    def _forget_anchor(self) -> None:
        profile = self._table.selected_item()
        item = self._anchor_list.currentItem()
        if profile is None or profile.id is None or item is None or item.data(_FIELD_ROLE) is None:
            return
        with ErrorGuard(self):
            self._service.forget_anchor(profile.id, item.data(_FIELD_ROLE))
        self._reload(profile.id)

    def _forget(self) -> None:
        profile = self._table.selected_item()
        if profile is None or profile.id is None:
            return
        text = (
            f"Alles Gelernte zu „{profile.issuer_name or profile.issuer_key}“ vergessen?\n"
            "Ihre Rechnungen und Belege bleiben unverändert."
        )
        if not confirm(self, "Absender vergessen", text, "Vergessen"):
            return
        with ErrorGuard(self):
            self._service.forget(profile.id)
        self._reload()

    def _forget_all(self) -> None:
        text = "Alles Gelernte zu allen Absendern vergessen?\nIhre Rechnungen und Belege bleiben unverändert."
        if not confirm(self, "Alles vergessen", text, "Alles vergessen"):
            return
        with ErrorGuard(self):
            self._service.forget_all()
        self._reload()
