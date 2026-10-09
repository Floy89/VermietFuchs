"""Hauptfenster: Kopfzeile mit Benutzer-Menü und die Tabs."""

from __future__ import annotations

import logging

from PyQt6.QtCore import QSize, Qt, pyqtSignal
from PyQt6.QtGui import QAction, QCloseEvent
from PyQt6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMenu,
    QMessageBox,
    QTabWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

import config
from app_context import AppContext
from ui import icons, styles
from ui.dialogs.receipt_scan_dialog import ReceiptScanSetupDialog
from ui.dialogs.settings_dialog import SettingsDialog
from ui.helpers import ErrorGuard
from ui.widgets.billing_tab import BillingTab
from ui.widgets.common import BaseTab
from ui.widgets.dashboard_tab import DashboardTab
from ui.widgets.invoices_tab import InvoicesTab
from ui.widgets.meter_reading_tab import MeterReadingTab
from ui.widgets.property_tab import PropertyTab
from ui.widgets.reports_tab import ReportsTab
from ui.widgets.tenant_tab import TenantTab
from ui.widgets.trend_tab import TrendTab

logger = logging.getLogger(__name__)

#: Reihenfolge, Beschriftung und Icon der Tabs.
_TAB_DEFINITIONS: tuple[tuple[str, str, type[BaseTab]], ...] = (
    ("Dashboard", "dashboard", DashboardTab),
    ("Immobilien", "building", PropertyTab),
    ("Mieter", "users", TenantTab),
    ("Zählerstände", "gauge", MeterReadingTab),
    ("Rechnungen", "wallet", InvoicesTab),
    ("Abrechnung", "calculator", BillingTab),
    ("Berichte", "report", ReportsTab),
    ("Entwicklung", "chart", TrendTab),
)


