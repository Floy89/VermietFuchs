# Änderungen

Alle wichtigen Änderungen an VermietFuchs, neueste zuerst. Die Versionsnummern folgen
[Semantic Versioning](https://semver.org/lang/de/): neue Funktionen erhöhen die mittlere Zahl,
reine Fehlerbehebungen die letzte.

Ein Update übernimmt alle Daten: Beim ersten Start einer neuen Version wird die Datenbank
automatisch angepasst; vorher legt das Programm eine Sicherungskopie daneben an.

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

[1.1.0]: https://github.com/Floy89/VermietFuchs/compare/v1.0.0...v1.1.0
[1.0.0]: https://github.com/Floy89/VermietFuchs/releases/tag/v1.0.0
