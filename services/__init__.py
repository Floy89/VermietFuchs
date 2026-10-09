"""Application/Service Layer: orchestriert Abläufe und enthält die Abrechnungslogik.

Services hängen nur von den Repository-Protokollen ab
(:mod:`repositories.interfaces`), nie von SQLite oder Qt.
"""
