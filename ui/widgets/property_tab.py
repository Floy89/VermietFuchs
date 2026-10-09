"""Tab 2 - Immobilien: Häuser (links) und ihre Wohnungen (rechts) verwalten."""

from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QSplitter, QWidget

from app_context import AppContext
from models.apartment import Apartment
from models.property import Property
from ui.dialogs.property_dialog import ApartmentDialog, PropertyDialog
from ui.helpers import ErrorGuard, confirm, make_button
from ui.lookups import Lookups
from ui.widgets.common import BaseTab, Card, Column, ObjectTable, page_title
from utils.formatters import format_date, format_decimal


class PropertyTab(BaseTab):
    """Master-Detail-Ansicht: Auswahl einer Immobilie zeigt deren Wohnungen."""

    def __init__(self, context: AppContext, parent: QWidget | None = None) -> None:
        super().__init__(context, parent)
        self._lookups: Lookups | None = None
        self._apartment_counts: dict[int, int] = {}
        self.root.addWidget(page_title("Immobilien und Wohnungen"))

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setChildrenCollapsible(False)
        splitter.addWidget(self._build_property_card())
        splitter.addWidget(self._build_apartment_card())
        splitter.setStretchFactor(0, 5)
        splitter.setStretchFactor(1, 6)
        self.root.addWidget(splitter, 1)

    # ------------------------------------------------------------------ #
    # Aufbau
    # ------------------------------------------------------------------ #
    def _build_property_card(self) -> Card:
        """Linke Karte: Liste der Immobilien."""
        card = Card("Immobilien")
        self._add_property_button = make_button("Neu", "plus", "primary")
        self._edit_property_button = make_button("Bearbeiten", "edit")
        self._delete_property_button = make_button("Löschen", "trash", "danger")
        for button in (self._add_property_button, self._edit_property_button, self._delete_property_button):
            card.header.addWidget(button)

        self._property_table: ObjectTable[Property] = ObjectTable(
            [
                Column("Name", lambda p: p.name),
                Column("Adresse", lambda p: p.address),
                Column(
                    "Wohnungen",
                    lambda p: str(self._apartment_counts.get(p.id or -1, 0)),
                    lambda p: self._apartment_counts.get(p.id or -1, 0),
                    True,
                ),
            ],
            stretch_column=1,
        )
        card.body.addWidget(self._property_table)

        self._add_property_button.clicked.connect(self._add_property)
        self._edit_property_button.clicked.connect(self._edit_property)
        self._delete_property_button.clicked.connect(self._delete_property)
        self._property_table.item_activated.connect(lambda *_: self._edit_property())
        self._property_table.selection_changed.connect(self._on_property_selected)
        return card

    def _build_apartment_card(self) -> Card:
        """Rechte Karte: Wohnungen der gewählten Immobilie."""
        self._apartment_card = Card("Wohnungen")
        self._add_apartment_button = make_button("Neu", "plus", "primary")
        self._edit_apartment_button = make_button("Bearbeiten", "edit")
        self._delete_apartment_button = make_button("Löschen", "trash", "danger")
        for button in (self._add_apartment_button, self._edit_apartment_button, self._delete_apartment_button):
            self._apartment_card.header.addWidget(button)

        self._apartment_table: ObjectTable[Apartment] = ObjectTable(
            [
                Column("Nummer", lambda a: a.number),
                Column("Wohnfläche", lambda a: f"{format_decimal(a.area_sqm)} m²", lambda a: float(a.area_sqm), True),
                Column("Personen", lambda a: str(a.person_count), lambda a: a.person_count, True),
                Column(
                    "Aktueller Mieter",
                    lambda a: self._lookups.current_tenant_name(a.id) if self._lookups else "",
                ),
                Column("Mieter seit", self._tenant_since, lambda a: self._tenant_since(a, iso=True)),
            ],
            stretch_column=3,
        )
        self._apartment_card.body.addWidget(self._apartment_table)

        self._add_apartment_button.clicked.connect(self._add_apartment)
        self._edit_apartment_button.clicked.connect(self._edit_apartment)
        self._delete_apartment_button.clicked.connect(self._delete_apartment)
        self._apartment_table.item_activated.connect(lambda *_: self._edit_apartment())
        self._apartment_table.selection_changed.connect(self._update_buttons)
        return self._apartment_card

    def _tenant_since(self, apartment: Apartment, iso: bool = False) -> str:
        """Einzugsdatum des aktuellen Mieters ("–" bei Leerstand).

        Args:
            apartment: Die Wohnung.
            iso: ``True`` liefert ``JJJJ-MM-TT`` - damit sortiert die Spalte
                zeitlich richtig statt nach dem Tag.
        """
        tenant = self._lookups.current_tenant(apartment.id) if self._lookups else None
        if tenant is None:
            return "" if iso else "–"
        return tenant.start_date.isoformat() if iso else format_date(tenant.start_date)

    # ------------------------------------------------------------------ #
    # Daten laden
    # ------------------------------------------------------------------ #
    def refresh(self) -> None:
        """Lädt Immobilien und Wohnungen neu; die Auswahl bleibt erhalten."""
        selected = self._property_table.selected_item()
        selected_id = selected.id if selected else None
        with ErrorGuard(self):
            self._lookups = Lookups(self._context)
            self._apartment_counts = self._context.properties.apartment_counts()
            self._property_table.set_items(self._lookups.properties)
            self._reselect_property(selected_id)
        self._on_property_selected()

    def _reselect_property(self, property_id: int | None) -> None:
        """Markiert die zuvor gewählte Immobilie wieder (sonst die erste)."""
        if not self._property_table.select_where(lambda prop: prop.id == property_id):
            self._property_table.select_first()

    def _on_property_selected(self) -> None:
        """Zeigt die Wohnungen der markierten Immobilie an."""
        prop = self._property_table.selected_item()
        if prop is None or prop.id is None or self._lookups is None:
            self._apartment_card.set_title("Wohnungen")
            self._apartment_table.set_items([])
        else:
            self._apartment_card.set_title(f"Wohnungen – {prop.name}")
            self._apartment_table.set_items(
                [a for a in self._lookups.apartments if a.property_id == prop.id]
            )
        self._update_buttons()

    def _update_buttons(self) -> None:
        """Aktiviert Schaltflächen nur, wenn die passende Auswahl vorliegt."""
        has_property = self._property_table.selected_item() is not None
        has_apartment = self._apartment_table.selected_item() is not None
        self._edit_property_button.setEnabled(has_property)
        self._delete_property_button.setEnabled(has_property)
        self._add_apartment_button.setEnabled(has_property)
        self._edit_apartment_button.setEnabled(has_apartment)
        self._delete_apartment_button.setEnabled(has_apartment)

    # ------------------------------------------------------------------ #
    # Aktionen: Immobilien
    # ------------------------------------------------------------------ #
    def _add_property(self) -> None:
        if PropertyDialog(self._context.properties.save_property, parent=self).exec():
            self.refresh()

    def _edit_property(self) -> None:
        prop = self._property_table.selected_item()
        if prop and PropertyDialog(self._context.properties.save_property, prop, self).exec():
            self.refresh()

    def _delete_property(self) -> None:
        prop = self._property_table.selected_item()
        if prop is None or prop.id is None:
            return
        if not confirm(self, "Immobilie löschen", f"Immobilie '{prop.name}' wirklich löschen?"):
            return
        with ErrorGuard(self):
            self._context.properties.delete_property(prop.id)
        self.refresh()

    # ------------------------------------------------------------------ #
    # Aktionen: Wohnungen
    # ------------------------------------------------------------------ #
    def _add_apartment(self) -> None:
        prop = self._property_table.selected_item()
        if prop is None or prop.id is None:
            return
        if ApartmentDialog(self._context.apartments.save_apartment, prop.id, parent=self).exec():
            self.refresh()

    def _edit_apartment(self) -> None:
        apartment = self._apartment_table.selected_item()
        if apartment is None:
            return
        dialog = ApartmentDialog(
            self._context.apartments.save_apartment, apartment.property_id, apartment, self
        )
        if dialog.exec():
            self.refresh()

    def _delete_apartment(self) -> None:
        apartment = self._apartment_table.selected_item()
        if apartment is None or apartment.id is None:
            return
        if not confirm(self, "Wohnung löschen", f"Wohnung '{apartment.number}' wirklich löschen?"):
            return
        with ErrorGuard(self):
            self._context.apartments.delete_apartment(apartment.id)
        self.refresh()
