# Änderungen

Alle wichtigen Änderungen an VermietFuchs, neueste zuerst. Die Versionsnummern folgen
[Semantic Versioning](https://semver.org/lang/de/): neue Funktionen erhöhen die mittlere Zahl,
reine Fehlerbehebungen die letzte.

Ein Update übernimmt alle Daten: Beim ersten Start einer neuen Version wird die Datenbank
automatisch angepasst; vorher legt das Programm eine Sicherungskopie daneben an.

## [1.2.0] - 2026-10-21

### Neu

- **Wasserregel** für Wasserrechnungen (Rechnungsdialog → „Verbrauchsdifferenz“ →
  „Wasserregel …“). Die Wasserrechnungen des Abrechnungszeitraums werden gemeinsam aufgeteilt:
  Grundgebühr (aus dem Grundpreis der Splits) nach Wohnfläche - oder, wenn im Mietvertrag so
  vereinbart, mit „Wasserregel, Grundgebühr je Wohnung“ zu gleichen Teilen je Wohnung -, der Mengenpreis
  für das Wasser der Wohnungszähler nach Verbrauch, das Kaltwasser für die Warmwasserbereitung
  (Warmwasserzähler, z. B. Ablesewerte der ISTA-Abrechnung) als eigene Position
  „Warmwasser – Kaltwasseranteil“ nach Warmwasserverbrauch, die Restmenge (Heizungsnachspeisung,
  Zapfstellen, Verluste) nach Wohnfläche. Fehlen Zählerstände am Anfang oder Ende des Zeitraums,
  gibt es einen Hinweis und die bisherige Verteilung. Zählerstände mit „Platzhalter“ in der
  Bemerkung werden in der Abrechnung gemeldet.
- **Heizkostenrechnung mit Kaltwasser:** Neuer Haken „Enthält das Kaltwasser für die
  Warmwasserbereitung“ bei Heiz- und Warmwasserrechnungen (z. B. der Messdienst-Abrechnung der
  verbundenen Heizungs- und Warmwasserversorgung). Ist er gesetzt und deckt die Rechnung den
  Zeitraum ab, entfällt die eigene Position „Warmwasser – Kaltwasseranteil“ (ein Hinweis nennt
  Menge und Betrag). Die Datenbank wird dafür auf Version 13 angepasst.
- **Nicht umlegbare Kosten:** Neuer Umlageschlüssel „Nicht umlegbar (trägt Eigentümer)“ für alle
  Kostenarten, z. B. für die Miete der Rauchwarnmelder. Solche Rechnungen erscheinen in keiner
  Mieterabrechnung, zählen aber in den Gesamtkosten; die Abrechnung weist sie als „nicht umlegbar“ aus.

### Geändert

- **Abrechnungsschreiben (PDF):** Die Kostenaufstellung nennt nur noch die Kostenart, nicht mehr
  Lieferant und Rechnung. Rechnungen derselben Kostenart, die gleich verteilt werden, stehen in
  einer Zeile. Die Übersicht in der App zeigt weiterhin jede Rechnung einzeln.
- **Reihenfolge der Positionen** in Abrechnung und PDF nach dem Katalog der Kostenarten
  (§ 2 BetrKV), innerhalb einer Kostenart nach Datum - statt nach Rechnungsbeginn.
- **CO2-Kosten je Jahr:** Bei Rechnungen über einen Jahreswechsel ohne CO2-Angabe je Zeitraum,
  aber mit Emissionsfaktor, wird der CO2-Ausstoß eines Jahres aus seinen kWh berechnet und mit
  dem CO2-Preis dieses Jahres bewertet (2024: 45 €/t, 2025: 55 €/t, 2026: 60 €/t, zzgl. 19 %
  USt) - statt die Summe mit Mischpreis nach kWh aufzuteilen.

## [1.1.0] - 2026-10-15

### Neu

- **Belege ohne KI auslesen.** „Beleg auslesen …“ im Rechnungsdialog liest Absender,
  Rechnungsdatum, Zeitraum, Betrag, Verbrauch, Kostenart, Preise, Umsatzsteuer und
  Zwischenablesungen und schlägt daraus passende Splits vor - aus dem Text von PDFs und, für
  Fotos und Scans, mit der Texterkennung von Windows. Die optionale KI wird nur noch gefragt,
  wenn beides nichts ergibt.
- **Lernen aus Korrekturen.** Je Absender merkt sich das Programm beim Speichern, wo Betrag,
  Datum, Zeitraum, Verbrauch und CO2-Werte im Beleg standen und welche Kostenart, Beschreibung
  und welchen Schlüssel Sie gewählt haben. Benutzer-Menü → **Gelernte Absender …** zeigt das
  Gelernte und lässt es ändern oder vergessen.
- **CO2-Kosten und CO2-Ausstoß** je Brennstoff- bzw. Heizungsrechnung (CO2-Kostenaufteilungsgesetz),
  auch je Split. Die aggregierten Rechnungen zeigen die Jahressummen für die Meldung an den
  Messdienst.
- **Split-Dialog:**
  - passt sich der Kostenart an: Volumen, Zustandszahl und Brennwert nur bei kWh (Gas),
  - Haken **Grundpreis je 365 Tage (auch im Schaltjahr)** für Versorger, die den Grundpreis
    starr je Tag rechnen,
  - berechnete Beträge lassen sich **von Hand korrigieren** (farbig markiert, wird gespeichert);
    **Berechnung wiederherstellen** setzt sie zurück,
  - **Differenz als Eintrag** erfasst einen Restbetrag (meist Rundungscent), der linear über den
    ganzen Rechnungszeitraum verteilt wird,
  - der Umsatzsteuersatz wird aus der letzten Rechnung derselben Kostenart vorgeschlagen.
- **Fehlende Zählerstände zu einem Split:** Statt nach Wohnfläche wird nach dem Verbrauch
  zwischen den nächsten Ablesungen davor und danach verteilt - wie die angrenzenden Splits.

### Geändert

- Arbeits- und Grundpreisanteil eines Splits werden wie auf Versorgerrechnungen je auf Cent
  gerundet, bevor die Umsatzsteuer dazukommt. Berechnete Split-Beträge können sich dadurch um
  einen Cent verschieben; die Summe bleibt der Rechnungsbetrag.
- Eine Korrektur in einer Zeile verschiebt keine Cent mehr in andere Splits.
- Die Schaltfläche „Beleg scannen …“ heißt jetzt „Beleg auslesen …“ und ist immer da.

### Behoben

- Zahlen- und Datumsfelder übernehmen Eingaben zuverlässig: „12,5“ wurde teils zu 12, „1.9829“
  zu 19829, „31.12.2025“ zu 01.12.2025. Unlesbare Eingaben werden rot markiert und beim Verlassen
  des Feldes durch den letzten gültigen Wert ersetzt.
- Splits einer Wasserrechnung wichen ab, wenn der Grundpreis in einem Schaltjahr starr mit
  1/365 je Tag berechnet war (13 Cent im Beispiel).

### Datenbank

- Schema 11: Tabelle `document_profiles` (Gelerntes je Absender).
- Schema 12: `costs.co2_cost`, `co2_emission`, `co2_factor`.

## [1.0.0] - 2026-10-09

Erste veröffentlichte Version.

- Immobilien, Wohnungen, Mieter mit Vorauszahlungs-Historie, Zählerstände mit Fotos.
- Rechnungen mit echtem Rechnungszeitraum, Splits, Sammelrechnungen, Belege (PDF/Foto) an der
  Rechnung, Rechnungs-Checkliste je Jahr.
- Umlage nach Wohnfläche, Personen, Wohneinheiten, Verbrauch, Verbrauch + Wohnfläche
  (HeizkostenV) und direkter Zuordnung; abweichende Schlüssel je Mieter; Verbrauchsdifferenz
  zum Hauptzähler.
- Abrechnung je Mietverhältnis mit Abrechnungsschreiben als PDF, Empfehlung für die
  Vorauszahlung, Entwicklung und Prognose der Kosten.
- Optionale Belegerkennung über ein lokal laufendes KI-Modell (Ollama).
- Mehrere Benutzer mit eigener Datenbank, Änderungsprotokoll, helles und dunkles Design.

[1.2.0]: https://github.com/Floy89/VermietFuchs/compare/v1.1.0...v1.2.0
[1.1.0]: https://github.com/Floy89/VermietFuchs/compare/v1.0.0...v1.1.0
[1.0.0]: https://github.com/Floy89/VermietFuchs/releases/tag/v1.0.0
