"""Envelope schemas for the standard API response shape (spec rules 44-45)."""

from __future__ import annotations

from typing import Any, Generic, TypeVar

from pydantic import BaseModel, Field

T = TypeVar("T")


class ErrorDetail(BaseModel):
    code: str = Field(..., examples=["SUBSCRIPTION_EXPIRED"])
    message: str = Field(..., examples=["Subscription has expired"])
    details: dict[str, Any] | None = None


class ErrorResponse(BaseModel):
    success: bool = False
    error: ErrorDetail
    request_id: str | None = None


class SuccessResponse(BaseModel, Generic[T]):
    success: bool = True
    data: T
    request_id: str | None = None


class PageMeta(BaseModel):
    page: int
    page_size: int
    total: int


class PaginatedResponse(BaseModel, Generic[T]):
    success: bool = True
    data: list[T]
    meta: PageMeta
    request_id: str | None = None
