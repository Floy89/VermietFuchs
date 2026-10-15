"""Entity: Rechnung (Kostenposition) einer Immobilie samt Umlage-Regeln und Splits."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Final, Sequence

from models.cost_type import CostType
from models.enums import AllocationKey
from models.exceptions import ValidationError
from models.tariff import SplitCalculation
from models.value_objects import DateRange, Money
from utils.validators import optional_text, require_in_range, require_non_negative, require_positive

#: Heizkostenverordnung § 7: Mindestens 50 % und höchstens 70 % der
#: Heizkosten sind nach dem erfassten Verbrauch zu verteilen.
MIN_CONSUMPTION_SHARE: Final[int] = 50
MAX_CONSUMPTION_SHARE: Final[int] = 70
DEFAULT_CONSUMPTION_SHARE: Final[int] = 70

#: Schlüssel, nach denen die Differenz zwischen Gesamtverbrauch laut Rechnung
#: und der Summe der Wohnungszähler verteilt werden kann. ``CONSUMPTION``
#: bedeutet: anteilig wie der gemessene Verbrauch.
DIFFERENCE_KEYS: Final[tuple[AllocationKey, ...]] = (
    AllocationKey.CONSUMPTION,
    AllocationKey.UNITS,
    AllocationKey.AREA,
    AllocationKey.PERSONS,
)

#: Erlaubte Abweichung zwischen Gesamtverbrauch und Summe der Split-Verbräuche
#: (gleicht Rundungen in der dritten Nachkommastelle aus).
_CONSUMPTION_TOLERANCE: Final[Decimal] = Decimal("0.001")
_THOUSANDTH: Final[Decimal] = Decimal("0.001")


@dataclass(frozen=True)
class InvoiceSplit:
    """Ein Teilabschnitt einer Rechnung, z. B. zwischen zwei Ableseterminen.

    Versorger weisen auf einer Rechnung oft mehrere Abschnitte mit eigenem
    Verbrauch und eigenen Kosten aus (Preisänderung, Zwischenablesung). Sind
    solche Splits hinterlegt, weiß die Anwendung genau, welcher Teil der
    Rechnung in welches Jahr fällt - statt die Summe gleichmäßig über den
    gesamten Rechnungszeitraum zu verteilen.

    Attributes:
        period: Zeitraum des Abschnitts (innerhalb des Rechnungszeitraums).
        amount: Kosten des Abschnitts.
        consumption: Verbrauch des Abschnitts in der Einheit der Rechnung;
            ``None``, wenn die Rechnung keinen Verbrauch ausweist.
        calculation: Optional die Angaben, aus denen Verbrauch und Betrag
            berechnet wurden (Gasvolumen, Preise). Für die Abrechnung zählen
            allein ``amount`` und ``consumption``; die Rechengrundlagen werden
            nur aufbewahrt, damit sie beim nächsten Bearbeiten wieder dastehen.
        amount_corrected: ``True``, wenn der Benutzer den aus Preisen berechneten
            Betrag von Hand überschrieben hat. Der Split-Dialog rechnet ihn dann
            nicht mehr neu, bis die Berechnung ausdrücklich wiederhergestellt wird.
        is_difference: ``True`` für einen Differenz-Eintrag: ein (meist kleiner)
            Restbetrag, der nicht zu einem Abschnitt gehört - z. B. Rundungs-
            differenzen des Versorgers. Er gilt für den ganzen Rechnungszeitraum,
            hat keinen Verbrauch, darf negativ sein und wird bei der
            Jahreszuordnung linear (nach Tagen) auf die übrigen Splits verteilt.
        co2_cost / co2_emission: CO2-Kosten brutto und CO2-Ausstoß (kg) des
            Abschnitts, wenn die Rechnung sie je Zeitraum ausweist - die
            Kosten je Jahr weichen ab, weil der CO2-Preis zum Jahreswechsel
            steigt. Ohne Angabe wird die CO2-Summe der Rechnung nach Verbrauch
            aufgeteilt.
    """

    period: DateRange
    amount: Money
    consumption: Decimal | None = None
    calculation: SplitCalculation | None = None
    amount_corrected: bool = False
    is_difference: bool = False
    co2_cost: Money | None = None
    co2_emission: Decimal | None = None


def validate_splits(
    splits: Sequence[InvoiceSplit],
    invoice_period: DateRange,
    invoice_amount: Money,
    invoice_consumption: Decimal | None,
) -> None:
    """Prüft, ob die Splits zur Rechnung passen.

    Regeln:

    * Jeder Split liegt im Rechnungszeitraum; Splits überschneiden sich nicht.
    * Die Summe der Split-Beträge ist **exakt** der Rechnungsbetrag.
    * Weist die Rechnung einen Verbrauch aus, braucht jeder Split einen
      Verbrauch, und deren Summe ist der Gesamtverbrauch. Ohne
      Gesamtverbrauch darf auch kein Split einen Verbrauch haben.

    Eine leere Liste ist immer gültig (Rechnung ohne Splits).

    Raises:
        ValidationError: Mit der konkreten Abweichung, damit der Benutzer
            sie gezielt korrigieren kann.
    """
    if not splits:
        return

    differences = [split for split in splits if split.is_difference]
    if len(differences) > 1:
        raise ValidationError("Es darf nur einen Differenz-Eintrag geben.")
    for difference in differences:
        if difference.period != invoice_period:
            raise ValidationError("Der Differenz-Eintrag muss den ganzen Rechnungszeitraum umfassen.")
        if difference.consumption is not None:
            raise ValidationError("Der Differenz-Eintrag hat keinen Verbrauch.")

    ordered = sorted((split for split in splits if not split.is_difference), key=lambda split: split.period.start)
    for index, split in enumerate(ordered):
        label = f"Split {index + 1} ({_short(split.period)})"
        if not invoice_period.encloses(split.period):
            raise ValidationError(f"{label} liegt nicht vollständig im Rechnungszeitraum.")
        if split.amount.is_negative:
            raise ValidationError(f"{label}: Der Betrag darf nicht negativ sein.")
        if split.consumption is not None and split.consumption < 0:
            raise ValidationError(f"{label}: Der Verbrauch darf nicht negativ sein.")
        if index > 0 and ordered[index - 1].period.overlaps(split.period):
            raise ValidationError(f"{label} überschneidet sich mit dem vorherigen Split.")

    amount_sum = sum((split.amount for split in splits), Money.zero())
    if amount_sum != invoice_amount:
        difference = invoice_amount - amount_sum
        raise ValidationError(
            f"Die Summe der Split-Beträge ({_euro(amount_sum)}) entspricht nicht dem Gesamtbetrag "
            f"({_euro(invoice_amount)}). Differenz: {_euro(difference)}."
        )

    with_consumption = [split for split in ordered if split.consumption is not None]
    if invoice_consumption is None:
        if with_consumption:
            raise ValidationError(
                "Die Splits enthalten Verbräuche, die Rechnung aber keinen Gesamtverbrauch. "
                "Bitte den Gesamtverbrauch eintragen."
            )
        return
    if len(with_consumption) != len(ordered):
        raise ValidationError(
            "Die Rechnung hat einen Gesamtverbrauch - bitte bei jedem Split den Verbrauch eintragen."
        )
    consumption_sum = sum((split.consumption or Decimal(0) for split in ordered), Decimal(0))
    if abs(consumption_sum - invoice_consumption) > _CONSUMPTION_TOLERANCE:
        raise ValidationError(
            f"Die Summe der Split-Verbräuche ({_number(consumption_sum)}) entspricht nicht dem "
            f"Gesamtverbrauch ({_number(invoice_consumption)}). "
            f"Differenz: {_number(invoice_consumption - consumption_sum)}."
        )


def uncovered_periods(splits: Sequence[InvoiceSplit], invoice_period: DateRange) -> list[DateRange]:
    """Zeiträume der Rechnung, die von keinem Split abgedeckt sind.

    Solche Lücken sind erlaubt, aber fast immer ein Tippfehler in einem
    Datum: Die Tage in der Lücke bekommen keine Kosten zugerechnet, obwohl
    die Summenprüfung bestanden ist. Deshalb weist die Oberfläche darauf hin.

    Returns:
        Die Lücken in zeitlicher Reihenfolge; leer, wenn es keine Splits gibt
        (dann wird linear verteilt) oder die Splits lückenlos anschließen.
    """
    splits = [split for split in splits if not split.is_difference]
    if not splits:
        return []
    one_day = timedelta(days=1)
    gaps: list[DateRange] = []
    expected_start = invoice_period.start
    for split in sorted(splits, key=lambda s: s.period.start):
        if split.period.start > expected_start:
            gaps.append(DateRange(expected_start, split.period.start - one_day))
        expected_start = max(expected_start, split.period.end + one_day)
    if expected_start <= invoice_period.end:
        gaps.append(DateRange(expected_start, invoice_period.end))
    return gaps


@dataclass
class Cost:
    """Eine Rechnung (z. B. die Jahresrechnung des Wasserversorgers).

    Attributes:
        property_id: Immobilie, für die die Kosten angefallen sind.
        cost_type: Kostenart.
        amount: Gesamtbetrag der Rechnung.
        period: Zeitraum, für den die Kosten angefallen sind
            (Rechnungszeitraum). Er darf vom Abrechnungszeitraum abweichen.
        allocation_key: Umlageschlüssel.
        consumption_share: Verbrauchsanteil in Prozent; nur beim Schlüssel
            ``CONSUMPTION_AREA`` von Bedeutung (Rest wird nach Fläche verteilt).
        description: Freitext, z. B. "Stadtwerke, Rechnung 4711".
        direct_shares: Nur beim Schlüssel ``DIRECT``: fertig ermittelter
            Betrag je Mieter-ID, z. B. aus der Abrechnung eines Messdienstes.
        invoice_date: Rechnungsdatum laut Beleg (optional).
        consumption: Gesamtverbrauch laut Rechnung (z. B. kWh Gas, m³ Wasser).
            Dient der Meldung an den Messdienst - und beim Schlüssel
            ``CONSUMPTION`` als Gesamtmenge, auf die sich die Kosten beziehen.
        consumption_unit: Einheit des Verbrauchs, z. B. "kWh" oder "m³".
        bundle_id: Sammelrechnung, zu der diese Position gehört (``None`` =
            einzeln erfasste Rechnung).
        difference_key: Nur beim Schlüssel ``CONSUMPTION`` mit angegebenem
            Gesamtverbrauch: Wonach die Differenz zwischen Gesamtverbrauch und
            Summe der Wohnungszähler verteilt wird (siehe :data:`DIFFERENCE_KEYS`).
        excluded: ``True`` = Rechnung wird weder aggregiert noch abgerechnet.
        co2_cost / co2_emission / co2_factor: CO2-Kosten brutto, CO2-Ausstoß in
            kg und Emissionsfaktor in kg/kWh laut Rechnung (Brennstoffe, nach
            dem CO2-Kostenaufteilungsgesetz). Für die Meldung an den
            Messdienst; zeitliche Ausschnitte bekommen ihren Anteil nach
            Verbrauch (ohne Verbrauch nach Betrag).
        splits: Optionale Teilabschnitte (siehe :class:`InvoiceSplit`). Sind
            welche vorhanden, bestimmen sie allein, wie viel der Rechnung in
            welchen Zeitraum fällt.
        full_amount / full_period: Nur bei einem zeitlichen Ausschnitt (siehe
            :meth:`pieces` und :meth:`prorate`): Betrag und Zeitraum der
            Originalrechnung. Nicht gespeichert.
        split_period: Nur bei einem Ausschnitt, der aus einem Split stammt:
            der vollständige Zeitraum dieses Splits. Nicht gespeichert.
    """

    property_id: int
    cost_type: CostType
    amount: Money
    period: DateRange
    allocation_key: AllocationKey
    consumption_share: int = DEFAULT_CONSUMPTION_SHARE
    description: str = ""
    direct_shares: dict[int, Money] = field(default_factory=dict)
    invoice_date: date | None = None
    consumption: Decimal | None = None
    consumption_unit: str = ""
    excluded: bool = False
    splits: list[InvoiceSplit] = field(default_factory=list)
    difference_key: AllocationKey = AllocationKey.CONSUMPTION
    bundle_id: int | None = None
    co2_cost: Money | None = None
    co2_emission: Decimal | None = None
    co2_factor: Decimal | None = None
    id: int | None = None
    full_amount: Money | None = None
    full_period: DateRange | None = None
    split_period: DateRange | None = None

    # ------------------------------------------------------------------ #
    # Prüfung
    # ------------------------------------------------------------------ #
    def validate(self) -> None:
        """Prüft die fachlichen Regeln; wirft ``ValidationError`` bei Verstößen."""
        self.description = optional_text(self.description, "Beschreibung", max_length=300)
        self.consumption_unit = optional_text(self.consumption_unit, "Einheit", max_length=12)
        require_positive(self.amount.cents, "Betrag")
        if self.consumption is not None:
            require_non_negative(self.consumption, "Verbrauch")
            if not self.consumption_unit:
                raise ValidationError("Bitte zum Verbrauch eine Einheit angeben (z. B. kWh oder m³).")

        if self.co2_cost is not None and self.co2_cost.is_negative:
            raise ValidationError("Die CO2-Kosten dürfen nicht negativ sein.")
        if self.co2_emission is not None:
            require_non_negative(self.co2_emission, "CO2-Ausstoß")
        if self.co2_factor is not None:
            require_in_range(self.co2_factor, Decimal(0), Decimal(10), "Emissionsfaktor (kg/kWh)")

        allowed = self.cost_type.allowed_keys()
        if self.allocation_key not in allowed:
            allowed_labels = ", ".join(key.label for key in allowed)
            raise ValidationError(
                f"Für '{self.cost_type.label}' ist der Umlageschlüssel "
                f"'{self.allocation_key.label}' nicht zulässig. Erlaubt: {allowed_labels}."
            )

        if self.allocation_key is AllocationKey.CONSUMPTION_AREA:
            require_in_range(
                self.consumption_share,
                MIN_CONSUMPTION_SHARE,
                MAX_CONSUMPTION_SHARE,
                "Verbrauchsanteil (%)",
            )

        if self.allocation_key is not AllocationKey.CONSUMPTION:
            # Die Differenzverteilung gehört nur zum reinen Verbrauchsschlüssel.
            self.difference_key = AllocationKey.CONSUMPTION
        elif self.difference_key not in DIFFERENCE_KEYS:
            raise ValidationError(
                f"Die Verbrauchsdifferenz kann nicht nach '{self.difference_key.label}' verteilt werden."
            )

        if self.allocation_key is AllocationKey.DIRECT:
            self._validate_direct_shares()
        else:
            # Direkte Anteile gehören nur zum Schlüssel DIRECT.
            self.direct_shares = {}

        self.splits = sorted(self.splits, key=lambda split: split.period.start)
        validate_splits(self.splits, self.period, self.amount, self.consumption)

    def _validate_direct_shares(self) -> None:
        """Direkte Anteile dürfen nicht negativ sein und die Rechnung nicht übersteigen."""
        self.direct_shares = {tid: share for tid, share in self.direct_shares.items() if not share.is_zero}
        if not self.direct_shares:
            raise ValidationError("Bitte für mindestens einen Mieter einen Betrag eintragen.")
        if any(share.is_negative for share in self.direct_shares.values()):
            raise ValidationError("Die Beträge je Mieter dürfen nicht negativ sein.")
        if self.assigned_amount > self.amount:
            raise ValidationError(
                "Die Summe der Beträge je Mieter ist höher als der Gesamtbetrag der Kostenposition."
            )

    # ------------------------------------------------------------------ #
    # Abgeleitete Werte
    # ------------------------------------------------------------------ #
    @property
    def title(self) -> str:
        """Anzeigename: Kostenart, ergänzt um die Beschreibung, falls vorhanden."""
        if self.description:
            return f"{self.cost_type.label} – {self.description}"
        return self.cost_type.label

    @property
    def allocation_label(self) -> str:
        """Umlageschlüssel als Text, bei Mischschlüssel mit Prozentangabe."""
        if self.allocation_key is AllocationKey.CONSUMPTION_AREA:
            area_share = 100 - self.consumption_share
            return f"{self.consumption_share} % Verbrauch, {area_share} % Wohnfläche"
        if self.splits_difference:
            return f"{self.allocation_key.label}, Differenz nach {self.difference_key.label}"
        return self.allocation_key.label

    @property
    def splits_difference(self) -> bool:
        """``True``, wenn eine Verbrauchsdifferenz nach einem eigenen Schlüssel verteilt werden soll."""
        return (
            self.allocation_key is AllocationKey.CONSUMPTION
            and self.consumption is not None
            and self.difference_key is not AllocationKey.CONSUMPTION
        )

    @property
    def assigned_amount(self) -> Money:
        """Summe der direkt zugeordneten Beträge (nur Schlüssel ``DIRECT``)."""
        return sum(self.direct_shares.values(), Money.zero())

    @property
    def split_gaps(self) -> list[DateRange]:
        """Von den Splits nicht abgedeckte Zeiträume (siehe :func:`uncovered_periods`)."""
        return uncovered_periods(self.splits, self.period)

    @property
    def is_prorated(self) -> bool:
        """``True``, wenn dies nur ein zeitlicher Ausschnitt einer Rechnung ist."""
        return self.full_period is not None

    # ------------------------------------------------------------------ #
    # Zeitliche Ausschnitte
    # ------------------------------------------------------------------ #
    def pieces(self, billing_period: DateRange) -> list[Cost]:
        """Zerlegt die Rechnung in die Stücke, die in den Abrechnungszeitraum fallen.

        Das ist die zentrale Regel dafür, *wann* Kosten angefallen sind:

        1. **Mit Splits:** Jeder Split ist ein eigenes Stück mit eigenem
           Zeitraum, Betrag und Verbrauch. Liegt ein Split nur teilweise im
           Abrechnungszeitraum, wird *dieser Split* taggenau geteilt.
        2. **Ohne Splits:** Die gesamte Rechnung ist ein Stück und wird
           taggenau über den Rechnungszeitraum geteilt (lineare Annahme).

        Jedes Stück ist eine Kopie der Rechnung mit eigenem ``period`` und
        ``amount`` - die Kostenverteilung behandelt es wie eine eigene
        Position. So zahlt bei einem Mieterwechsel jeder Mieter die Abschnitte,
        in denen er tatsächlich gewohnt hat.

        Returns:
            Die Stücke in zeitlicher Reihenfolge; leer, wenn die Rechnung den
            Abrechnungszeitraum nicht berührt. Liegt eine Rechnung ohne Splits
            vollständig im Zeitraum, ist das einzige Stück die Rechnung selbst.
        """
        # Ein einziger Split über den ganzen Rechnungszeitraum (z. B. um Verbrauch
        # und Kosten aus Volumen und Preisen berechnen zu lassen) teilt nichts
        # auf - die Rechnung verhält sich dann wie eine ohne Splits.
        splits = self._splits_with_difference_spread()
        covers_everything = len(splits) == 1 and splits[0].period == self.period
        if not splits or covers_everything:
            overlap = self.period.intersection(billing_period)
            if overlap is None:
                return []
            if overlap == self.period:
                return [self]
            return [self._cut(self.period, overlap, self.amount, self.consumption, split_period=None)]

        # CO2 je Split nur verwenden, wenn *alle* Splits Angaben haben - sonst passt die Summe nicht.
        own_co2_cost = all(split.co2_cost is not None for split in splits)
        own_co2_emission = all(split.co2_emission is not None for split in splits)
        result: list[Cost] = []
        for split in splits:
            overlap = split.period.intersection(billing_period)
            if overlap is not None:
                piece = self._cut(split.period, overlap, split.amount, split.consumption, split.period)
                factor = Decimal(overlap.days) / Decimal(split.period.days)
                if own_co2_cost and split.co2_cost is not None:
                    piece = replace(piece, co2_cost=_scale(split.co2_cost, factor))
                if own_co2_emission and split.co2_emission is not None:
                    piece = replace(
                        piece, co2_emission=(split.co2_emission * factor).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
                    )
                result.append(piece)
        return result

    def _splits_with_difference_spread(self) -> list[InvoiceSplit]:
        """Die Splits ohne Differenz-Eintrag - dessen Betrag ist nach Tagen auf sie verteilt.

        So wird die Differenz linear über den Zeitraum gestreckt, ohne als
        eigenes (womöglich negatives) Stück in die Kostenverteilung zu gehen.
        """
        regular = [split for split in self.splits if not split.is_difference]
        difference = sum((split.amount for split in self.splits if split.is_difference), Money.zero())
        if difference.is_zero or not regular:
            return regular
        shares = difference.allocate([split.period.days for split in regular])
        return [replace(split, amount=split.amount + share) for split, share in zip(regular, shares)]

    def prorate(self, billing_period: DateRange) -> Cost | None:
        """Der Teil der Rechnung im Abrechnungszeitraum als *eine* Summe.

        Fasst :meth:`pieces` zusammen - für Übersichten wie die aggregierten
        Rechnungen je Kalenderjahr, wo nur Betrag und Verbrauch im Jahr zählen.

        Returns:
            * ``None``, wenn die Rechnung den Zeitraum nicht berührt,
            * die Rechnung selbst, wenn sie vollständig darin liegt,
            * sonst eine Kopie mit dem Betrag und Verbrauch im Zeitraum.
        """
        overlap = self.period.intersection(billing_period)
        if overlap is None:
            return None
        if overlap == self.period:
            return self

        pieces = self.pieces(billing_period)
        consumptions = [piece.consumption for piece in pieces if piece.consumption is not None]
        direct: dict[int, Money] = {}
        for piece in pieces:
            for tenant_id, share in piece.direct_shares.items():
                direct[tenant_id] = direct.get(tenant_id, Money.zero()) + share
        co2_costs = [piece.co2_cost for piece in pieces if piece.co2_cost is not None]
        co2_emissions = [piece.co2_emission for piece in pieces if piece.co2_emission is not None]
        return replace(
            self,
            amount=sum((piece.amount for piece in pieces), Money.zero()),
            consumption=sum(consumptions, Decimal(0)) if consumptions else None,
            co2_cost=sum(co2_costs, Money.zero()) if co2_costs else None,
            co2_emission=sum(co2_emissions, Decimal(0)) if co2_emissions else None,
            period=overlap,
            direct_shares=direct,
            full_amount=self.amount,
            full_period=self.period,
            split_period=None,
        )

    def _cut(
        self,
        source_period: DateRange,
        overlap: DateRange,
        source_amount: Money,
        source_consumption: Decimal | None,
        split_period: DateRange | None,
    ) -> Cost:
        """Schneidet aus einem Abschnitt den Teil ``overlap`` heraus (taggenau).

        Args:
            source_period: Zeitraum des Abschnitts (Rechnung oder Split).
            overlap: Der Teil davon, der in den Abrechnungszeitraum fällt.
            source_amount / source_consumption: Werte des ganzen Abschnitts.
            split_period: Zeitraum des Splits bzw. ``None`` bei einer Rechnung ohne Splits.
        """
        factor = Decimal(overlap.days) / Decimal(source_period.days)
        amount = _scale(source_amount, factor)
        consumption = source_consumption
        if consumption is not None:
            consumption = (consumption * factor).quantize(_THOUSANDTH, rounding=ROUND_HALF_UP)

        # Direkt zugeordnete Beträge beziehen sich auf die ganze Rechnung und
        # werden im Verhältnis des Stücks zum Rechnungsbetrag mitgenommen.
        share_factor = Decimal(amount.cents) / Decimal(self.amount.cents) if self.amount.cents else Decimal(0)
        # CO2 entsteht mit dem Verbrauch - der Anteil folgt den kWh, ohne Verbrauch dem Betrag.
        co2_factor = share_factor
        if consumption is not None and self.consumption:
            co2_factor = consumption / self.consumption
        return replace(
            self,
            amount=amount,
            consumption=consumption,
            co2_cost=_scale(self.co2_cost, co2_factor) if self.co2_cost is not None else None,
            co2_emission=(
                (self.co2_emission * co2_factor).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
                if self.co2_emission is not None
                else None
            ),
            period=overlap,
            direct_shares={tenant_id: _scale(share, share_factor) for tenant_id, share in self.direct_shares.items()},
            splits=[],
            full_amount=self.amount,
            full_period=self.period,
            split_period=split_period,
        )


def _scale(money: Money, factor: Decimal) -> Money:
    """Multipliziert einen Betrag mit einem Faktor (kaufmännisch gerundet)."""
    return Money(int((Decimal(money.cents) * factor).quantize(Decimal(1), rounding=ROUND_HALF_UP)))


# Die folgenden Helfer formatieren Fehlermeldungen. Sie stehen hier (statt in
# utils.formatters), weil das Domain-Modell nicht von der Formatierungsschicht
# abhängen soll - utils.formatters importiert seinerseits Domain-Klassen.
def _euro(money: Money) -> str:
    return f"{money.amount:.2f} €".replace(".", ",")


def _number(value: Decimal) -> str:
    return f"{value.normalize():f}".replace(".", ",")


def _short(period: DateRange) -> str:
    return f"{period.start.strftime('%d.%m.%Y')} – {period.end.strftime('%d.%m.%Y')}"
