"""Tab 8 - Entwicklung: Verlauf von Nebenkosten und Vorauszahlungen über mehrere Jahre, mit Prognose."""

from __future__ import annotations

from datetime import date

from PyQt6.QtCore import QPointF, QRectF, Qt
from PyQt6.QtGui import QColor, QFont, QPainter, QPaintEvent, QPen, QPolygonF
from PyQt6.QtWidgets import QComboBox, QHBoxLayout, QLabel, QSizePolicy, QSpinBox, QSplitter, QWidget

from app_context import AppContext
from models.trend import CostTrend, YearPoint
from models.value_objects import Money
from ui import styles
from ui.helpers import ErrorGuard, fill_combo
from ui.widgets.common import BaseTab, Card, Column, ObjectTable, muted_label, page_title
from utils.formatters import format_money

#: So viele Jahre zeigt der Tab beim ersten Öffnen höchstens.
_DEFAULT_SPAN = 6


def _whole_euro(money: Money) -> str:
    """Betrag ohne Cent, z. B. "1.250 €" (für Achsen und Fließtext)."""
    return format_money(Money(round(money.cents / 100) * 100)).replace(",00", "")


def describe_trend(trend: CostTrend) -> str:
    """Fasst Verlauf und Prognose in Worten zusammen - samt der Aussage, wann eine Anhebung nötig wird."""
    if not any(not point.costs.is_zero for point in trend.points):
        return "Für diesen Zeitraum sind keine umgelegten Kosten vorhanden."

    parts: list[str] = []
    incomplete = [str(point.year) for point in trend.points if not point.complete and not point.costs.is_zero]
    if not trend.forecast or trend.yearly_change is None:
        parts.append("Für eine Prognose werden mindestens zwei vollständige Jahre mit Kosten gebraucht.")
    else:
        change = trend.yearly_change
        direction = "steigen" if change.cents > 0 else "sinken" if change.cents < 0 else "bleiben"
        if change.is_zero:
            parts.append("Die Kosten bleiben im Mittel gleich.")
        else:
            parts.append(f"Die Kosten {direction} im Mittel um {_whole_euro(abs(change))} je Jahr.")

        monthly = trend.current_monthly_prepayment
        last = trend.trend_points[-1]
        horizon = trend.forecast[-1].year
        if monthly is None:
            parts.append("Es gibt keinen aktiven Mieter mit Vorauszahlung, mit der sich vergleichen ließe.")
        elif trend.already_exceeded:
            parts.append(
                f"Die heutige Vorauszahlung ({_whole_euro(monthly)} je Monat) liegt schon unter den Kosten von "
                f"{last.year} ({_whole_euro(last.monthly_costs)} je Monat) – eine Anhebung auf etwa "
                f"{_whole_euro(trend.recommended_prepayment(last))} je Monat ist jetzt angezeigt."
            )
        elif trend.crossing is not None:
            crossing = trend.crossing
            parts.append(
                f"Bei gleichbleibender Vorauszahlung ({_whole_euro(monthly)} je Monat) übersteigen die Kosten sie "
                f"voraussichtlich {crossing.year} – dann wären etwa "
                f"{_whole_euro(trend.recommended_prepayment(crossing))} je Monat nötig."
            )
        else:
            parts.append(
                f"Die heutige Vorauszahlung ({_whole_euro(monthly)} je Monat) reicht nach dieser Prognose "
                f"bis mindestens {horizon}."
            )
    if incomplete:
        parts.append(
            f"Nicht in die Prognose eingerechnet (laut Checkliste fehlen Rechnungen): {', '.join(incomplete)}."
        )
    return " ".join(parts)


