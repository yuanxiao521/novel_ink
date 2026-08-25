"""数据层 · Repository：SimulationState / Events / Beliefs 持久化。

表结构（SQLite 风格但跑在 Postgres）：
  simulation(id PK, scenario, turn, state_json, created_at)   # 整份黑板快照 append
  events(id PK, sim_id FK, seq, turn, type, source, actor, payload_json, guard_flags, ts)
  beliefs(id PK, sim_id FK, char_id, fact_id, source_event_id, channel, text, confidence, ts)

约定：以"整份快照 + 事件日志"双写；simulation 存最新 state_json（回放用 events）。
"""
from __future__ import annotations

import json
from typing import Optional

from app.schemas.models import SimulationState

DDL = """
CREATE TABLE IF NOT EXISTS simulation (
  id TEXT PRIMARY KEY,
  scenario TEXT NOT NULL,
  turn INTEGER NOT NULL DEFAULT 0,
  state_json TEXT NOT NULL,
  created_at TIMESTAMPTZ DEFAULT now()
);
CREATE TABLE IF NOT EXISTS events (
  sim_id TEXT NOT NULL,
  seq INTEGER NOT NULL,
  turn INTEGER NOT NULL,
  type TEXT, source TEXT, actor TEXT,
  payload_json TEXT, guard_flags_json TEXT, ts INTEGER,
  PRIMARY KEY (sim_id, seq)
);
CREATE TABLE IF NOT EXISTS beliefs (
  sim_id TEXT NOT NULL,
  char_id TEXT NOT NULL, fact_id TEXT, source_event_id TEXT,
  channel TEXT, text TEXT, confidence REAL, ts INTEGER,
  PRIMARY KEY (sim_id, char_id, fact_id, source_event_id, ts)
);
"""

# 内存态回退：DB 未就绪时用 dict 顶住，service 层仍可 start/step
_memory: dict[str, SimulationState] = {}


class Repo:
    def __init__(self, db):
        self.db = db

    @property
    def ready(self) -> bool:
        return bool(self.db) and getattr(self.db, "ready", False)

    def init_schema(self) -> None:
        if not self.ready:
            return
        with self.db.cursor() as cur:
            if cur:
                cur.execute(DDL)
                cur.connection.commit()

    # ---- simulation（最新快照） ----
    def save(self, sim_id: str, sim: SimulationState) -> None:
        if not self.ready:
            _memory[sim_id] = sim
            return
        with self.db.cursor() as cur:
            if cur:
                cur.execute(
                    """INSERT INTO simulation(id, scenario, turn, state_json)
                       VALUES(%s,%s,%s,%s)
                       ON CONFLICT(id) DO UPDATE SET turn=EXCLUDED.turn, state_json=EXCLUDED.state_json""",
                    (sim_id, sim.scenario, sim.world.turn, sim.model_dump_json()),
                )
                cur.connection.commit()

    def load(self, sim_id: str) -> Optional[SimulationState]:
        if not self.ready:
            return _memory.get(sim_id)
        with self.db.cursor() as cur:
            if cur:
                cur.execute("SELECT state_json FROM simulation WHERE id=%s", (sim_id,))
                row = cur.fetchone()
                if row:
                    return SimulationState.model_validate_json(row["state_json"])
        return None

    def delete(self, sim_id: str) -> None:
        _memory.pop(sim_id, None)
        if self.ready:
            with self.db.cursor() as cur:
                if cur:
                    cur.execute("DELETE FROM events WHERE sim_id=%s", (sim_id,))
                    cur.execute("DELETE FROM beliefs WHERE sim_id=%s", (sim_id,))
                    cur.execute("DELETE FROM simulation WHERE id=%s", (sim_id,))
                    cur.connection.commit()