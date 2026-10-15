"""Belege ohne KI auslesen - und aus den Korrekturen des Benutzers lernen."""

from __future__ import annotations

import logging
from datetime import date, datetime
from decimal import Decimal
from typing import Sequence

from models.cost import Cost
from models.document import DocumentDraft, DocumentProfile
from models.exceptions import ValidationError
from repositories.interfaces import DocumentProfileRepository
from services.document_reader import learn_anchor, read_document, split_lines

logger = logging.getLogger(__name__)


class DocumentService:
    """Liest Belegtexte mit festen Regeln aus und merkt sich je Absender, was richtig war.

    Lernen heißt hier: Beim Speichern einer Rechnung, die aus einem Beleg
    gefüllt wurde, wird festgehalten, welche Kostenart, Beschreibung und
    welchen Umlageschlüssel der Benutzer gewählt hat - und in welcher Zeile
    des Belegs Betrag, Daten und Verbrauch standen, *so wie der Benutzer sie
    am Ende eingetragen hat*. Hat er einen falsch gelesenen Wert korrigiert,
    findet die Anwendung beim nächsten Beleg desselben Absenders die richtige
    Zeile.
    """

    def __init__(self, profiles: DocumentProfileRepository) -> None:
        self._profiles = profiles

    def read(self, text: str, cost_type_names: Sequence[str], source: str = "pdf") -> DocumentDraft:
        """Liest einen Beleg; ein vorhandenes Profil des Absenders fließt ein."""
        draft = read_document(text, cost_type_names, source=source)
        if draft.issuer_key:
            profile = self._profiles.find_by_issuer(draft.issuer_key)
            if profile is not None:
                draft = read_document(text, cost_type_names, profile=profile, source=source)
        logger.info(
            "Beleg ohne KI gelesen (%s): Absender %s, gelernte Felder %s",
            source,
            draft.issuer_key or "unbekannt",
            ", ".join(draft.learned_fields) or "keine",
        )
        return draft

    def learn(self, draft: DocumentDraft, cost: Cost) -> DocumentProfile | None:
        """Merkt sich, was der Benutzer zu diesem Beleg gespeichert hat.

        Args:
            draft: Der Vorschlag, mit dem der Dialog gefüllt wurde (samt Belegtext).
            cost: Die Rechnung, wie sie gespeichert wurde.

        Returns:
            Das aktualisierte Profil - oder ``None``, wenn der Absender nicht
            erkannt wurde (dann lässt sich nichts zuordnen).
        """
        if not draft.issuer_key:
            return None
        profile = self._profiles.find_by_issuer(draft.issuer_key) or DocumentProfile(issuer_key=draft.issuer_key)
        lines = split_lines(draft.text)

        values: dict[str, date | Decimal] = {
            "amount": cost.amount.amount,
            "period_start": cost.period.start,
            "period_end": cost.period.end,
        }
        if cost.invoice_date is not None:
            values["invoice_date"] = cost.invoice_date
        if cost.consumption is not None:
            values["consumption"] = cost.consumption
        for field_name, value in values.items():
            kind = "number" if isinstance(value, Decimal) else "date"
            anchor = learn_anchor(lines, value, kind)
            if anchor is not None:
                profile.anchors[field_name] = anchor

        profile.issuer_name = draft.issuer_name or profile.issuer_name
        profile.cost_type_id = cost.cost_type.id
        profile.allocation_key = cost.allocation_key
        profile.description = cost.description
        profile.consumption_unit = cost.consumption_unit
        vat = next(
            (s.calculation.vat_percent for s in cost.splits if s.calculation and s.calculation.vat_percent is not None),
            None,
        )
        if vat is not None:
            profile.vat_percent = vat
        profile.times_used += 1
        profile.updated_at = datetime.now()
        if profile.id is None:
            saved = self._profiles.add(profile)
        else:
            self._profiles.update(profile)
            saved = profile
        logger.info("Absender-Profil gelernt: %s (%d. Beleg)", saved.issuer_key, saved.times_used)
        return saved

    def profiles(self) -> list[DocumentProfile]:
        """Alle gelernten Profile."""
        return self._profiles.list_all()

    def save_profile(self, profile: DocumentProfile) -> None:
        """Speichert ein von Hand geändertes Profil (Vorgaben oder entfernte Fundstellen)."""
        if profile.id is None:
            raise ValidationError("Nur bereits gelernte Profile lassen sich ändern.")
        profile.updated_at = datetime.now()
        self._profiles.update(profile)

    def forget_anchor(self, profile_id: int, field_name: str) -> DocumentProfile:
        """Vergisst eine einzelne Fundstelle; beim nächsten Beleg gilt dafür wieder die allgemeine Regel."""
        profile = self._profiles.get(profile_id)
        if profile.anchors.pop(field_name, None) is not None:
            self.save_profile(profile)
        return profile

    def forget(self, profile_id: int) -> None:
        """Löscht ein gelerntes Profil, z. B. wenn es in die Irre führt."""
        self._profiles.delete(profile_id)

    def forget_all(self) -> int:
        """Löscht alle gelernten Profile und gibt ihre Anzahl zurück."""
        profiles = self._profiles.list_all()
        for profile in profiles:
            if profile.id is not None:
                self._profiles.delete(profile.id)
        return len(profiles)
