"""User model for authentication and account ownership."""

from typing import TYPE_CHECKING

from sqlalchemy import Boolean, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin

if TYPE_CHECKING:
    from app.models.browser_session import BrowserSession
    from app.models.provider_config import ProviderConfig


class User(TimestampMixin, Base):
    """
    User model for the ScrapeGPT platform.

    Handles authentication, account status, scrape task ownership, and provider
    configuration ownership.
    """
    
    __tablename__ = "users"
    
    # -------------------------------------------------------------------------
    # Primary Key
    # -------------------------------------------------------------------------
    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
        autoincrement=True,
    )
    
    # -------------------------------------------------------------------------
    # Authentication Fields
    # -------------------------------------------------------------------------
    email: Mapped[str] = mapped_column(
        String(255),
        unique=True,
        nullable=False,
        index=True,
        comment="User's email address (login identifier)",
    )
    
    hashed_password: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        comment="bcrypt hashed password",
    )
    
    # -------------------------------------------------------------------------
    # Account Status
    # -------------------------------------------------------------------------
    is_active: Mapped[bool] = mapped_column(
        Boolean,
        default=True,
        server_default="true",
        nullable=False,
        index=True,
        comment="Whether the account is enabled",
    )
    
    is_verified: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        server_default="false",
        nullable=False,
        comment="Whether email has been verified",
    )
    
    default_provider_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("provider_configs.id", ondelete="SET NULL"),
        nullable=True,
        comment="Default provider config for AI calls",
    )
    
    # -------------------------------------------------------------------------
    # Relationships
    # -------------------------------------------------------------------------
    scrape_tasks = relationship(
        "ScrapeTask",
        back_populates="user",
        cascade="all, delete-orphan",
    )

    provider_configs: Mapped[list["ProviderConfig"]] = relationship(
        "ProviderConfig",
        back_populates="user",
        cascade="all, delete-orphan",
        foreign_keys="ProviderConfig.user_id",
    )

    default_provider: Mapped["ProviderConfig | None"] = relationship(
        "ProviderConfig",
        foreign_keys=[default_provider_id],
        post_update=True,
    )

    browser_sessions: Mapped[list["BrowserSession"]] = relationship(
        "BrowserSession",
        back_populates="user",
        cascade="all, delete-orphan",
        foreign_keys="BrowserSession.user_id",
    )

    # -------------------------------------------------------------------------
    # Methods
    # -------------------------------------------------------------------------

    def __repr__(self) -> str:
        """String representation for debugging."""
        return f"<User {self.email}>"

