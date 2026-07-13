"""Pydantic request/response models for the Job-Star API."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from job_star.models import Domain, GoalStatus, Urgency


# ============================================================================
# Shared
# ============================================================================
class ORMBase(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class PaginatedResponse(BaseModel):
    items: list[Any]
    total: int
    page: int
    page_size: int


# ============================================================================
# Auth
# ============================================================================
class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


class UserIdentity(BaseModel):
    user_id: str
    role: str = "agent"
    email: str = ""


# ============================================================================
# Goal
# ============================================================================
class IntakeRequest(BaseModel):
    title: str = Field(..., min_length=1, max_length=255)
    description: str = ""
    domain: Domain = Domain.CODING
    urgency: Urgency = Urgency.SOON
    source: str = "api"
    metadata: dict = Field(default_factory=dict)
    requested_by: str = ""


class GoalSummary(BaseModel):
    id: str
    title: str
    description: Optional[str]
    domain: str
    status: str
    urgency: str
    progress: float
    created_at: datetime
    updated_at: datetime
    expert: Optional[str] = None
    requested_by: Optional[str] = None
    step_count: int = 0
    completed_steps: int = 0
    failed_steps: int = 0
    pending_checkin_id: Optional[str] = None


class GoalResponse(GoalSummary):
    steps: list[dict] = Field(default_factory=list)
    conflicts: list[dict] = Field(default_factory=list)


class GoalListResponse(BaseModel):
    goals: list[GoalSummary]
    total: int


class WorkRequest(BaseModel):
    model: Optional[str] = None


class CompleteRequest(BaseModel):
    pass


# ============================================================================
# Ask / Answer
# ============================================================================
class AskRequest(BaseModel):
    question: str = Field(..., min_length=1)
    goal_id: Optional[str] = None


class AskResponse(BaseModel):
    question_id: str
    question: str
    goal_id: Optional[str]
    status: str
    created_at: datetime


class AnswerRequest(BaseModel):
    answer: str = Field(..., min_length=1)


# ============================================================================
# Events
# ============================================================================
class EventPayload(BaseModel):
    type: str
    payload: dict
    ts: datetime


class StatusResponse(BaseModel):
    status: str
    service: str
    version: str
