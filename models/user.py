"""Entity: Benutzer der Anwendung (der Vermieter bzw. Verwalter)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from models.exceptions import ValidationError
from utils.validators import require_text

#: Zeichen, die Windows in Ordnernamen nicht erlaubt. Da aus dem Benutzernamen
#: ein Ordner entsteht, dürfen sie im Namen nicht vorkommen.
_FORBIDDEN_NAME_CHARS = '<>:"/\\|?*'


@dataclass
class User:
    """Ein Benutzer. Jede ``benutzer.db`` gehört genau einem Benutzer."""

    name: str
    created_at: datetime = field(default_factory=datetime.now)
    last_login: datetime | None = None
    id: int | None = None

    def validate(self) -> None:
        """Prüft die fachlichen Regeln; wirft ``ValidationError`` bei Verstößen."""
        self.name = validate_user_name(self.name)


def validate_user_name(name: str) -> str:
    """Prüft einen Benutzernamen und gibt ihn bereinigt zurück.

    Der Name wird auch als Ordnername verwendet und darf deshalb keine für
    Windows-Dateinamen verbotenen Zeichen enthalten.
    """
    cleaned = require_text(name, "Benutzername", max_length=60)
    if any(char in _FORBIDDEN_NAME_CHARS for char in cleaned):
        raise ValidationError(
            f"Der Benutzername darf keines dieser Zeichen enthalten: {_FORBIDDEN_NAME_CHARS}"
        )
    if cleaned.endswith((".", " ")):
        raise ValidationError("Der Benutzername darf nicht mit einem Punkt enden.")
    return cleaned
