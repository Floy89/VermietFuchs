"""PDF-Export der Nebenkostenabrechnung (ReportLab).

Der Service bekommt ein fertig berechnetes
:class:`~models.billing.BillingResult` und setzt daraus Abrechnungsschreiben.
Er rechnet selbst nichts - die Zahlen im PDF sind exakt die der Oberfläche.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Sequence
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.enums import TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    Flowable,
    KeepTogether,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

import config
from models.billing import BillingResult, CostShare, TenantStatement
from models.exceptions import ExportError, ValidationError
from models.settings import LandlordSettings
from models.value_objects import Money
from utils.formatters import (
    format_date,
    format_money,
    format_period,
    format_quantity,
    format_share_basis,
    format_share_title,
)

logger = logging.getLogger(__name__)

# Nutzbare Breite: A4 (210 mm) minus Ränder (22 + 18 mm) minus je 6 pt
# Innenabstand des ReportLab-Rahmens - gerundet 165 mm. Alle Tabellen sind
# genau so breit und schließen damit bündig mit dem Fließtext ab.
_ACCENT = colors.HexColor("#2F6FED")
_LIGHT_GREY = colors.HexColor("#F1F3F7")
_BORDER = colors.HexColor("#C9CFD9")
_MUTED = colors.HexColor("#5F6B7A")


class PdfExportService:
    """Erzeugt Abrechnungsschreiben als PDF."""

    def __init__(self) -> None:
        base = getSampleStyleSheet()
        self._normal = ParagraphStyle("Text", parent=base["Normal"], fontName="Helvetica", fontSize=10, leading=14)
        self._small = ParagraphStyle("Klein", parent=self._normal, fontSize=8, leading=10, textColor=_MUTED)
        self._cell = ParagraphStyle("Zelle", parent=self._normal, fontSize=9, leading=11)
        self._cell_right = ParagraphStyle("ZelleRechts", parent=self._cell, alignment=TA_RIGHT)
        self._right = ParagraphStyle("Rechts", parent=self._normal, alignment=TA_RIGHT)
        self._title = ParagraphStyle(
            "Titel", parent=self._normal, fontName="Helvetica-Bold", fontSize=15, leading=19, spaceAfter=2
        )
        self._heading = ParagraphStyle(
            "Abschnitt", parent=self._normal, fontName="Helvetica-Bold", fontSize=11, leading=14,
            spaceBefore=10, spaceAfter=4, textColor=_ACCENT,
        )

    # ------------------------------------------------------------------ #
    # Öffentliche Schnittstelle
    # ------------------------------------------------------------------ #
    def export_statements(
        self,
        result: BillingResult,
        statements: Sequence[TenantStatement],
        landlord: LandlordSettings,
        target: Path,
        issue_date: date | None = None,
    ) -> Path:
        """Schreibt ein PDF mit einem Abrechnungsschreiben je Mieter.

        Args:
            result: Das Gesamtergebnis der Abrechnung.
            statements: Die auszugebenden Einzelabrechnungen (eine Seite+ je Mieter).
            landlord: Absender- und Bankdaten.
            target: Zieldatei (wird überschrieben).
            issue_date: Datum des Schreibens (Standard: heute).

        Returns:
            Den Pfad der geschriebenen Datei.

        Raises:
            ValidationError: Wenn keine Abrechnung ausgewählt wurde.
            ExportError: Wenn die Datei nicht geschrieben werden kann
                (z. B. weil sie noch in einem PDF-Betrachter geöffnet ist).
        """
        if not statements:
            raise ValidationError("Es wurde keine Abrechnung zum Export ausgewählt.")

        issue_date = issue_date or date.today()
        story: list[Flowable] = []
        for index, statement in enumerate(statements):
            if index > 0:
                story.append(PageBreak())
            story.extend(self._build_statement(result, statement, landlord, issue_date))

        document = SimpleDocTemplate(
            str(target),
            pagesize=A4,
            leftMargin=22 * mm,
            rightMargin=18 * mm,
            topMargin=18 * mm,
            bottomMargin=18 * mm,
            title=f"Nebenkostenabrechnung {format_period(result.period)}",
            author=landlord.name or config.APP_NAME,
        )
        try:
            document.build(story, onFirstPage=self._draw_footer, onLaterPages=self._draw_footer)
        except OSError as exc:
            raise ExportError(
                f"Die PDF-Datei '{target}' kann nicht geschrieben werden. "
                f"Ist sie vielleicht noch geöffnet? ({exc})"
            ) from exc
        logger.info("PDF erstellt: %s (%d Abrechnung(en))", target, len(statements))
        return target

    # ------------------------------------------------------------------ #
    # Aufbau eines Schreibens
    # ------------------------------------------------------------------ #
    def _build_statement(
        self,
        result: BillingResult,
        statement: TenantStatement,
        landlord: LandlordSettings,
        issue_date: date,
    ) -> list[Flowable]:
        """Stellt alle Elemente eines Abrechnungsschreibens zusammen."""
        tenant = statement.tenant
        story: list[Flowable] = []

        # --- Briefkopf: Absenderzeile, Anschrift, Datum ---------------------
        story.append(Paragraph(escape(landlord.address_line) or "&nbsp;", self._small))
        story.append(Spacer(1, 3 * mm))
        address_lines = [tenant.name, *self._split_address(result.property.address)]
        story.append(Paragraph("<br/>".join(escape(line) for line in address_lines), self._normal))
        story.append(Spacer(1, 4 * mm))
        place = landlord.city.split(" ", 1)[-1] if landlord.city else ""
        date_line = f"{place}, {format_date(issue_date)}" if place else format_date(issue_date)
        story.append(Paragraph(escape(date_line), self._right))
        story.append(Spacer(1, 4 * mm))

        # --- Titel und Einleitung --------------------------------------------
        story.append(Paragraph("Nebenkostenabrechnung", self._title))
        story.append(
            Paragraph(f"für den Zeitraum {format_period(result.period)}", self._normal)
        )
        story.append(Spacer(1, 5 * mm))
        story.append(Paragraph(f"Guten Tag {escape(tenant.name)},", self._normal))
        story.append(Spacer(1, 2 * mm))
        story.append(
            Paragraph(
                "nachfolgend erhalten Sie die Abrechnung der Betriebs- und Heizkosten "
                "für Ihre Wohnung.",
                self._normal,
            )
        )

        # --- Abrechnungsgrundlagen -------------------------------------------
        story.append(Paragraph("Abrechnungsgrundlagen", self._heading))
        story.append(self._facts_table(result, statement))

        # --- Kostenaufstellung -----------------------------------------------
        story.append(Paragraph("Kostenaufstellung", self._heading))
        story.append(self._cost_table(statement))

        # --- Ergebnis und Schlusstext ---------------------------------------
        # Überschrift, Ergebnistabelle und Schlusssatz nie durch einen
        # Seitenumbruch trennen.
        story.append(
            KeepTogether(
                [
                    Paragraph("Ergebnis", self._heading),
                    self._result_table(statement),
                    Spacer(1, 4 * mm),
                    Paragraph(self._closing_text(statement, landlord), self._normal),
                ]
            )
        )
        story.append(Spacer(1, 3 * mm))
        story.append(
            Paragraph(
                "Die der Abrechnung zugrunde liegenden Belege können Sie nach "
                "Terminabsprache einsehen. Einwendungen gegen die Abrechnung sind "
                "innerhalb von zwölf Monaten nach Zugang mitzuteilen (§ 556 Abs. 3 BGB).",
                self._small,
            )
        )
        story.append(
            KeepTogether(
                [
                    Spacer(1, 4 * mm),
                    Paragraph("Mit freundlichen Grüßen", self._normal),
                    Spacer(1, 6 * mm),
                    Paragraph(escape(landlord.name), self._normal),
                ]
            )
        )
        return story

    def _facts_table(self, result: BillingResult, statement: TenantStatement) -> Table:
        """Tabelle mit Objekt, Wohnung und Zeiträumen."""
        apartment = statement.apartment
        prop = result.property
        # Heißt die Immobilie wie ihre Straße, den Namen nicht doppelt nennen.
        object_text = prop.address if prop.name in prop.address else f"{prop.name}, {prop.address}"
        rows = [
            ("Objekt", object_text),
            ("Wohnung", apartment.number),
            (
                "Wohnfläche / Personen",
                f"{format_quantity(apartment.area_sqm)} m² / {apartment.describe_persons(statement.occupancy)}",
            ),
            ("Abrechnungszeitraum", f"{format_period(result.period)} ({result.period.days} Tage)"),
            ("Ihr Abrechnungszeitraum", f"{format_period(statement.occupancy)} ({statement.occupancy.days} Tage)"),
        ]
        data = [[Paragraph(escape(k), self._cell), Paragraph(escape(v), self._cell)] for k, v in rows]
        table = Table(data, colWidths=[46 * mm, 119 * mm], hAlign="LEFT")
        table.setStyle(
            TableStyle(
                [
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("TEXTCOLOR", (0, 0), (0, -1), _MUTED),
                    ("TOPPADDING", (0, 0), (-1, -1), 1.5),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 1.5),
                    ("LEFTPADDING", (0, 0), (0, -1), 0),
                ]
            )
        )
        return table

    def _cost_table(self, statement: TenantStatement) -> Table:
        """Tabelle aller Kostenanteile mit Berechnungsgrundlage."""
        header = ["Kostenart", "Gesamtkosten", "Verteilung", "Ihr Anteil"]
        data: list[list[Any]] = [[Paragraph(f"<b>{text}</b>", self._cell) for text in header]]
        data[0][1] = Paragraph("<b>Gesamtkosten</b>", self._cell_right)
        data[0][3] = Paragraph("<b>Ihr Anteil</b>", self._cell_right)

        for line in category_lines(statement.shares):
            data.append(
                [
                    Paragraph(escape(line.title), self._cell),
                    Paragraph(format_money(line.total), self._cell_right),
                    Paragraph(escape(line.basis), self._cell),
                    Paragraph(format_money(line.amount), self._cell_right),
                ]
            )
        if not statement.shares:
            data.append([Paragraph("Keine Kostenpositionen im Abrechnungszeitraum.", self._cell), "", "", ""])

        data.append(
            [
                Paragraph("<b>Summe Ihrer Kostenanteile</b>", self._cell),
                "",
                "",
                Paragraph(f"<b>{format_money(statement.total_costs)}</b>", self._cell_right),
            ]
        )

        table = Table(data, colWidths=[58 * mm, 28 * mm, 53 * mm, 26 * mm], repeatRows=1, hAlign="LEFT")
        last = len(data) - 1
        table.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, 0), _LIGHT_GREY),
                    ("LINEBELOW", (0, 0), (-1, 0), 0.8, _BORDER),
                    ("LINEBELOW", (0, 1), (-1, last - 1), 0.3, _BORDER),
                    ("LINEABOVE", (0, last), (-1, last), 0.8, colors.black),
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("TOPPADDING", (0, 0), (-1, -1), 3),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
                ]
            )
        )
        return table

    def _result_table(self, statement: TenantStatement) -> Table:
        """Tabelle: Kosten, Vorauszahlungen und Saldo."""
        balance = statement.balance
        if statement.is_credit:
            label, colour = "Guthaben zu Ihren Gunsten", colors.HexColor("#1E7F55")
        elif balance.is_zero:
            label, colour = "Ausgeglichen", colors.black
        else:
            label, colour = "Nachzahlung", colors.HexColor("#B83232")

        bold_right = ParagraphStyle("Saldo", parent=self._right, fontName="Helvetica-Bold", textColor=colour)
        bold_left = ParagraphStyle("SaldoText", parent=self._normal, fontName="Helvetica-Bold", textColor=colour)
        data = [
            [Paragraph("Summe Ihrer Kostenanteile", self._normal), Paragraph(format_money(statement.total_costs), self._right)],
            [Paragraph(self._prepayment_label(statement), self._normal), Paragraph(format_money(statement.prepayments), self._right)],
            [Paragraph(label, bold_left), Paragraph(format_money(abs(balance)), bold_right)],
        ]
        table = Table(data, colWidths=[127 * mm, 38 * mm], hAlign="LEFT")
        table.setStyle(
            TableStyle(
                [
                    ("LINEABOVE", (0, 2), (-1, 2), 0.8, colors.black),
                    ("BACKGROUND", (0, 2), (-1, 2), _LIGHT_GREY),
                    ("TOPPADDING", (0, 0), (-1, -1), 3),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
                ]
            )
        )
        return table

    # ------------------------------------------------------------------ #
    # Texte
    # ------------------------------------------------------------------ #
    @staticmethod
    def _prepayment_label(statement: TenantStatement) -> str:
        """Beschriftung der Vorauszahlungszeile mit den zugrunde liegenden Monatsraten.

        Beispiele: "(300,00 € monatlich)" oder nach einer Erhöhung
        "(215,00 € ab 01.01.2025, 230,00 € ab 01.07.2025)".
        """
        rates = statement.prepayment_rates
        if not rates:
            return "abzüglich Vorauszahlungen"
        if len(rates) == 1:
            detail = f"{format_money(rates[0].amount)} monatlich"
        else:
            detail = ", ".join(
                f"{format_money(rate.amount)} ab {format_date(max(rate.valid_from, statement.occupancy.start))}"
                for rate in rates
            )
        return f"abzüglich Vorauszahlungen ({detail})"

    @staticmethod
    def _closing_text(statement: TenantStatement, landlord: LandlordSettings) -> str:
        """Schlusssatz je nach Ergebnis: Nachzahlung, Guthaben oder ausgeglichen."""
        amount = f"<b>{format_money(abs(statement.balance))}</b>"
        if statement.is_credit:
            return (
                f"Es ergibt sich ein Guthaben von {amount}. Der Betrag wird Ihnen in den "
                "nächsten Tagen erstattet bzw. mit der nächsten Mietzahlung verrechnet."
            )
        if statement.balance.is_zero:
            return "Ihre Vorauszahlungen decken die angefallenen Kosten genau. Es ist nichts weiter zu veranlassen."

        text = (
            f"Es ergibt sich eine Nachzahlung von {amount}. Bitte überweisen Sie den Betrag "
            f"innerhalb von {landlord.payment_term_days} Tagen"
        )
        if landlord.iban:
            bank = f" ({escape(landlord.bank_name)})" if landlord.bank_name else ""
            text += f" auf das Konto IBAN {escape(landlord.iban)}{bank}"
        return text + "."

    @staticmethod
    def _split_address(address: str) -> list[str]:
        """Zerlegt eine Adresse an Kommas/Zeilenumbrüchen in einzelne Zeilen."""
        parts = address.replace("\n", ",").split(",")
        return [part.strip() for part in parts if part.strip()]

    @staticmethod
    def _draw_footer(canvas: Any, document: Any) -> None:
        """Zeichnet die Fußzeile mit Seitenzahl auf jede Seite."""
        canvas.saveState()
        canvas.setFont("Helvetica", 7.5)
        canvas.setFillColor(_MUTED)
        canvas.drawString(22 * mm, 10 * mm, f"Erstellt mit {config.APP_NAME} {config.APP_VERSION}")
        canvas.drawRightString(A4[0] - 18 * mm, 10 * mm, f"Seite {document.page}")
        canvas.restoreState()


@dataclass(frozen=True)
class CategoryLine:
    """Eine Zeile der Kostenaufstellung im Schreiben: Kategorie statt einzelner Rechnung."""

    title: str
    total: Money
    basis: str
    amount: Money


def category_lines(shares: Sequence[CostShare]) -> list[CategoryLine]:
    """Die Kostenanteile eines Mieters als Zeilen je Kategorie (Kostenart), ohne Lieferant.

    Anteile derselben Kostenart und desselben Bestandteils, die gleich verteilt
    werden (gleiche Bezugsgrößen, ganzer Zeitraum bewohnt), werden zu einer Zeile
    zusammengefasst - z. B. zwei Stromrechnungen, die beide ins Jahr fallen.
    Andere Anteile bleiben eigene Zeilen, damit die Rechnung nachvollziehbar bleibt.
    """
    lines: list[CategoryLine] = []
    merged: dict[tuple[object, ...], int] = {}
    for share in shares:
        title = share.cost.cost_type.label
        if share.component_amount != share.cost.amount:
            title += f" ({share.component})"
        basis = format_share_basis(share)
        key: tuple[object, ...] | None = None
        if share.days == share.period_days and share.quantity is None and not share.is_difference:
            key = (title, basis)
        if key is not None and key in merged:
            index = merged[key]
            line = lines[index]
            lines[index] = CategoryLine(title, line.total + share.component_amount, basis, line.amount + share.amount)
            continue
        if key is not None:
            merged[key] = len(lines)
        lines.append(CategoryLine(title, share.component_amount, basis, share.amount))
    return lines

