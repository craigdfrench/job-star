"""
Goal registry: persistent store of known goals/intake requests.

Used by the duplicate checker to find candidate matches for new intake requests.
Backed by SQLite for simplicity and zero external dependencies during bootstrap.
"""

from __future__ import annotations

import json
import sqlite3
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator


SCHEMA = """
CREATE TABLE IF NOT EXISTS goals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    goal_id TEXT UNIQUE NOT NULL,
    title TEXT NOT NULL,
    domain TEXT,
    urgency TEXT,
    type TEXT,
    status TEXT NOT NULL DEFAULT 'active',
    source_text TEXT NOT NULL,
    source_hash TEXT NOT NULL,
    keywords_json TEXT NOT NULL DEFAULT '[]',
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_goals_source_hash ON goals(source_hash);
CREATE INDEX IF NOT EXISTS idx_goals_domain ON goals(domain);
CREATE INDEX IF NOT EXISTS idx_goals_status ON goals(status);
CREATE INDEX IF NOT EXISTS idx_goals_keywords ON goals(keywords_json);
"""


@dataclass
class GoalRecord:
    goal_id: str
    title: str
    domain: str | None
    urgency: str | None
    type: str | None
    status: str
    source_text: str
    source_hash: str
    keywords: list[str] = field(default_factory=list)
    created_at: float = 0.0
    updated_at: float = 0.0
    id: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "goal_id": self.goal_id,
            "title": self.title,
            "domain": self.domain,
            "urgency": self.urgency,
            "type": self.type,
            "status": self.status,
            "source_text": self.source_text,
            "source_hash": self.source_hash,
            "keywords": self.keywords,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


class GoalRegistry:
    """SQLite-backed goal registry."""

    def __init__(self, db_path: str | Path = "job_star/data/goal_registry.db") -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    @contextmanager
    def _conn(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def _init_schema(self) -> None:
        with self._conn() as conn:
            conn.executescript(SCHEMA)

    def register(self, record: GoalRecord) -> None:
        """Insert or replace a goal record."""
        now = time.time()
        record.created_at = record.created_at or now
        record.updated_at = now
        with self._conn() as conn:
            conn.execute(
                """
                INSERT INTO goals
                    (goal_id, title, domain, urgency, type, status,
                     source_text, source_hash, keywords_json, created_at, updated_at)
                VALUES
                    (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(goal_id) DO UPDATE SET
                    title=excluded.title,
                    domain=excluded.domain,
                    urgency=excluded.urgency,
                    type=excluded.type,
                    status=excluded.status,
                    source_text=excluded.source_text,
                    source_hash=excluded.source_hash,
                    keywords_json=excluded.keywords_json,
                    updated_at=excluded.updated_at
                """,
                (
                    record.goal_id,
                    record.title,
                    record.domain,
                    record.urgency,
                    record.type,
                    record.status,
                    record.source_text,
                    record.source_hash,
                    json.dumps(record.keywords),
                    record.created_at,
                    record.updated_at,
                ),
            )

    def get(self, goal_id: str) -> GoalRecord | None:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM goals WHERE goal_id = ?", (goal_id,)
            ).fetchone()
            return self._row_to_record(row) if row else None

    def list_active(self) -> list[GoalRecord]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM goals WHERE status = 'active' ORDER BY updated_at DESC"
            ).fetchall()
            return [self._row_to_record(r) for r in rows]

    def find_by_source_hash(self, source_hash: str) -> list[GoalRecord]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM goals WHERE source_hash = ?", (source_hash,)
            ).fetchall()
            return [self._row_to_record(r) for r in rows]

    def find_by_domain(self, domain: str) -> list[GoalRecord]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM goals WHERE domain = ? AND status = 'active'",
                (domain,),
            ).fetchall()
            return [self._row_to_record(r) for r in rows]

    def find_by_keyword_overlap(self, keywords: list[str]) -> list[GoalRecord]:
        """Naive scan: returns active goals that share at least one keyword."""
        if not keywords:
            return []
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM goals WHERE status = 'active'"
            ).fetchall()
            results: list[tuple[int, GoalRecord]] = []
            kw_set = {k.lower() for k in keywords}
            for row in rows:
                rec = self._row_to_record(row)
                rec_kw = {k.lower() for k in rec.keywords}
                overlap = len(kw_set & rec_kw)
                if overlap > 0:
                    results.append((overlap, rec))
            results.sort(key=lambda x: x[0], reverse=True)
            return [r for _, r in results]

    @staticmethod
    def _row_to_record(row: sqlite3.Row) -> GoalRecord:
        return GoalRecord(
            id=row["id"],
            goal_id=row["goal_id"],
            title=row["title"],
            domain=row["domain"],
            urgency=row["urgency"],
            type=row["type"],
            status=row["status"],
            source_text=row["source_text"],
            source_hash=row["source_hash"],
            keywords=json.loads(row["keywords_json"]),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )


// --- DUPLICATE BLOCK ---

"""
Goal Registry — persistent store of accepted goals for Job-Star.

File-backed (JSON) during bootstrap mode. Designed to be swappable with
a database backend later without changing the public interface.
"""

from __future__ import annotations

import json
import os
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional


@dataclass
class Goal:
    """A single accepted goal in the registry."""
    id: str
    title: str
    description: str
    domain: str
    urgency: str
    goal_type: str
    keywords: List[str] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)
    status: str = "active"
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Goal":
        return cls(**data)


class GoalRegistry:
    """
    File-backed goal registry.

    Layout:
        {
            "goals": [ {Goal}, ... ],
            "version": 1
        }
    """

    def __init__(self, path: str | os.PathLike = "data/goal_registry.json"):
        self.path = Path(path)
        self._goals: Dict[str, Goal] = {}
        self._load()

    # ---- persistence ----

    def _load(self) -> None:
        if not self.path.exists():
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self._save()
            return
        with self.path.open("r", encoding="utf-8") as f:
            data = json.load(f)
        for g in data.get("goals", []):
            goal = Goal.from_dict(g)
            self._goals[goal.id] = goal

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": 1,
            "goals": [g.to_dict() for g in self._goals.values()],
        }
        tmp = self.path.with_suffix(".json.tmp")
        with tmp.open("w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2, ensure_ascii=False)
        tmp.replace(self.path)

    # ---- public API ----

    def add(self, goal: Goal) -> None:
        """Insert or update a goal."""
        self._goals[goal.id] = goal
        self._save()

    def get(self, goal_id: str) -> Optional[Goal]:
        return self._goals.get(goal_id)

    def all_goals(self) -> List[Goal]:
        return list(self._goals.values())

    def active_goals(self) -> List[Goal]:
        return [g for g in self._goals.values() if g.status == "active"]

    def remove(self, goal_id: str) -> bool:
        if goal_id in self._goals:
            del self._goals[goal_id]
            self._save()
            return True
        return False

    def new_id(self) -> str:
        return f"goal-{uuid.uuid4().hex[:12]}"


// --- DUPLICATE BLOCK ---

"""
Goal registry: persistent store of known goals/intake requests.

Used by the duplicate checker to find candidate matches for new intake requests.
Backed by SQLite for simplicity and zero external dependencies during bootstrap.
"""

from __future__ import annotations

import json
import sqlite3
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator


SCHEMA = """
CREATE TABLE IF NOT EXISTS goals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    goal_id TEXT UNIQUE NOT NULL,
    title TEXT NOT NULL,
    domain TEXT,
    urgency TEXT,
    type TEXT,
    status TEXT NOT NULL DEFAULT 'active',
    source_text TEXT NOT NULL,
    source_hash TEXT NOT NULL,
    keywords_json TEXT NOT NULL DEFAULT '[]',
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_goals_source_hash ON goals(source_hash);
CREATE INDEX IF NOT EXISTS idx_goals_domain ON goals(domain);
CREATE INDEX IF NOT EXISTS idx_goals_status ON goals(status);
CREATE INDEX IF NOT EXISTS idx_goals_keywords ON goals(keywords_json);
"""


@dataclass
class GoalRecord:
    goal_id: str
    title: str
    domain: str | None
    urgency: str | None
    type: str | None
    status: str
    source_text: str
    source_hash: str
    keywords: list[str] = field(default_factory=list)
    created_at: float = 0.0
    updated_at: float = 0.0
    id: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "goal_id": self.goal_id,
            "title": self.title,
            "domain": self.domain,
            "urgency": self.urgency,
            "type": self.type,
            "status": self.status,
            "source_text": self.source_text,
            "source_hash": self.source_hash,
            "keywords": self.keywords,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


class GoalRegistry:
    """SQLite-backed goal registry."""

    def __init__(self, db_path: str | Path = "job_star/data/goal_registry.db") -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    @contextmanager
    def _conn(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def _init_schema(self) -> None:
        with self._conn() as conn:
            conn.executescript(SCHEMA)

    def register(self, record: GoalRecord) -> None:
        """Insert or replace a goal record."""
        now = time.time()
        record.created_at = record.created_at or now
        record.updated_at = now
        with self._conn() as conn:
            conn.execute(
                """
                INSERT INTO goals
                    (goal_id, title, domain, urgency, type, status,
                     source_text, source_hash, keywords_json, created_at, updated_at)
                VALUES
                    (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(goal_id) DO UPDATE SET
                    title=excluded.title,
                    domain=excluded.domain,
                    urgency=excluded.urgency,
                    type=excluded.type,
                    status=excluded.status,
                    source_text=excluded.source_text,
                    source_hash=excluded.source_hash,
                    keywords_json=excluded.keywords_json,
                    updated_at=excluded.updated_at
                """,
                (
                    record.goal_id,
                    record.title,
                    record.domain,
                    record.urgency,
                    record.type,
                    record.status,
                    record.source_text,
                    record.source_hash,
                    json.dumps(record.keywords),
                    record.created_at,
                    record.updated_at,
                ),
            )

    def get(self, goal_id: str) -> GoalRecord | None:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM goals WHERE goal_id = ?", (goal_id,)
            ).fetchone()
            return self._row_to_record(row) if row else None

    def list_active(self) -> list[GoalRecord]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM goals WHERE status = 'active' ORDER BY updated_at DESC"
            ).fetchall()
            return [self._row_to_record(r) for r in rows]

    def find_by_source_hash(self, source_hash: str) -> list[GoalRecord]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM goals WHERE source_hash = ?", (source_hash,)
            ).fetchall()
            return [self._row_to_record(r) for r in rows]

    def find_by_domain(self, domain: str) -> list[GoalRecord]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM goals WHERE domain = ? AND status = 'active'",
                (domain,),
            ).fetchall()
            return [self._row_to_record(r) for r in rows]

    def find_by_keyword_overlap(self, keywords: list[str]) -> list[GoalRecord]:
        """Naive scan: returns active goals that share at least one keyword."""
        if not keywords:
            return []
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM goals WHERE status = 'active'"
            ).fetchall()
            results: list[tuple[int, GoalRecord]] = []
            kw_set = {k.lower() for k in keywords}
            for row in rows:
                rec = self._row_to_record(row)
                rec_kw = {k.lower() for k in rec.keywords}
                overlap = len(kw_set & rec_kw)
                if overlap > 0:
                    results.append((overlap, rec))
            results.sort(key=lambda x: x[0], reverse=True)
            return [r for _, r in results]

    @staticmethod
    def _row_to_record(row: sqlite3.Row) -> GoalRecord:
        return GoalRecord(
            id=row["id"],
            goal_id=row["goal_id"],
            title=row["title"],
            domain=row["domain"],
            urgency=row["urgency"],
            type=row["type"],
            status=row["status"],
            source_text=row["source_text"],
            source_hash=row["source_hash"],
            keywords=json.loads(row["keywords_json"]),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )


// --- DUPLICATE BLOCK ---

"""
Goal Registry — persistent store of accepted goals for Job-Star.

File-backed (JSON) during bootstrap mode. Designed to be swappable with
a database backend later without changing the public interface.
"""

from __future__ import annotations

import json
import os
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional


@dataclass
class Goal:
    """A single accepted goal in the registry."""
    id: str
    title: str
    description: str
    domain: str
    urgency: str
    goal_type: str
    keywords: List[str] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)
    status: str = "active"
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Goal":
        return cls(**data)


class GoalRegistry:
    """
    File-backed goal registry.

    Layout:
        {
            "goals": [ {Goal}, ... ],
            "version": 1
        }
    """

    def __init__(self, path: str | os.PathLike = "data/goal_registry.json"):
        self.path = Path(path)
        self._goals: Dict[str, Goal] = {}
        self._load()

    # ---- persistence ----

    def _load(self) -> None:
        if not self.path.exists():
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self._save()
            return
        with self.path.open("r", encoding="utf-8") as f:
            data = json.load(f)
        for g in data.get("goals", []):
            goal = Goal.from_dict(g)
            self._goals[goal.id] = goal

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": 1,
            "goals": [g.to_dict() for g in self._goals.values()],
        }
        tmp = self.path.with_suffix(".json.tmp")
        with tmp.open("w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2, ensure_ascii=False)
        tmp.replace(self.path)

    # ---- public API ----

    def add(self, goal: Goal) -> None:
        """Insert or update a goal."""
        self._goals[goal.id] = goal
        self._save()

    def get(self, goal_id: str) -> Optional[Goal]:
        return self._goals.get(goal_id)

    def all_goals(self) -> List[Goal]:
        return list(self._goals.values())

    def active_goals(self) -> List[Goal]:
        return [g for g in self._goals.values() if g.status == "active"]

    def remove(self, goal_id: str) -> bool:
        if goal_id in self._goals:
            del self._goals[goal_id]
            self._save()
            return True
        return False

    def new_id(self) -> str:
        return f"goal-{uuid.uuid4().hex[:12]}"