class MainWindow(QMainWindow):
    """Das Hauptfenster für einen angemeldeten Benutzer.

    Signals:
        switch_user_requested: Der Benutzer möchte den Benutzer wechseln.
    """

    switch_user_requested = pyqtSignal()

    def __init__(self, context: AppContext) -> None:
        """
        Args:
            context: Kontext des angemeldeten Benutzers.
        """
        super().__init__()
        self._context = context
        self.setWindowTitle(f"{config.APP_NAME} – {context.user.name}")
        self.setWindowIcon(icons.app_icon())
        self.resize(1360, 860)
        self.setMinimumSize(1080, 680)

        central = QWidget()
        self._layout = QVBoxLayout(central)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(0)
        self.setCentralWidget(central)

        self._header: QFrame | None = None
        self._tabs: QTabWidget | None = None
        self._build_content()

        self._status_label = QLabel(f"Datenbank: {context.database.path}")
        self.statusBar().addWidget(self._status_label)
        self.statusBar().addPermanentWidget(QLabel(f"Version {config.APP_VERSION}"))

    # ------------------------------------------------------------------ #
    # Aufbau
    # ------------------------------------------------------------------ #
    def _build_content(self, current_index: int = 0) -> None:
        """Baut Kopfzeile und Tabs (neu) auf.

        Wird auch nach einem Wechsel des Farbschemas aufgerufen: Icons und
        farbige Tabellenzellen werden dabei in den neuen Farben erzeugt.
        """
        for old in (self._header, self._tabs):
            if old is not None:
                self._layout.removeWidget(old)
                # deleteLater() löscht erst beim nächsten Durchlauf der
                # Ereignisschleife. Ohne das Abhängen bliebe das alte Widget
                # bis dahin sichtbar und würde durch das neue durchscheinen.
                old.setParent(None)
                old.deleteLater()

        self._header = self._build_header()
        self._layout.addWidget(self._header)

        tabs = QTabWidget()
        tabs.setDocumentMode(True)
        tabs.setIconSize(QSize(18, 18))
        for title, icon_name, tab_class in _TAB_DEFINITIONS:
            tabs.addTab(tab_class(self._context), icons.icon(icon_name), title)
        tabs.currentChanged.connect(self._refresh_tab)
        self._layout.addWidget(tabs, 1)
        self._tabs = tabs

        tabs.setCurrentIndex(current_index)
        self._refresh_tab(current_index)

    def _build_header(self) -> QFrame:
        """Kopfzeile: Logo und Titel links, Benutzer-Menü rechts."""
        header = QFrame()
        header.setObjectName("HeaderBar")
        layout = QHBoxLayout(header)
        layout.setContentsMargins(20, 10, 20, 10)
        layout.setSpacing(10)

        logo = QLabel()
        logo.setPixmap(icons.app_pixmap(30))
        title = QLabel(config.APP_NAME)
        title.setObjectName("AppTitle")
        layout.addWidget(logo)
        layout.addWidget(title)
        layout.addStretch(1)

        user_button = QToolButton()
        user_button.setObjectName("UserButton")
        user_button.setText(f" {self._context.user.name}")
        user_button.setIcon(icons.icon("user"))
        user_button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        user_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        user_button.setMenu(self._build_user_menu(user_button))
        layout.addWidget(user_button)
        return header

    def _build_user_menu(self, parent: QWidget) -> QMenu:
        """Menü oben rechts: Benutzer wechseln, Einstellungen, Design, Info."""
        menu = QMenu(parent)

        switch_action = QAction(icons.icon("switch"), "Benutzer wechseln", menu)
        switch_action.triggered.connect(self.switch_user_requested.emit)
        settings_action = QAction(icons.icon("settings"), "Einstellungen …", menu)
        settings_action.triggered.connect(self._open_settings)

        is_dark = styles.current_theme() == styles.THEME_DARK
        theme_action = QAction(
            icons.icon("sun" if is_dark else "moon"),
            "Helles Design" if is_dark else "Dunkles Design",
            menu,
        )
        theme_action.triggered.connect(self._toggle_theme)
        info_action = QAction(icons.icon("info"), "Info", menu)
        info_action.triggered.connect(self._show_info)

        scan_action = QAction(icons.icon("camera"), "Belegerkennung …", menu)
        scan_action.triggered.connect(self._open_receipt_scan_setup)
        scan_action.setEnabled(self._context.receipt_scan is not None)

        menu.addAction(switch_action)
        menu.addAction(settings_action)
        menu.addAction(scan_action)
        menu.addAction(theme_action)
        menu.addSeparator()
        menu.addAction(info_action)
        return menu

    # ------------------------------------------------------------------ #
    # Verhalten
    # ------------------------------------------------------------------ #
    def _refresh_tab(self, index: int) -> None:
        """Lädt die Daten des Tabs neu, der gerade sichtbar wird."""
        if self._tabs is None:
            return
        tab = self._tabs.widget(index)
        if isinstance(tab, BaseTab):
            tab.refresh()

    def _open_settings(self) -> None:
        """Öffnet den Einstellungsdialog."""
        if SettingsDialog(self._context.settings, self).exec() and self._tabs is not None:
            # Einstellungen (z. B. Abzug der Vorauszahlungen) wirken sofort.
            self._refresh_tab(self._tabs.currentIndex())

    def _open_receipt_scan_setup(self) -> None:
        """Öffnet Einrichtung und Anleitung der optionalen Belegerkennung."""
        if self._context.receipt_scan is not None:
            ReceiptScanSetupDialog(self._context.receipt_scan, self).exec()

    def _toggle_theme(self) -> None:
        """Wechselt zwischen hellem und dunklem Design und merkt sich die Wahl."""
        new_theme = styles.THEME_LIGHT if styles.current_theme() == styles.THEME_DARK else styles.THEME_DARK
        with ErrorGuard(self):
            self._context.settings.set_theme(new_theme)
        app = QApplication.instance()
        if isinstance(app, QApplication):
            styles.apply_theme(app, new_theme)
        current = self._tabs.currentIndex() if self._tabs else 0
        self._build_content(current)

    def _show_info(self) -> None:
        """Zeigt Programmversion und Speicherorte."""
        box = QMessageBox(self)
        box.setWindowTitle(f"Über {config.APP_NAME}")
        box.setIconPixmap(icons.app_pixmap(96))
        box.setTextFormat(Qt.TextFormat.RichText)
        box.setText(
            f"<b>{config.APP_NAME}</b> &nbsp;Version {config.APP_VERSION}<br>"
            f"{config.APP_TAGLINE}<br><br>"
            "Verwaltung und Abrechnung von Nebenkosten für Mietwohnungen.<br>"
            "Alle Daten bleiben lokal auf diesem Rechner bzw. dem gewählten Laufwerk.<br>"
            "Freie Software unter der GNU General Public License, Version 3 - ohne Gewähr; "
            "keine Rechtsberatung.<br><br>"
            f"<b>Benutzer:</b> {self._context.user.name}<br>"
            f"<b>Datenbank:</b> {self._context.database.path}<br>"
            f"<b>Benutzerliste:</b> {config.config_file()}<br>"
            f"<b>Logdateien:</b> {config.log_dir()}"
        )
        box.exec()

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802 (Qt-Name)
        """Schließt beim Beenden die Datenbank sauber."""
        self._context.close()
        super().closeEvent(event)
