"""Entity: Mieter mit seinem Mietvertrag."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from models.deposit import DepositStatement, InterestRate, deposit_statement, validate_rates
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

    Attributes:
        deposit_amount: Eingezahlte Mietkaution (0 = keine erfasst).
        deposit_paid_on: Tag der Einzahlung; ``None`` = Vertragsbeginn.
        deposit_rates: Zinssätze des Kautionskontos mit "gültig ab".
    """

    apartment_id: int
    name: str
    start_date: date
    end_date: date | None = None
    contact: str = ""
    rent_amount: Money = Money.zero()
    id: int | None = None
    deposit_amount: Money = Money.zero()
    deposit_paid_on: date | None = None
    deposit_rates: list[InterestRate] = field(default_factory=list)

    def validate(self) -> None:
        """Prüft die fachlichen Regeln; wirft ``ValidationError`` bei Verstößen."""
        self.name = require_text(self.name, "Name", max_length=120)
        self.contact = optional_text(self.contact, "Kontakt", max_length=300)
        if self.end_date is not None and self.end_date < self.start_date:
            raise ValidationError("Das Vertragsende darf nicht vor dem Vertragsbeginn liegen.")
        require_non_negative(self.rent_amount.cents, "Mietzins")
        require_non_negative(self.deposit_amount.cents, "Kaution")
        validate_rates(self.deposit_rates)
        self.deposit_rates = sorted(self.deposit_rates, key=lambda rate: rate.valid_from)
        if self.end_date is not None and self.deposit_paid_on is not None and self.deposit_paid_on > self.end_date:
            raise ValidationError("Die Kaution kann nicht nach dem Vertragsende eingezahlt worden sein.")

    @property
    def has_deposit(self) -> bool:
        """``True``, wenn eine Kaution erfasst ist."""
        return not self.deposit_amount.is_zero

    @property
    def deposit_start(self) -> date:
        """Tag der Einzahlung der Kaution (ohne Angabe: Vertragsbeginn)."""
        return self.deposit_paid_on or self.start_date

    def deposit_statement(self, until: date | None = None) -> DepositStatement:
        """Kaution samt Zinsen bis zum Stichtag - Standard: Tag nach Vertragsende bzw. heute.

        Endet der Vertrag am 31.03., wird die Kaution zum 01.04. abgerechnet:
        Der 31.03. ist noch ein Zinstag.
        """
        if until is None:
            until = date.fromordinal(self.end_date.toordinal() + 1) if self.end_date else date.today()
        return deposit_statement(self.deposit_amount, self.deposit_start, self.deposit_rates, until)

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
