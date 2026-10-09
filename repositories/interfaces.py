"""Abstraktionen (Protokolle) der Repositories.

Dependency Inversion: Die Service-Schicht hängt nur von diesen Protokollen ab,
nicht von SQLite. In Tests lassen sich dadurch einfache Attrappen einsetzen,
und ein späterer Wechsel der Datenbank beträfe nur das Paket ``repositories``.

Ein ``Protocol`` ist "strukturelle Typisierung": Eine Klasse erfüllt es
automatisch, sobald sie die genannten Methoden besitzt - sie muss nicht davon
erben.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Mapping, Protocol, TypeVar

from models.apartment import Apartment
from models.attachment import CostAttachment
from models.audit_entry import AuditEntry
from models.cost import Cost
from models.billing import BillingPeriodOverride
from models.cost_type import CostType
from models.enums import AuditAction, MeterType
from models.invoice_bundle import InvoiceBundle
from models.invoice_checklist import CostTypeRelevance
from models.invoice_group import InvoiceGroupSetting
from models.meter_reading import MeterReading
from models.photo import Photo
from models.prepayment_rate import PrepaymentRate
from models.property import Property
from models.tenant import Tenant, TenantKeyOverride
from models.user import User
from models.value_objects import DateRange

T = TypeVar("T")


class Repository(Protocol[T]):
    """Grundoperationen, die jedes Entity-Repository anbietet."""

    def get(self, entity_id: int) -> T:
        """Lädt einen Datensatz; wirft ``NotFoundError``, wenn er fehlt."""
        ...

    def find(self, entity_id: int) -> T | None:
        """Lädt einen Datensatz oder gibt ``None`` zurück."""
        ...

    def list_all(self) -> list[T]:
        """Lädt alle Datensätze in der Standard-Sortierung."""
        ...

    def add(self, entity: T) -> T:
        """Speichert einen neuen Datensatz und gibt ihn mit vergebener ID zurück."""
        ...

    def update(self, entity: T) -> None:
        """Überschreibt einen bestehenden Datensatz."""
        ...

    def delete(self, entity_id: int) -> None:
        """Löscht einen Datensatz."""
        ...


class TransactionManager(Protocol):
    """Erlaubt Services, mehrere Repository-Aufrufe atomar zu bündeln."""

    def transaction(self) -> Any:
        """Kontextmanager: Der ``with``-Block läuft in einer Transaktion."""
        ...


class PropertyRepository(Repository[Property], Protocol):
    """Zugriff auf Immobilien."""


class ApartmentRepository(Repository[Apartment], Protocol):
    """Zugriff auf Wohnungen."""

    def list_by_property(self, property_id: int) -> list[Apartment]:
        """Alle Wohnungen einer Immobilie."""
        ...


class TenantRepository(Repository[Tenant], Protocol):
    """Zugriff auf Mieter."""

    def list_by_apartment(self, apartment_id: int) -> list[Tenant]:
        """Alle (auch ehemaligen) Mieter einer Wohnung."""
        ...

    def list_by_property(self, property_id: int) -> list[Tenant]:
        """Alle Mieter sämtlicher Wohnungen einer Immobilie."""
        ...


class MeterReadingRepository(Repository[MeterReading], Protocol):
    """Zugriff auf Zählerstände."""

    def list_filtered(
        self,
        property_id: int | None = None,
        apartment_id: int | None = None,
        meter_type: MeterType | None = None,
    ) -> list[MeterReading]:
        """Zählerstände, optional eingeschränkt; neueste zuerst."""
        ...

    def list_for_property_and_type(self, property_id: int, meter_type: MeterType) -> list[MeterReading]:
        """Alle Ablesungen einer Zählerart in einer Immobilie (für die Verbrauchsermittlung)."""
        ...


class CostTypeRepository(Repository[CostType], Protocol):
    """Zugriff auf Kostenarten."""

    def count_costs(self, cost_type_id: int) -> int:
        """Anzahl der Kostenpositionen, die diese Kostenart verwenden."""
        ...


class CostRepository(Repository[Cost], Protocol):
    """Zugriff auf Kostenpositionen."""

    def list_by_property(self, property_id: int) -> list[Cost]:
        """Alle Kostenpositionen einer Immobilie."""
        ...

    def list_by_bundle(self, bundle_id: int) -> list[Cost]:
        """Die Positionen einer Sammelrechnung."""
        ...


class InvoiceBundleRepository(Repository[InvoiceBundle], Protocol):
    """Zugriff auf Sammelrechnungen."""

    def list_by_property(self, property_id: int) -> list[InvoiceBundle]:
        """Alle Sammelrechnungen einer Immobilie."""
        ...


class PrepaymentRateRepository(Repository[PrepaymentRate], Protocol):
    """Zugriff auf die Historie der vereinbarten Vorauszahlungen."""

    def list_by_tenant(self, tenant_id: int) -> list[PrepaymentRate]:
        """Alle Raten eines Mieters, älteste zuerst."""
        ...

    def list_by_property(self, property_id: int) -> list[PrepaymentRate]:
        """Die Raten aller Mieter einer Immobilie."""
        ...


class BillingPeriodOverrideRepository(Repository[BillingPeriodOverride], Protocol):
    """Zugriff auf von Hand angepasste Abrechnungszeiträume."""

    def list_by_period(self, period: DateRange) -> list[BillingPeriodOverride]:
        """Alle Overrides, die für genau diesen Abrechnungszeitraum gelten."""
        ...


class InvoiceGroupSettingRepository(Repository[InvoiceGroupSetting], Protocol):
    """Zugriff auf die Einstellungen der aggregierten Rechnungen."""

    def list_by_property(self, property_id: int) -> list[InvoiceGroupSetting]:
        """Alle Einstellungen einer Immobilie."""
        ...


class TenantKeyOverrideRepository(Repository[TenantKeyOverride], Protocol):
    """Zugriff auf abweichende Umlageschlüssel je Mieter."""

    def list_by_tenant(self, tenant_id: int) -> list[TenantKeyOverride]:
        """Alle Abweichungen eines Mieters."""
        ...

    def list_by_property(self, property_id: int) -> list[TenantKeyOverride]:
        """Die Abweichungen aller Mieter einer Immobilie."""
        ...


class CostTypeRelevanceRepository(Repository[CostTypeRelevance], Protocol):
    """Zugriff auf die von Hand gesetzte Relevanz von Kostenarten."""

    def list_by_property(self, property_id: int) -> list[CostTypeRelevance]:
        """Alle Einträge einer Immobilie."""
        ...


class CostAttachmentRepository(Repository[CostAttachment], Protocol):
    """Zugriff auf die Belege von Rechnungen."""

    def list_metadata(self, cost_id: int | None = None, bundle_id: int | None = None) -> list[CostAttachment]:
        """Die Belege einer Rechnung bzw. Sammelrechnung *ohne* Dateidaten."""
        ...

    def count_by_cost(self) -> dict[int, int]:
        """Anzahl der Belege je Rechnungs-ID."""
        ...


class PhotoRepository(Repository[Photo], Protocol):
    """Zugriff auf Fotos."""

    def list_metadata(
        self,
        reading_id: int | None = None,
        standalone_only: bool = False,
    ) -> list[Photo]:
        """Fotos *ohne* Bilddaten (schnell, für Listen)."""
        ...

    def reading_ids_with_photos(self) -> set[int]:
        """IDs aller Zählerstände, zu denen mindestens ein Foto existiert."""
        ...


class UserRepository(Repository[User], Protocol):
    """Zugriff auf die Benutzer einer Datenbank."""

    def find_by_name(self, name: str) -> User | None:
        """Sucht einen Benutzer anhand seines Namens."""
        ...

    def touch_last_login(self, user_id: int, when: datetime) -> None:
        """Aktualisiert den Zeitpunkt der letzten Anmeldung (ohne Protokolleintrag)."""
        ...


class AuditRecorder(Protocol):
    """Schreibender Zugriff auf das Änderungsprotokoll (von Repositories genutzt)."""

    def record(
        self,
        table_name: str,
        record_id: int,
        action: AuditAction,
        old_values: Mapping[str, Any] | None,
        new_values: Mapping[str, Any] | None,
    ) -> None:
        """Schreibt einen Protokolleintrag in der laufenden Transaktion."""
        ...


class AuditRepository(AuditRecorder, Protocol):
    """Lesender und schreibender Zugriff auf das Änderungsprotokoll."""

    def list_entries(self, limit: int = 500, table_name: str | None = None) -> list[AuditEntry]:
        """Die neuesten Protokolleinträge, optional nur für eine Tabelle."""
        ...

    def list_table_names(self) -> list[str]:
        """Alle Tabellennamen, zu denen Protokolleinträge existieren."""
        ...


class SettingsRepository(Protocol):
    """Zugriff auf die Schlüssel-Wert-Einstellungen."""

    def get_all(self) -> dict[str, str]:
        """Alle Einstellungen als Wörterbuch."""
        ...

    def set_many(self, values: Mapping[str, str]) -> None:
        """Speichert mehrere Einstellungen in einer Transaktion."""
        ...
