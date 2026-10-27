"""Data groups: which provider may read which of a person's data.

A connection belongs to a data group, and so does everything a model can be
handed about a person -- memories, notes, skills, knowledge bases, reports, the
personality and impression a model keeps, and the chats themselves. A model
reads only the rows of the group its own connection is in. Two providers in one
group see the same data; two in different groups never see each other's.

**Data belongs to a group, not to a connection.** Moving a connection into
another group does not carry anything with it -- that provider simply starts
reading the other group. That is the only reading under which "which provider
has seen this?" has an answer that does not depend on history.

`id` is a short string rather than a generated UUID so the one group every
instance has can be called `"default"` in code and in the database alike, and
every row written before groups existed can be backfilled to it without a
lookup. See services/data_groups.py for how a group is resolved.
"""

from __future__ import annotations

from sqlalchemy import ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from lembas.db.base import Base, Timestamps, UUIDPrimaryKey, new_id
from lembas.db.models.organisation import DEFAULT_ORG

# The group every instance has, every connection is in until somebody says
# otherwise, and every row written before data groups existed is backfilled to.
DEFAULT_GROUP = "default"


class DataGroup(Timestamps, Base):
    """One partition of the people's data, and the models that may read it.

    `owner_id` NULL is an instance group, set up by an administrator and usable
    by everybody. Set, it is somebody's personal group -- made by a person
    holding `data.manage` to keep one provider away from the rest of their own
    data, and invisible to everybody else.

    The four model columns name the services that read a group's data without
    being a chat's model: the embedder that indexes it and the model that
    reviews generated images. Empty means the instance's own choice, which is
    what every group starts with. They are text ids with a connection beside
    them, never a `Model` primary key, for the reason `Chat.model_id` gives: a
    "Test & refresh" recreates the row.
    """

    __tablename__ = "data_groups"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    org_id: Mapped[str] = mapped_column(
        String(32),
        ForeignKey("organisations.id", ondelete="CASCADE"),
        default=DEFAULT_ORG,
        nullable=False,
        index=True,
    )
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="")
    position: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    owner_id: Mapped[str | None] = mapped_column(
        String(32), ForeignKey("users.id", ondelete="CASCADE"), nullable=True, index=True
    )

    embedding_model_id: Mapped[str] = mapped_column(String(300), default="")
    embedding_connection_id: Mapped[str] = mapped_column(String(32), default="")
    review_model_id: Mapped[str] = mapped_column(String(300), default="")
    review_connection_id: Mapped[str] = mapped_column(String(32), default="")

    @property
    def is_default(self) -> bool:
        return self.id == DEFAULT_GROUP

    @property
    def personal(self) -> bool:
        return self.owner_id is not None

    def __repr__(self) -> str:
        return f"<DataGroup {self.id} {self.name!r}>"


class InDataGroup:
    """Mixin: the data group a row belongs to.

    Nullable, and NULL reads as the default group everywhere -- which is what a
    row written before data groups existed holds until `data_groups.sweep_unassigned` reaches
    it at startup. A plain string rather than a foreign key: `sync_schema` adds
    a column with its type only, so a `REFERENCES` clause would exist on a fresh
    database and not on an upgraded one, and the two would then disagree about
    what deleting a group does.
    """

    data_group_id: Mapped[str | None] = mapped_column(String(32), nullable=True)


# How a data group's access rules apply (`security/access.py`):
RULE_PRESET = "preset"  # what a new chat in the group starts with, editable per chat
RULE_FLOOR = "floor"  # the most a chat in the group may ever have there
RULE_MODES = (RULE_PRESET, RULE_FLOOR)


class DataGroupRule(UUIDPrimaryKey, Timestamps, Base):
    """One path and level in a data group's access preset or floor.

    A preset rule is a grant a new chat starts from (`level` none, ro, stage or
    commit, covering everything below `path`; the most specific wins). A floor
    rule is a ceiling no chat in the group can widen past, the most specific
    applying; no floor rule at all means no cap.
    """

    __tablename__ = "data_group_rules"

    data_group_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("data_groups.id", ondelete="CASCADE"), nullable=False
    )
    path: Mapped[str] = mapped_column(Text, nullable=False)
    level: Mapped[str] = mapped_column(String(8), nullable=False)
    mode: Mapped[str] = mapped_column(String(8), default=RULE_PRESET, nullable=False)

    def __repr__(self) -> str:
        return f"<DataGroupRule {self.mode} {self.path} {self.level}>"


Index("ix_data_group_rules_group", DataGroupRule.data_group_id, DataGroupRule.mode)
