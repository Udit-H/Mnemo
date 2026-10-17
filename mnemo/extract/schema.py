"""What the LLM must return for one conversation. Lenient validators coerce near-misses instead of failing."""

import hashlib
import re
from typing import Literal

from pydantic import BaseModel, field_validator

# Keep in sync with the Domain seed in mnemo/graph/schema.cypher
DOMAINS = ("coding", "learning", "planning", "health", "finance", "career", "personal")
ENTITY_KINDS = ("tool", "library", "language", "service", "person", "organization", "place", "product", "other")
OUTCOMES = ("success", "failed", "abandoned", "unknown")
SCOPES = ("context", "domain", "global")


def norm(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().lower()


def stable_id(prefix: str, *parts: str) -> str:
    """Same id for texts that differ only in case, spacing or punctuation."""
    key = "|".join(re.sub(r"[^\w\s]", "", norm(p)) for p in parts)
    digest = hashlib.sha1(key.encode()).hexdigest()[:12]
    return f"{prefix}:{digest}"


def _pick(value: str, allowed: tuple[str, ...], default: str) -> str:
    value = norm(value or "")
    return value if value in allowed else default


class Entity(BaseModel):
    name: str
    kind: str = "other"

    @field_validator("name")
    @classmethod
    def _name(cls, v: str) -> str:
        return norm(v)

    @field_validator("kind")
    @classmethod
    def _kind(cls, v: str) -> str:
        return _pick(v, ENTITY_KINDS, "other")


class Goal(BaseModel):
    text: str
    existing_id: str | None = None


class Task(BaseModel):
    text: str
    outcome: str = "unknown"
    outcome_summary: str = ""
    uses: list[str] = []
    goal: str | None = None  # an existing goal id, or the text of a goal in this extraction

    @field_validator("outcome")
    @classmethod
    def _outcome(cls, v: str) -> str:
        return _pick(v, OUTCOMES, "unknown")

    @field_validator("uses")
    @classmethod
    def _uses(cls, v: list[str]) -> list[str]:
        return [norm(x) for x in v if x and x.strip()]


class Preference(BaseModel):
    text: str
    scope: str = "domain"
    existing_id: str | None = None  # same as an existing preference
    replaces: str | None = None  # contradicts (and supersedes) an existing preference
    correction: str | None = None  # the user's words if this came from correcting the assistant

    @field_validator("scope")
    @classmethod
    def _scope(cls, v: str) -> str:
        return _pick(v, SCOPES, "domain")


class Extraction(BaseModel):
    domain: str
    summary: str = ""
    topics: list[str] = []
    entities: list[Entity] = []
    goals: list[Goal] = []
    tasks: list[Task] = []
    preferences: list[Preference] = []

    @field_validator("domain")
    @classmethod
    def _domain(cls, v: str) -> str:
        return _pick(v, DOMAINS, "personal")

    @field_validator("topics")
    @classmethod
    def _topics(cls, v: list[str]) -> list[str]:
        return list(dict.fromkeys(norm(t) for t in v if t and t.strip()))[:6]

    @field_validator("entities")
    @classmethod
    def _entities(cls, v: list[Entity]) -> list[Entity]:
        return list({e.name: e for e in v if e.name}.values())[:10]

    @field_validator("tasks")
    @classmethod
    def _tasks(cls, v: list[Task]) -> list[Task]:
        return v[:5]
