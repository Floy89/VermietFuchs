"""Fachliche Ausnahmen der Anwendung.

Alle eigenen Ausnahmen erben von :class:`AppError`. Die Oberfläche fängt genau
diese Basisklasse ab und zeigt die (deutsche) Meldung dem Benutzer an. Alles,
was *nicht* von ``AppError`` erbt, ist ein echter Programmfehler und wird
geloggt.
"""

from __future__ import annotations


class AppError(Exception):
    """Basisklasse aller erwarteten, dem Benutzer anzeigbaren Fehler."""


class ValidationError(AppError):
    """Eine Eingabe oder ein Domain-Objekt verletzt eine fachliche Regel."""


class NotFoundError(AppError):
    """Ein angeforderter Datensatz existiert nicht (mehr)."""


class IntegrityViolationError(AppError):
    """Die Datenbank hat eine Verknüpfungs- oder Eindeutigkeitsregel abgelehnt."""


class DatabaseError(AppError):
    """Technischer Fehler beim Zugriff auf die SQLite-Datei."""


class ConfigError(AppError):
    """Die ``config.json`` ist nicht lesbar, nicht schreibbar oder fehlerhaft."""


class ScanError(AppError):
    """Die optionale Belegerkennung ist nicht verfügbar oder hat nichts Brauchbares geliefert."""


class ExportError(AppError):
    """Ein Export (z. B. PDF) konnte nicht erstellt werden."""
