"""Infrastructure Layer: Repositories kapseln sämtliche SQL-Zugriffe.

Die Services kennen nur die Protokolle aus :mod:`repositories.interfaces`.
Die konkreten SQLite-Klassen werden an genau einer Stelle zusammengesteckt:
in :mod:`app_context`.
"""
