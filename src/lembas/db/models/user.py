"""Users, groups and login sessions."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Table,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from lembas.db.base import Base, Timestamps, UUIDPrimaryKey
from lembas.db.models.organisation import DEFAULT_ORG, ORG_MANAGERS, ORG_MEMBER
from lembas.db.types import JSONDict, JSONList

if TYPE_CHECKING:
    # Annotation only; SQLAlchemy resolves the real class from its registry.
    from lembas.db.models.connection import Model
    from lembas.db.models.tool import CustomTool, McpServer

# Roles are a simple ordered ladder rather than a permission matrix. Groups
# (below) carry finer-grained permissions once the users/groups UI lands.
ROLE_ADMIN = "admin"
ROLE_USER = "user"
ROLE_PENDING = "pending"  # registered but awaiting admin approval

user_groups = Table(
    "user_groups",
    Base.metadata,
    Column("user_id", String(32), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
    Column("group_id", String(32), ForeignKey("groups.id", ondelete="CASCADE"), primary_key=True),
)


class User(UUIDPrimaryKey, Timestamps, Base):
    __tablename__ = "users"

    email: Mapped[str] = mapped_column(String(320), unique=True, nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    password_hash: Mapped[str] = mapped_column(Text, nullable=False)
    role: Mapped[str] = mapped_column(String(16), default=ROLE_USER, nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # Per-user preferences: theme, default model, composer behaviour, etc.
    settings_json: Mapped[dict[str, Any]] = mapped_column(JSONDict, default=dict)

    # The one organisation this account belongs to, and its role there.
    # `role` above is the *instance* role: an instance admin runs connections,
    # models, services, updates, backups and organisations, and gets no
    # implicit access to anybody's files. `org_role` is what this person may
    # do inside their organisation (`organisation.ORG_ROLES`).
    org_id: Mapped[str] = mapped_column(
        String(32),
        ForeignKey("organisations.id", ondelete="CASCADE"),
        default=DEFAULT_ORG,
        nullable=False,
        index=True,
    )
    org_role: Mapped[str] = mapped_column(String(16), default=ORG_MEMBER, nullable=False)

    groups: Mapped[list[Group]] = relationship(secondary=user_groups, back_populates="users")
    sessions: Mapped[list[Session]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )

    @property
    def is_admin(self) -> bool:
        return self.role == ROLE_ADMIN

    @property
    def manages_org(self) -> bool:
        """An owner or admin of their organisation (not the instance)."""
        return self.org_role in ORG_MANAGERS

    def __repr__(self) -> str:
        return f"<User {self.email} role={self.role}>"


class Group(UUIDPrimaryKey, Timestamps, Base):
    """A named set of users. Permissions are enforced once the RBAC pass lands."""

    __tablename__ = "groups"
    # A name is unique within its organisation, not across the instance.
    __table_args__ = (UniqueConstraint("org_id", "name", name="uq_groups_org_name"),)

    org_id: Mapped[str] = mapped_column(
        String(32),
        ForeignKey("organisations.id", ondelete="CASCADE"),
        default=DEFAULT_ORG,
        nullable=False,
        index=True,
    )
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="")

    # Only the granted keys need be present. Absent means "no opinion", not
    # "deny" -- permissions union across a user's groups. See
    # lembas.security.permissions.
    permissions_json: Mapped[dict[str, Any]] = mapped_column(JSONDict, default=dict)

    # What members of this group may use at once. Resolved across a user's groups by
    # **maximum**, which is the union rule applied to numbers: being in a second
    # group can only ever grant more. Zero means "no limit" and therefore wins
    # outright, because a group that says "unlimited" saying less than one that
    # says "a million" would be the union rule inverted for one value.
    #
    # Absent keys mean the group has no opinion and contribute nothing. See
    # security/permissions.py:limits_for.
    limits_json: Mapped[dict[str, Any]] = mapped_column(JSONDict, default=dict)

    users: Mapped[list[User]] = relationship(secondary=user_groups, back_populates="groups")
    models: Mapped[list[Model]] = relationship(
        "Model", secondary="model_groups", back_populates="groups"
    )
    custom_tools: Mapped[list[CustomTool]] = relationship(
        "CustomTool", secondary="custom_tool_groups", back_populates="groups"
    )
    mcp_servers: Mapped[list[McpServer]] = relationship(
        "McpServer", secondary="mcp_server_groups", back_populates="groups"
    )


class Session(UUIDPrimaryKey, Timestamps, Base):
    """Server-side login session.

    Sessions live in the database rather than in a signed JWT so that logging
    out, banning a user, or rotating a device actually revokes access
    immediately instead of waiting for a token to expire.
    """

    __tablename__ = "sessions"

    user_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    # SHA-256 of the cookie value. The raw token is shown to the browser once
    # and never stored, so a database leak does not hand over live sessions.
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    user_agent: Mapped[str] = mapped_column(Text, default="")
    ip_address: Mapped[str] = mapped_column(String(45), default="")

    user: Mapped[User] = relationship(back_populates="sessions")


Index("ix_sessions_user_id", Session.user_id)


class PushSubscription(UUIDPrimaryKey, Timestamps, Base):
    """One browser, on one device, that has agreed to be told.

    Per device rather than per account, and that is not a detail: the permission
    and the subscription both belong to a browser, so somebody signed in on a
    laptop and a phone has two of these and revoking one must not silence the
    other. It is also why there is no "notifications on" column on `User` -- the
    presence of a row here *is* the state, and it cannot drift from what the
    browser thinks.

    `endpoint` is chosen by the browser vendor and is the address their push
    service will accept a message at. Unique, because a browser that
    re-subscribes hands back the same one and two rows would mean two
    notifications for one arrival.

    `p256dh` and `auth_secret` are the browser's half of the encryption. Stored
    as the browser gave them, base64url: they are public key material and a
    per-subscription salt, not credentials -- what they protect is the payload,
    and a database holding them can already read everything the payload could
    say. See services/push.py.
    """

    __tablename__ = "push_subscriptions"

    user_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    endpoint: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    p256dh: Mapped[str] = mapped_column(String(255), nullable=False)
    auth_secret: Mapped[str] = mapped_column(String(64), nullable=False)
    # Which device this is, for a list somebody can revoke from. Whatever the
    # browser says about itself, trimmed; never parsed.
    label: Mapped[str] = mapped_column(String(200), default="")
    # The last refusal from the push service, kept so a subscription that has
    # stopped working says why rather than being silently useless. A 404 or 410
    # deletes the row instead -- that is the end of its life, not a fault.
    last_error: Mapped[str] = mapped_column(Text, default="")
    # The app's token this registration came in under (UnifiedPush through
    # `/api/v2/push`), or None for a browser's. Signing that token out takes the
    # registration with it (`api_tokens.forget_device`): a phone that signed
    # out must stop being told. No foreign key, as `DeviceSessionDelete` has
    # none -- the schema is additive, and the revoke path is the one writer.
    token_id: Mapped[str | None] = mapped_column(String(32), nullable=True)

    user: Mapped[User] = relationship()


Index("ix_push_subscriptions_user_id", PushSubscription.user_id)


class Usage(Base):
    """What one account used of one model in one month. Counting only.

    A row per user, period and model rather than a row per reply: the usage
    page wants totals, and a reply's own cost is on its message
    (`Message.cost_micro`). **Never a quota** -- nothing reads this to refuse
    anything; LLeMbas counts and prices, it does not ration.

    `period` is "YYYY-MM" in UTC. `model_id` is the model's text id ("" when
    nothing names one); speech and pictures are recorded under the service's
    own name (`stt/whisper-1`, `image/sdxl`). `cost_micro` is micro-units of
    `currency`: the currency of the first priced use in the row, NULL for a row
    with no priced use yet (or one written before this column, which reads as
    the instance's), and `"*"` once two currencies have met in it -- a model's
    connection changed currency mid-month, say. A `"*"` row's cost is not one
    amount and is never reported as one; `UsageCost` holds the same money per
    currency, exactly.

    Written by `generation._persist` for a reply (finished, stopped or failed
    alike -- an endpoint charges for what it generated), by the `/v1` routes
    for a call, and by the speech and picture services.
    """

    __tablename__ = "usage"

    user_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    period: Mapped[str] = mapped_column(String(7), primary_key=True)
    model_id: Mapped[str] = mapped_column(String(300), primary_key=True, default="")

    tokens_in: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    tokens_cached: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    tokens_out: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    requests: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    cost_micro: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    images: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    audio_seconds: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    speech_chars: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    currency: Mapped[str | None] = mapped_column(String(3), nullable=True)

    def __repr__(self) -> str:
        return f"<Usage {self.user_id} {self.period} {self.model_id}>"


class UsageCost(Base):
    """What one account's use cost in one month, in one currency. Counting only.

    `Usage.cost_micro` adds every model's cost together, but connections price
    in their own currencies, and dollars and euros are never summed: this is
    the same money kept apart per currency, written beside it by
    `services/usage.record`. A reply is in its connection's currency; speech
    and pictures in the instance's. `cost_micro` is micro-units of `currency`.
    """

    __tablename__ = "usage_costs"

    user_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    period: Mapped[str] = mapped_column(String(7), primary_key=True)
    currency: Mapped[str] = mapped_column(String(3), primary_key=True)
    cost_micro: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)

    def __repr__(self) -> str:
        return f"<UsageCost {self.user_id} {self.period} {self.currency}>"


class UserModelSetting(UUIDPrimaryKey, Timestamps, Base):
    """One person's own defaults for one model (Settings -> Models).

    Two things, both narrowing what the instance already decided and never
    widening it:

    - `tools_off_json`: tool gates (`tools.gate_of`) this person does not want
      offered with this model. Applied in `tools.resolve_tools` *after* the
      model's capabilities, their permissions and the instance's settings, so a
      gate named here that those had already removed changes nothing. A chat's
      own explicit switch still wins (`scope_json.families[gate] = True`).
    - `reasoning_effort`: the effort a new chat with this model starts at, in
      place of the model's default. "" follows the instance, "off" sends none,
      anything else is one of the model's own levels -- checked against them
      where it is read, since a model's vocabulary can narrow afterwards.

    A row per person per model rather than a key in `User.settings_json`: it is
    read on every reply's tool resolution, and one indexed row is cheaper and
    plainer than walking a JSON blob that also holds the theme. Keyed by the
    model's `model_id`, as the default model in `settings_json` is, so a model
    rediscovered on its connection keeps its row.
    """

    __tablename__ = "user_model_settings"
    __table_args__ = (UniqueConstraint("user_id", "model_id", name="uq_user_model_setting"),)

    user_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    model_id: Mapped[str] = mapped_column(String(300), nullable=False)
    tools_off_json: Mapped[list[str]] = mapped_column(JSONList, default=list)
    reasoning_effort: Mapped[str] = mapped_column(String(16), default="", nullable=False)

    def __repr__(self) -> str:
        return f"<UserModelSetting {self.user_id} {self.model_id}>"
