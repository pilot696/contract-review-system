"""FastAPI 依赖。"""

from app.config import get_settings
from app.db import get_db

__all__ = ["get_db", "get_settings"]
