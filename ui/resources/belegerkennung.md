# KI-Belegerkennung einrichten (optional)

**Für die meisten Belege brauchen Sie das nicht.** Die Schaltfläche **Beleg auslesen …** im
Rechnungsdialog liest PDFs und - per Windows-Texterkennung - auch Fotos und Scans ohne KI
aus und lernt aus Ihren Korrekturen. Die KI-Belegerkennung kommt nur zum Zug, wenn dort
nichts Verwertbares erkannt wird, etwa bei ungewöhnlich aufgebauten oder schlecht
lesbaren Belegen.

Dafür wird ein KI-Modell benötigt, das **auf Ihrem eigenen Rechner** läuft. Es ist bewusst
nicht Teil der Anwendung: Es ist mehrere Gigabyte groß und nicht jeder braucht es. Ohne
diese Einrichtung funktioniert alles andere unverändert.

**Ihre Belege verlassen den Rechner nicht.** Das Modell arbeitet vollständig offline.

## Voraussetzungen

| | Empfohlen | Mindestens |
|---|---|---|
| Arbeitsspeicher | 16 GB | 8 GB (nur kleines Modell) |
| Grafikkarte | NVIDIA/AMD mit 8 GB | keine (dann langsam) |
| Freier Speicherplatz | 10 GB | 5 GB |

## Schritt 1: Ollama installieren

Ollama ist ein kostenloses Programm, das KI-Modelle lokal ausführt.

1. <https://ollama.com/download> öffnen und die Windows-Version herunterladen.
2. `OllamaSetup.exe` ausführen. Ollama startet danach automatisch im Hintergrund
   (Symbol im Infobereich neben der Uhr) und bei jedem Windows-Start erneut.

Benötigt wird Version 0.12.7 oder neuer.

## Schritt 2: Modell laden

Eingabeaufforderung oder PowerShell öffnen und **einen** der Befehle ausführen:

| Modell | Befehl | Download | Geeignet für |
|---|---|---|---|
| Qwen3-VL 8B | `ollama pull qwen3-vl:8b-instruct` | 6,1 GB | Grafikkarte mit 8 GB oder mehr |
| Qwen3-VL 4B (Voreinstellung) | `ollama pull qwen3-vl:4b-instruct` | 3,3 GB | Grafikkarte mit 4 GB |
| Qwen3-VL 2B | `ollama pull qwen3-vl:2b-instruct` | 1,9 GB | Rechner ohne Grafikkarte |

Der Download läuft einmalig. Mit `ollama list` sehen Sie, welche Modelle installiert sind.

**Wichtig: die Variante mit `-instruct` nehmen.** Der Kurzname ohne Zusatz (`qwen3-vl:8b`)
ist eine „Thinking"-Variante. Sie überlegt vor jeder Antwort minutenlang und liefert für
diese Aufgabe kein Ergebnis.

## Schritt 3: In der Anwendung verbinden

1. Oben rechts das Benutzer-Menü öffnen → **Belegerkennung …**
2. Die Adresse `http://localhost:11434` stehen lassen.
3. Bei **Modell** den Namen aus Schritt 2 eintragen, z. B. `qwen3-vl:4b-instruct`.
4. **Verbindung prüfen** anklicken. Erscheint „Bereit", auf **Speichern** klicken.

Ab jetzt nutzt **Beleg auslesen …** im Dialog „Neue Kostenposition" bei Bedarf die KI.

## Verwendung

1. Tab *Rechnungen* → **Neu** → **Beleg auslesen …**
2. PDF oder Foto auswählen. Das Auslesen dauert je nach Rechner zwischen wenigen Sekunden
   und einigen Minuten; beim ersten Beleg nach dem Start am längsten, weil das Modell
   erst geladen wird. Richtwert: Auf einem Laptop mit 4-GB-Grafikkarte brauchte das
   4B-Modell rund 20–30 Sekunden je Seite, das 8B-Modell 35–45 Sekunden.
3. **Die eingetragenen Werte prüfen**, bei Bedarf korrigieren, dann speichern.

Ausgewertet werden die ersten drei Seiten eines Belegs.

## Wichtige Hinweise

- **Immer kontrollieren.** Ein KI-Modell kann sich verlesen - etwa einen Teilbetrag statt des
  Gesamtbetrags nehmen. Die Anwendung trägt die Werte deshalb nur als Vorschlag ein und
  speichert nichts von selbst.
- **Gute Vorlagen helfen.** Original-PDFs werden zuverlässiger gelesen als Fotos. Fotos
  möglichst gerade, scharf und ohne Schatten aufnehmen.
- **Nur lokale Adressen sind privat.** Tragen Sie eine andere Adresse als `localhost` ein,
  werden die Belege an diesen Rechner übertragen.

## Wenn es nicht funktioniert

| Meldung | Lösung |
|---|---|
| „Ollama ist … nicht erreichbar" | Ollama starten (Startmenü → Ollama) oder Rechner neu starten |
| „Modell … ist nicht installiert" | Befehl aus Schritt 2 ausführen; Schreibweise des Namens prüfen |
| „… ist eine 'Thinking'-Variante" | Modell mit `-instruct` im Namen laden und eintragen (Schritt 2) |
| „Zeitüberschreitung" | Kleineres Modell wählen; andere speicherhungrige Programme schließen |
| Werte sind falsch oder leer | Bessere Vorlage verwenden oder größeres Modell wählen |

## Wieder entfernen

Modell löschen: `ollama rm qwen3-vl:4b-instruct` (bzw. der gewählte Name). Ollama selbst über *Windows-Einstellungen → Apps*
deinstallieren. Das Auslesen ohne KI funktioniert danach unverändert weiter.
