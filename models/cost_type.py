"""Entity: Kostenart (z. B. "Grundsteuer" oder eine selbst angelegte Art).

Kostenarten sind Daten, kein fest einprogrammierter Aufzählungstyp: Die
Anwendung liefert den Katalog der Betriebskostenverordnung (§ 2 BetrKV) mit,
und der Benutzer kann beliebige eigene Kostenarten ergänzen.
"""

from __future__ import annotations

from dataclasses import dataclass

from models.enums import AllocationKey, MeterType
from models.exceptions import ValidationError
from utils.validators import optional_text, require_text


@dataclass
class CostType:
    """Eine Kostenart mit ihren Umlage-Regeln.

    Attributes:
        name: Bezeichnung, wie sie in Oberfläche und Abrechnung erscheint.
        default_key: Umlageschlüssel, der bei einer neuen Kostenposition
            vorgeschlagen wird.
        meter_type: Zählerart, nach deren Verbrauch verteilt werden kann;
            ``None``, wenn es für diese Kostenart keinen Zähler gibt.
        heating_rule: ``True`` für Heiz- und Warmwasserkosten. Für sie
            schreibt die Heizkostenverordnung eine Verteilung zu 50–70 % nach
            Verbrauch vor (oder die Übernahme einer Messdienst-Abrechnung).
        legal_basis: Fundstelle, z. B. "§ 2 Nr. 1 BetrKV" (nur zur Information).
        is_builtin: ``True`` für mitgelieferte Katalog-Einträge; sie lassen
            sich weder ändern noch löschen.
        sort_order: Position in Auswahllisten (Katalog-Reihenfolge).
    """

    name: str
    default_key: AllocationKey = AllocationKey.AREA
    meter_type: MeterType | None = None
    heating_rule: bool = False
    legal_basis: str = ""
    is_builtin: bool = False
    sort_order: int = 1000
    id: int | None = None

    @property
    def label(self) -> str:
        """Anzeigename (identisch mit ``name``)."""
        return self.name

    def allowed_keys(self) -> tuple[AllocationKey, ...]:
        """Zulässige Umlageschlüssel; der Standard-Schlüssel steht vorn.

        * Heizkosten: Mischschlüssel Verbrauch + Fläche oder direkte
          Zuordnung (Abrechnung eines Messdienstes).
        * Alle anderen: Wohnfläche, Personenzahl, Wohneinheiten, direkte
          Zuordnung - und Verbrauch, sofern es einen passenden Zähler gibt.
        * Immer zuletzt: "nicht umlegbar" - die Rechnung trägt der Eigentümer allein.
        """
        if self.heating_rule:
            keys = [AllocationKey.CONSUMPTION_AREA, AllocationKey.DIRECT]
        else:
            keys = [AllocationKey.AREA, AllocationKey.PERSONS, AllocationKey.UNITS, AllocationKey.DIRECT]
            if self.meter_type is not None:
                keys.insert(0, AllocationKey.CONSUMPTION)
        keys.append(AllocationKey.OWNER)
        if self.default_key in keys:
            keys.remove(self.default_key)
            keys.insert(0, self.default_key)
        return tuple(keys)

    def override_keys(self) -> tuple[AllocationKey, ...]:
        """Schlüssel, die ein einzelner Mietvertrag abweichend vom Standard vereinbaren kann.

        * Heiz- und Warmwasserkosten: keine - die Heizkostenverordnung gibt
          die Verteilung zwingend vor.
        * Sonst: Wohnfläche, Personenzahl, Wohneinheiten und - mit Zähler -
          Verbrauch. "Direkte Zuordnung" gehört nicht dazu: Dort stehen die
          Beträge je Mieter ohnehin schon an der Rechnung.
        """
        if self.heating_rule:
            return ()
        keys = [AllocationKey.AREA, AllocationKey.PERSONS, AllocationKey.UNITS]
        if self.meter_type is not None:
            keys.append(AllocationKey.CONSUMPTION)
        return tuple(keys)

    def validate(self) -> None:
        """Prüft die fachlichen Regeln; wirft ``ValidationError`` bei Verstößen."""
        self.name = require_text(self.name, "Bezeichnung", max_length=80)
        self.legal_basis = optional_text(self.legal_basis, "Grundlage", max_length=80)
        if self.default_key not in self.allowed_keys():
            raise ValidationError(
                f"Der Schlüssel '{self.default_key.label}' ist für diese Kostenart nicht möglich"
                + (" - dafür muss ein Zähler gewählt sein." if self.default_key is AllocationKey.CONSUMPTION else ".")
            )
