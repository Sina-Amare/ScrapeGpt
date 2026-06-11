"""User-owned browser sessions for authenticated scraping."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    LargeBinary,
    String,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin


class BrowserSession(TimestampMixin, Base):
    """Encrypted browser cookies supplied by the user for a specific domain.

    Cookies are write-only: they are stored encrypted and only ever
    decrypted inside the fetcher. The API never returns plaintext cookies.
    """

    __tablename__ = "browser_sessions"

    id: Mapped[int] = mapped_column(
        Integer, primary_key=True, autoincrement=True
    )
    user_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    # Bare hostname, e.g. "oatd.org" (no scheme, no path).
    domain: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    # Fernet-encrypted JSON: list of Playwright cookie dicts.
    cookies_encrypted: Mapped[bytes] = mapped_column(
        LargeBinary, nullable=False
    )
    user_agent: Mapped[str | None] = mapped_column(String(512), nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default="true", nullable=False
    )

    user = relationship(
        "User",
        back_populates="browser_sessions",
        foreign_keys=[user_id],
    )
    projects = relationship(
        "Project",
        back_populates="browser_session",
        foreign_keys="Project.browser_session_id",
    )

    def __repr__(self) -> str:
        return (
            f"<BrowserSession {self.id} domain={self.domain} "
            f"user_id={self.user_id}>"
        )
