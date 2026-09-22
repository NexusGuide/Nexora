"""Custom SQLAlchemy column types."""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from sqlalchemy import String, TypeDecorator
from sqlalchemy.engine import Dialect


class EnumString(TypeDecorator):
    """Stores a :class:`StrEnum` as its string value and loads it back as the
    enum member.

    A plain ``String`` column would hand back a ``str``, which silently breaks
    identity checks (``status is UserStatus.ACTIVE``) while equality still
    passes — exactly the kind of mismatch that turns into a security bug in an
    authorization check. Loading the member back makes both forms correct.

    Unlike SQLAlchemy's native ``Enum``, this does not create a database-level
    enum type, so adding a value needs no migration of a type definition.
    """

    impl = String
    cache_ok = True

    def __init__(self, enum_class: type[StrEnum], length: int = 32, **kw: Any) -> None:
        self.enum_class = enum_class
        super().__init__(length=length, **kw)

    def process_bind_param(self, value: Any, dialect: Dialect) -> str | None:
        if value is None:
            return None
        if isinstance(value, self.enum_class):
            return value.value
        # Validates on the way in: an unknown value raises here rather than
        # being written and failing later on read.
        return self.enum_class(value).value

    def process_result_value(self, value: Any, dialect: Dialect) -> StrEnum | None:
        if value is None:
            return None
        return self.enum_class(value)
