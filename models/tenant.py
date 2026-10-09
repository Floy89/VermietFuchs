"""Entity: Mieter mit seinem Mietvertrag."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from models.enums import AllocationKey
from models.exceptions import ValidationError
from models.value_objects import DateRange, Money
from utils.validators import optional_text, require_non_negative, require_text


@dataclass
class TenantKeyOverride:
    """Abweichender Umlageschlüssel eines Mieters für eine Kostenart.

    Standard ist immer der Schlüssel der Rechnung. Ein Eintrag hier bedeutet:
    Im Mietvertrag dieses Mieters ist für diese Kostenart etwas anderes
    vereinbart, und sein Anteil wird danach berechnet.
    """

    tenant_id: int
    cost_type_id: int
    allocation_key: AllocationKey
    id: int | None = None


@dataclass
class Tenant:
    """Ein Mieter einer Wohnung für die Dauer seines Vertrags.

    Zieht ein Mieter um oder aus, bekommt der Nachmieter einen eigenen
    Datensatz. So lässt sich bei einem Mieterwechsel mitten im Jahr jede
    Kostenposition tagesgenau aufteilen.

    Die vereinbarten Nebenkosten-Vorauszahlungen stehen nicht hier, sondern
    als Historie in :class:`~models.prepayment_rate.PrepaymentRate`.
    """

    apartment_id: int
    name: str
    start_date: date
    end_date: date | None = None
    contact: str = ""
    rent_amount: Money = Money.zero()
    id: int | None = None

    def validate(self) -> None:
        """Prüft die fachlichen Regeln; wirft ``ValidationError`` bei Verstößen."""
        self.name = require_text(self.name, "Name", max_length=120)
        self.contact = optional_text(self.contact, "Kontakt", max_length=300)
        if self.end_date is not None and self.end_date < self.start_date:
            raise ValidationError("Das Vertragsende darf nicht vor dem Vertragsbeginn liegen.")
        require_non_negative(self.rent_amount.cents, "Mietzins")

    @property
    def contract_period(self) -> DateRange:
        """Vertragslaufzeit; unbefristete Verträge enden rechnerisch am ``date.max``."""
        return DateRange(self.start_date, self.end_date or date.max)

    def occupancy(self, period: DateRange) -> DateRange | None:
        """Teil von ``period``, in dem dieser Mieter die Wohnung bewohnt hat."""
        return self.contract_period.intersection(period)

    def occupied_days(self, period: DateRange) -> int:
        """Anzahl der Tage von ``period``, an denen der Vertrag bestand."""
        occupancy = self.occupancy(period)
        return occupancy.days if occupancy else 0

    def is_active(self, on: date) -> bool:
        """``True``, wenn der Vertrag am Stichtag ``on`` besteht."""
        return self.contract_period.contains(on)
