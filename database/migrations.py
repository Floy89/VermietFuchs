"""Schema-Migrationen.

Die aktuelle Schemaversion einer Datenbank steht in ``PRAGMA user_version``.
Beim Öffnen werden alle Migrationen mit höherer Nummer der Reihe nach
ausgeführt. Eine neue Schemaänderung bedeutet also nur: einen weiteren
Eintrag an :data:`MIGRATIONS` anhängen - bestehende Benutzer-Datenbanken
werden beim nächsten Start automatisch angehoben.
"""

from __future__ import annotations

import logging
import shutil
import sqlite3
from pathlib import Path
from typing import Final

from database.schema import SCHEMA_V1
from database.schema_v2 import SCHEMA_V2
from database.schema_v3 import SCHEMA_V3
from database.schema_v4 import SCHEMA_V4
from database.schema_v5 import SCHEMA_V5
from database.schema_v6 import SCHEMA_V6
from database.schema_v7 import SCHEMA_V7
from database.schema_v8 import SCHEMA_V8
from database.schema_v9 import SCHEMA_V9
from database.schema_v10 import SCHEMA_V10
from database.schema_v11 import SCHEMA_V11
from models.exceptions import DatabaseError

logger = logging.getLogger(__name__)

#: Liste aller Migrationen als (Zielversion, SQL-Skript), aufsteigend sortiert.
MIGRATIONS: Final[list[tuple[int, str]]] = [
    (1, SCHEMA_V1),
    (2, SCHEMA_V2),
    (3, SCHEMA_V3),
    (4, SCHEMA_V4),
    (5, SCHEMA_V5),
    (6, SCHEMA_V6),
    (7, SCHEMA_V7),
    (8, SCHEMA_V8),
    (9, SCHEMA_V9),
    (10, SCHEMA_V10),
    (11, SCHEMA_V11),
]

#: Höchste Schemaversion, die diese Programmversion kennt.
LATEST_VERSION: Final[int] = MIGRATIONS[-1][0]


def current_version(connection: sqlite3.Connection) -> int:
    """Liest die Schemaversion der geöffneten Datenbank (0 = leere Datei)."""
    row = connection.execute("PRAGMA user_version").fetchone()
    return int(row[0])


def migrate(connection: sqlite3.Connection, database_path: Path | None) -> None:
    """Bringt die Datenbank auf die neueste Schemaversion.

    Vor der Migration einer *bereits befüllten* Datenbank wird eine
    Sicherungskopie ``benutzer.db.v<alt>.bak`` neben der Datei angelegt.

    Args:
        connection: Offene Verbindung (ohne laufende Transaktion).
        database_path: Pfad der Datei für die Sicherungskopie; ``None`` bei
            einer reinen Speicher-Datenbank (Tests).

    Raises:
        DatabaseError: Wenn die Datenbank von einer neueren Programmversion
            stammt oder eine Migration fehlschlägt.
    """
    version = current_version(connection)
    if version > LATEST_VERSION:
        raise DatabaseError(
            "Diese Datenbank wurde mit einer neueren Programmversion erstellt "
            f"(Schema {version}, unterstützt wird bis {LATEST_VERSION}). "
            "Bitte die Anwendung aktualisieren."
        )

    pending = [(target, script) for target, script in MIGRATIONS if target > version]
    if not pending:
        return

    if version > 0 and database_path is not None:
        _backup_before_migration(database_path, version)

    for target, script in pending:
        logger.info("Migriere Datenbankschema von Version %d auf %d.", version, target)
        try:
            # executescript() führt mehrere Anweisungen aus. Durch das
            # umschließende BEGIN/COMMIT ist die Migration atomar: Entweder
            # gelingt sie komplett, oder die Datenbank bleibt unverändert.
            connection.executescript(f"BEGIN;\n{script}\nPRAGMA user_version = {target};\nCOMMIT;")
        except sqlite3.Error as exc:
            if connection.in_transaction:
                connection.execute("ROLLBACK")
            raise DatabaseError(f"Migration auf Schemaversion {target} fehlgeschlagen: {exc}") from exc
        version = target


def _backup_before_migration(database_path: Path, version: int) -> None:
    """Legt vor einer Migration eine Kopie der Datenbankdatei an."""
    backup_path = database_path.with_name(f"{database_path.name}.v{version}.bak")
    try:
        shutil.copy2(database_path, backup_path)
        logger.info("Sicherungskopie vor Migration angelegt: %s", backup_path)
    except OSError as exc:
        raise DatabaseError(
            f"Vor der Migration konnte keine Sicherungskopie angelegt werden: {exc}"
        ) from exc