class TrendChart(QWidget):
    """Liniendiagramm: Kosten und Vorauszahlungen je Jahr, Prognose gestrichelt.

    Bewusst selbst gezeichnet (``QPainter``) statt mit einer Diagramm-
    Bibliothek: Das spart ein Zusatzpaket samt dessen Verpackung in die
    ``.exe``, und die Farben folgen automatisch dem hellen bzw. dunklen Design.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._trend = CostTrend()
        self.setMinimumHeight(260)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

    def set_trend(self, trend: CostTrend) -> None:
        """Zeigt einen neuen Verlauf an."""
        self._trend = trend
        self.update()

    # ------------------------------------------------------------------ #
    # Zeichnen
    # ------------------------------------------------------------------ #
    def paintEvent(self, event: QPaintEvent | None) -> None:  # noqa: N802 (Qt-Name)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        trend = self._trend
        text_color = QColor(styles.color("muted"))
        if not trend.points:
            painter.setPen(text_color)
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "Keine Daten für diesen Zeitraum")
            painter.end()
            return

        cost_color = QColor(styles.color("primary"))
        prepayment_color = QColor(styles.color("success"))
        years = [point.year for point in trend.points] + [point.year for point in trend.forecast]
        future_prepayment = trend.current_yearly_prepayment
        values = [p.costs.cents for p in trend.points] + [p.prepayments.cents for p in trend.points]
        values += [p.costs.cents for p in trend.forecast]
        if future_prepayment is not None:
            values.append(future_prepayment.cents)
        top_value = self._nice_ceiling(max(values) or 100)

        plot = QRectF(84, 40, max(10, self.width() - 84 - 28), max(10, self.height() - 40 - 36))

        def x_of(year: int) -> float:
            if len(years) == 1:
                return plot.center().x()
            return plot.left() + plot.width() * (year - years[0]) / (years[-1] - years[0])

        def y_of(cents: int) -> float:
            return plot.bottom() - plot.height() * cents / top_value

        # --- Gitter und Achsenbeschriftung ---------------------------------
        font = QFont(self.font())
        font.setPointSizeF(max(7.5, font.pointSizeF() - 1))
        painter.setFont(font)
        grid_pen = QPen(QColor(styles.color("border")), 1)
        for step in range(5):
            cents = top_value * step // 4
            y = y_of(cents)
            painter.setPen(grid_pen)
            painter.drawLine(QPointF(plot.left(), y), QPointF(plot.right(), y))
            painter.setPen(text_color)
            painter.drawText(
                QRectF(0, y - 10, plot.left() - 10, 20),
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                _whole_euro(Money(cents)),
            )
        label_step = max(1, len(years) // 12)
        for index, year in enumerate(years):
            if index % label_step == 0:
                painter.setPen(text_color)
                painter.drawText(
                    QRectF(x_of(year) - 30, plot.bottom() + 8, 60, 20), Qt.AlignmentFlag.AlignCenter, str(year)
                )

        # --- Prognosebereich leicht hinterlegen ------------------------------
        last_actual = trend.points[-1].year
        if trend.forecast:
            shade = QColor(styles.color("hover"))
            shade.setAlpha(140)
            painter.fillRect(QRectF(x_of(last_actual), plot.top(), plot.right() - x_of(last_actual), plot.height()), shade)
            painter.setPen(text_color)
            painter.drawText(
                QRectF(x_of(last_actual) + 8, plot.top() + 4, 160, 18), Qt.AlignmentFlag.AlignLeft, "Prognose"
            )

        # --- Vorauszahlungen --------------------------------------------------
        self._draw_line(painter, [(x_of(p.year), y_of(p.prepayments.cents)) for p in trend.points], prepayment_color)
        for point in trend.points:
            self._draw_dot(painter, x_of(point.year), y_of(point.prepayments.cents), prepayment_color, filled=True)
        if future_prepayment is not None and trend.forecast:
            y = y_of(future_prepayment.cents)
            self._draw_line(
                painter, [(x_of(last_actual), y), (x_of(trend.forecast[-1].year), y)], prepayment_color, dashed=True
            )

        # --- Kosten -----------------------------------------------------------
        self._draw_line(painter, [(x_of(p.year), y_of(p.costs.cents)) for p in trend.points], cost_color)
        for point in trend.points:
            # Unvollständige Jahre als hohler Punkt: Der Wert ist vorläufig.
            self._draw_dot(painter, x_of(point.year), y_of(point.costs.cents), cost_color, filled=point.complete)
        if trend.forecast and trend.trend_points:
            anchor = trend.trend_points[-1]
            forecast_line = [(x_of(anchor.year), y_of(anchor.costs.cents))]
            forecast_line += [(x_of(p.year), y_of(p.costs.cents)) for p in trend.forecast]
            self._draw_line(painter, forecast_line, cost_color, dashed=True)

        # --- Schnittpunkt: ab hier reichen die Vorauszahlungen nicht mehr --------
        crossing = trend.crossing
        if crossing is not None:
            danger = QColor(styles.color("danger"))
            x = x_of(crossing.year)
            pen = QPen(danger, 1.5, Qt.PenStyle.DotLine)
            painter.setPen(pen)
            painter.drawLine(QPointF(x, plot.top()), QPointF(x, plot.bottom()))
            self._draw_dot(painter, x, y_of(crossing.costs.cents), danger, filled=True)
            painter.setPen(danger)
            painter.drawText(
                QRectF(x - 150, plot.top() + 4, 144, 18), Qt.AlignmentFlag.AlignRight, f"Anhebung nötig ab {crossing.year}"
            )

        self._draw_legend(painter, cost_color, prepayment_color, text_color)
        painter.end()

    @staticmethod
    def _nice_ceiling(cents: int) -> int:
        """Rundet den größten Wert auf eine "glatte" Obergrenze der Achse (durch 4 teilbar)."""
        euros = max(1, -(-cents // 100))
        magnitude = 10 ** max(0, len(str(euros)) - 2)
        step = magnitude * 4
        return (-(-int(euros * 1.08) // step)) * step * 100

    @staticmethod
    def _draw_line(painter: QPainter, points: list[tuple[float, float]], color: QColor, dashed: bool = False) -> None:
        if len(points) < 2:
            return
        painter.setPen(QPen(color, 2.2, Qt.PenStyle.DashLine if dashed else Qt.PenStyle.SolidLine))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawPolyline(QPolygonF([QPointF(x, y) for x, y in points]))

    def _draw_dot(self, painter: QPainter, x: float, y: float, color: QColor, filled: bool) -> None:
        painter.setPen(QPen(color, 2))
        painter.setBrush(color if filled else QColor(styles.color("surface")))
        painter.drawEllipse(QPointF(x, y), 4.2, 4.2)

    def _draw_legend(self, painter: QPainter, cost_color: QColor, prepayment_color: QColor, text_color: QColor) -> None:
        """Legende oben links über dem Diagramm."""
        x = 84.0
        for color, label, dashed in (
            (cost_color, "Kosten der Mieter", False),
            (prepayment_color, "Vorauszahlungen", False),
            (cost_color, "Prognose Kosten", True),
            (prepayment_color, "heutige Vorauszahlung", True),
        ):
            painter.setPen(QPen(color, 2.2, Qt.PenStyle.DashLine if dashed else Qt.PenStyle.SolidLine))
            painter.drawLine(QPointF(x, 16), QPointF(x + 24, 16))
            painter.setPen(text_color)
            width = painter.fontMetrics().horizontalAdvance(label)
            painter.drawText(QRectF(x + 30, 6, width + 4, 20), Qt.AlignmentFlag.AlignVCenter, label)
            x += 30 + width + 22


class TrendTab(BaseTab):
    """Zeigt, wie sich Kosten und Vorauszahlungen entwickeln - und wann eine Anhebung nötig wird."""

    def __init__(self, context: AppContext, parent: QWidget | None = None) -> None:
        super().__init__(context, parent)
        self._filling = False
        #: Der Zeitraum wird nur beim ersten Anzeigen vorgeschlagen, danach gilt die Wahl des Benutzers.
        self._period_suggested = False
        self.root.addWidget(page_title("Entwicklung der Nebenkosten"))

        current_year = date.today().year
        self._property_combo = QComboBox()
        self._property_combo.setMinimumWidth(240)
        self._scope_combo = QComboBox()
        self._scope_combo.setMinimumWidth(220)
        self._from_spin = QSpinBox()
        self._to_spin = QSpinBox()
        for spin in (self._from_spin, self._to_spin):
            spin.setRange(1990, 2100)
        self._from_spin.setValue(current_year - _DEFAULT_SPAN)
        self._to_spin.setValue(current_year - 1)
        self._forecast_spin = QSpinBox()
        self._forecast_spin.setRange(0, 15)
        self._forecast_spin.setValue(5)
        self._forecast_spin.setSuffix(" Jahre")
        self._forecast_spin.setSpecialValueText("keine")

        bar = QHBoxLayout()
        bar.setSpacing(8)
        for caption, widget in (
            ("Immobilie", self._property_combo),
            ("Bereich", self._scope_combo),
            ("von", self._from_spin),
            ("bis", self._to_spin),
            ("Prognose", self._forecast_spin),
        ):
            bar.addWidget(muted_label(caption))
            bar.addWidget(widget)
        bar.addStretch(1)
        self.root.addLayout(bar)

        chart_card = Card("Kosten und Vorauszahlungen je Jahr")
        self._summary = QLabel()
        self._summary.setWordWrap(True)
        self._summary.setObjectName("SectionTitle")
        self._chart = TrendChart()
        chart_card.body.addWidget(self._summary)
        chart_card.body.addWidget(self._chart, 1)
        chart_card.body.addWidget(
            muted_label(
                "Kosten = auf die Mieter umgelegte Kosten des Jahres laut Abrechnung. Die Prognose verlängert den "
                "linearen Trend der vollständigen Jahre; hohle Punkte sind Jahre, für die laut Checkliste noch "
                "Rechnungen fehlen. Gestrichelt grün: die heute vereinbarte Vorauszahlung, aufs Jahr gerechnet."
            )
        )

        table_card = Card("Werte")
        self._table: ObjectTable[YearPoint] = ObjectTable(
            [
                Column("Jahr", lambda p: str(p.year), lambda p: p.year),
                Column("Kosten", lambda p: format_money(p.costs), lambda p: p.costs.cents, True),
                Column("je Monat", lambda p: format_money(p.monthly_costs), lambda p: p.monthly_costs.cents, True),
                Column("Vorauszahlungen", lambda p: format_money(p.prepayments), lambda p: p.prepayments.cents, True),
                Column(
                    "Ergebnis der Mieter",
                    lambda p: ("Guthaben " if p.balance.cents > 0 else "Nachzahlung " if p.balance.cents < 0 else "")
                    + format_money(abs(p.balance)),
                    lambda p: p.balance.cents,
                    True,
                    lambda p: None if p.balance.is_zero else "success" if p.balance.cents > 0 else "danger",
                ),
                Column(
                    "Rechnungen",
                    lambda p: "vollständig" if p.complete else "unvollständig",
                    color=lambda p: None if p.complete else "warning_text",
                ),
            ],
            stretch_column=5,
        )
        table_card.body.addWidget(self._table)

        splitter = QSplitter(Qt.Orientation.Vertical)
        splitter.setChildrenCollapsible(False)
        splitter.addWidget(chart_card)
        splitter.addWidget(table_card)
        splitter.setSizes([520, 240])
        self.root.addWidget(splitter, 1)

        self._property_combo.currentIndexChanged.connect(lambda *_: self._on_property_changed())
        self._scope_combo.currentIndexChanged.connect(lambda *_: self._reload())
        for spin in (self._from_spin, self._to_spin, self._forecast_spin):
            spin.valueChanged.connect(lambda *_: self._reload())

    # ------------------------------------------------------------------ #
    # Daten laden
    # ------------------------------------------------------------------ #
    def refresh(self) -> None:
        """Lädt Immobilien und Wohnungen neu und berechnet den Verlauf."""
        with ErrorGuard(self):
            properties = self._context.properties.list_properties()
            fill_combo(self._property_combo, [(prop.name, prop.id) for prop in properties])
        self._on_property_changed()

    def _property_id(self) -> int | None:
        data = self._property_combo.currentData()
        return int(data) if data is not None else None

    def _on_property_changed(self) -> None:
        """Bietet die Wohnungen der Immobilie an und schlägt beim ersten Mal den Zeitraum vor."""
        property_id = self._property_id()
        self._filling = True
        choices: list[tuple[str, int | None]] = [("Gesamtes Haus (alle Mieter)", None)]
        if property_id is not None:
            with ErrorGuard(self):
                choices += [
                    (f"Wohnung {apartment.number}", apartment.id)
                    for apartment in self._context.apartments.list_apartments(property_id)
                ]
                years = self._context.trends.years_with_invoices(property_id)
                if years and not self._period_suggested:
                    # Beim ersten Anzeigen: von der ältesten Rechnung bis zum letzten abgelaufenen Jahr.
                    last = min(max(years), date.today().year - 1)
                    self._to_spin.setValue(max(last, min(years)))
                    self._from_spin.setValue(max(min(years), self._to_spin.value() - _DEFAULT_SPAN + 1))
                    self._period_suggested = True
        fill_combo(self._scope_combo, choices)
        self._filling = False
        self._reload()

    def _reload(self) -> None:
        """Berechnet Verlauf und Prognose für die aktuelle Auswahl."""
        if self._filling:
            return
        property_id = self._property_id()
        trend = CostTrend()
        if property_id is not None:
            with ErrorGuard(self):
                trend = self._context.trends.build(
                    property_id,
                    self._from_spin.value(),
                    self._to_spin.value(),
                    self._scope_combo.currentData(),
                    self._forecast_spin.value(),
                )
        self._chart.set_trend(trend)
        self._table.set_items(trend.points)
        self._summary.setText(
            describe_trend(trend) if property_id is not None else "Bitte zuerst eine Immobilie anlegen."
        )
