"""Runtime configuration."""

import os

DEFAULT_DATABASE_URL = "postgresql://stacdupes:stacdupes@localhost:5432/stacdupes"


def database_url() -> str:
    """Return the configured PostgreSQL connection string."""
    return os.environ.get("DATABASE_URL", DEFAULT_DATABASE_URL)
