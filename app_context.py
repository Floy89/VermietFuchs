"""Composition Root: Hier - und nur hier - werden konkrete Klassen verdrahtet.

Ein :class:`AppContext` gehört zu genau einer geöffneten Benutzer-Datenbank.
Er erzeugt die SQLite-Repositories und reicht sie an die Services weiter
(Dependency Injection per Konstruktor). Die Oberfläche bekommt nur den
fertigen Kontext und spricht ausschließlich mit den Services.
"""

from __future__ import annotations

from database.db_manager import DatabaseManager
from models.user import User
from repositories.apartment_repository import SqliteApartmentRepository
from repositories.attachment_repository import SqliteCostAttachmentRepository
from repositories.audit_repository import SqliteAuditRepository
from repositories.bundle_repository import SqliteInvoiceBundleRepository
from repositories.billing_repositories import (
    SqliteBillingPeriodOverrideRepository,
    SqliteCostTypeRelevanceRepository,
    SqliteInvoiceGroupSettingRepository,
    SqliteTenantKeyOverrideRepository,
)
from repositories.cost_repository import SqliteCostRepository
from repositories.cost_type_repository import SqliteCostTypeRepository
from repositories.document_profile_repository import SqliteDocumentProfileRepository
from repositories.meter_reading_repository import SqliteMeterReadingRepository
from repositories.photo_repository import SqlitePhotoRepository
from repositories.prepayment_rate_repository import SqlitePrepaymentRateRepository
from repositories.property_repository import SqlitePropertyRepository
from repositories.settings_repository import SqliteSettingsRepository
from repositories.tenant_repository import SqliteTenantRepository
from services.apartment_service import ApartmentService
from services.attachment_service import AttachmentService
from services.audit_service import AuditService
from services.billing_service import BillingService
from services.cost_allocation_service import CostAllocationService
from services.cost_service import CostService
from services.cost_type_service import CostTypeService
from services.dashboard_service import DashboardService
from services.document_service import DocumentService
from services.invoice_bundle_service import InvoiceBundleService
from services.invoice_checklist_service import InvoiceChecklistService
from services.invoice_group_service import InvoiceGroupService
from services.key_override_service import KeyOverrideService
from services.meter_reading_service import MeterReadingService
from services.pdf_export_service import PdfExportService
from services.photo_service import PhotoService
from services.prepayment_rate_service import PrepaymentRateService
from services.property_service import PropertyService
from services.receipt_scan_service import ReceiptScanService
from services.settings_service import SettingsService
from services.tenant_service import TenantService
from services.trend_service import TrendService


class AppContext:
    """Bündelt alle Services für einen angemeldeten Benutzer.

    Attributes:
        user: Der angemeldete Benutzer.
        database: Die geöffnete Benutzer-Datenbank.
    """

    def __init__(
        self,
        database: DatabaseManager,
        user: User,
        receipt_scan: ReceiptScanService | None = None,
    ) -> None:
        """
        Args:
            database: Bereits geöffnete Datenbank.
            user: Der Benutzer, dem die Datenbank gehört.
            receipt_scan: Optionale Belegerkennung. Sie hängt am Rechner, nicht
                an der Datenbank, und wird deshalb von außen hereingereicht.
                ``None`` bedeutet: Funktion nicht vorhanden (z. B. in Tests).
        """
        self.database = database
        self.user = user
        self.receipt_scan = receipt_scan

        # --- Repositories (Infrastructure) ---------------------------------
        audit = SqliteAuditRepository(database)
        properties = SqlitePropertyRepository(database, audit)
        apartments = SqliteApartmentRepository(database, audit)
        tenants = SqliteTenantRepository(database, audit)
        readings = SqliteMeterReadingRepository(database, audit)
        cost_types = SqliteCostTypeRepository(database, audit)
        costs = SqliteCostRepository(database, audit, cost_types)
        overrides = SqliteBillingPeriodOverrideRepository(database, audit)
        group_settings = SqliteInvoiceGroupSettingRepository(database, audit)
        key_overrides = SqliteTenantKeyOverrideRepository(database, audit)
        prepayment_rates = SqlitePrepaymentRateRepository(database, audit)
        photos = SqlitePhotoRepository(database, audit)
        settings = SqliteSettingsRepository(database, audit)

        # --- Services (Application Layer) ----------------------------------
        self.properties = PropertyService(properties, apartments, costs)
        self.apartments = ApartmentService(apartments, tenants, readings)
        self.tenants = TenantService(tenants, apartments, costs, prepayment_rates, database)
        self.prepayment_rates = PrepaymentRateService(prepayment_rates, tenants, database)
        self.meter_readings = MeterReadingService(readings, photos, database)
        self.photos = PhotoService(photos)
        self.costs = CostService(costs)
        self.bundles = InvoiceBundleService(SqliteInvoiceBundleRepository(database, audit), costs, database)
        self.attachments = AttachmentService(SqliteCostAttachmentRepository(database, audit))
        self.documents = DocumentService(SqliteDocumentProfileRepository(database, audit))
        self.cost_types = CostTypeService(cost_types)
        self.invoice_groups = InvoiceGroupService(costs, group_settings)
        self.invoice_checklist = InvoiceChecklistService(
            costs, cost_types, SqliteCostTypeRelevanceRepository(database, audit)
        )
        self.key_overrides = KeyOverrideService(key_overrides, tenants, cost_types)
        self.billing = BillingService(
            properties,
            apartments,
            tenants,
            costs,
            prepayment_rates,
            overrides,
            self.invoice_groups,
            self.key_overrides,
            self.meter_readings,
            CostAllocationService(),
        )
        self.dashboard = DashboardService(properties, apartments, tenants, self.billing)
        self.trends = TrendService(self.billing, self.invoice_checklist, costs)
        self.settings = SettingsService(settings)
        self.audit = AuditService(audit)
        self.pdf_export = PdfExportService()

    def close(self) -> None:
        """Schließt die Datenbank (z. B. beim Benutzerwechsel oder Beenden)."""
        self.database.close()
