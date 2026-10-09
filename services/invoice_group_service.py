"""Rechnungsmanagement: Rechnungen nach Kalenderjahren aufteilen und je Kostenart zusammenfassen."""

from __future__ import annotations

import logging
from collections import defaultdict

from models.cost import Cost
from models.invoice_group import InvoiceGroup, InvoiceGroupSetting, InvoicePart
from models.value_objects import DateRange
from repositories.interfaces import CostRepository, InvoiceGroupSettingRepository
from utils.validators import optional_text

logger = logging.getLogger(__name__)


class InvoiceGroupService:
    """Bildet aus den Originalrechnungen die "aggregierten Rechnungen".

    Eine aggregierte Rechnung entsteht automatisch für jede Kombination aus
    Kostenart und Kalenderjahr, die von mindestens einer Rechnung berührt
    wird. Der Benutzer kann sie umbenennen und von der Abrechnung ausschließen.
    """

    def __init__(self, costs: CostRepository, settings: InvoiceGroupSettingRepository) -> None:
        self._costs = costs
        self._settings = settings

    def groups(
        self,
        property_id: int,
        year: int | None = None,
        period: DateRange | None = None,
    ) -> list[InvoiceGroup]:
        """Aggregierte Rechnungen einer Immobilie.

        Jede Rechnung wird taggenau auf die Kalenderjahre aufgeteilt, die ihr
        Zeitraum berührt; Betrag und Verbrauch werden dabei im selben
        Verhältnis geteilt. Einzeln ausgeschlossene Rechnungen bleiben außen vor.

        Args:
            property_id: Die Immobilie.
            year: Nur dieses Kalenderjahr; ``None`` liefert alle Jahre.
            period: Stattdessen ein frei gewählter Zeitraum (nicht-kalendarische
                Abrechnung). Dann entsteht je Kostenart genau eine Gruppe mit
                den Rechnungsanteilen in diesem Zeitraum. Für Ausschluss und
                Namen zählt - wie in der Abrechnung - das Jahr des Beginns.

        Returns:
            Gruppen, neueste Jahre zuerst, innerhalb eines Jahres in Katalog-Reihenfolge.
        """
        settings = {(s.cost_type_id, s.year): s for s in self._settings.list_by_property(property_id)}
        parts_by_group: dict[tuple[int, int], list[InvoicePart]] = defaultdict(list)
        type_of: dict[int, Cost] = {}

        for invoice in self._costs.list_by_property(property_id):
            if invoice.excluded or invoice.cost_type.id is None:
                continue
            if period is not None:
                buckets = [(period.start.year, period)]
            else:
                buckets = [
                    (invoice_year, DateRange.for_year(invoice_year))
                    for invoice_year in range(invoice.period.start.year, invoice.period.end.year + 1)
                    if year is None or invoice_year == year
                ]
            for invoice_year, bucket in buckets:
                part = invoice.prorate(bucket)
                if part is not None:
                    parts_by_group[(invoice.cost_type.id, invoice_year)].append(InvoicePart(invoice, part))
                    type_of[invoice.cost_type.id] = invoice

        groups: list[InvoiceGroup] = []
        for (cost_type_id, group_year), parts in parts_by_group.items():
            setting = settings.get((cost_type_id, group_year))
            groups.append(
                InvoiceGroup(
                    property_id=property_id,
                    cost_type=type_of[cost_type_id].cost_type,
                    year=group_year,
                    custom_name=setting.name if setting else "",
                    excluded=setting.excluded if setting else False,
                    parts=sorted(parts, key=lambda p: p.part.period.start),
                    # Nur merken, wenn der Zeitraum kein volles Kalenderjahr ist.
                    period=period if period is not None and period != DateRange.for_year(group_year) else None,
                )
            )
        return sorted(groups, key=lambda g: (-g.year, g.cost_type.sort_order, g.cost_type.name.casefold()))

    def excluded_cost_type_ids(self, property_id: int, year: int) -> set[int]:
        """IDs der Kostenarten, deren aggregierte Rechnung im Jahr ausgeschlossen ist."""
        return {
            setting.cost_type_id
            for setting in self._settings.list_by_property(property_id)
            if setting.excluded and setting.year == year
        }

    def set_excluded(self, group: InvoiceGroup, excluded: bool) -> None:
        """Schließt eine aggregierte Rechnung von der Abrechnung aus bzw. nimmt sie wieder auf."""
        self._save(group, excluded=excluded)
        logger.info("Aggregierte Rechnung '%s': ausgeschlossen = %s", group.name, excluded)

    def rename(self, group: InvoiceGroup, name: str) -> None:
        """Gibt einer aggregierten Rechnung einen eigenen Namen (leer = Standardname)."""
        self._save(group, name=optional_text(name, "Name", max_length=80))

    def _save(self, group: InvoiceGroup, excluded: bool | None = None, name: str | None = None) -> None:
        """Legt die Einstellung an oder aktualisiert sie."""
        assert group.cost_type.id is not None  # Gruppen entstehen nur aus gespeicherten Rechnungen
        existing = next(
            (
                s
                for s in self._settings.list_by_property(group.property_id)
                if s.cost_type_id == group.cost_type.id and s.year == group.year
            ),
            None,
        )
        setting = existing or InvoiceGroupSetting(group.property_id, group.cost_type.id, group.year)
        if excluded is not None:
            setting.excluded = excluded
        if name is not None:
            setting.name = name
        if setting.id is None:
            self._settings.add(setting)
        else:
            self._settings.update(setting)
