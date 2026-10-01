"""async 存储层（SQLAlchemy 2.0 ORM + 内存兜底）。

接口兼容原 Repo（save/load/delete + 新增多级查询），数据访问统一走 ORM；
DB 不可用（settings.persist=False 或连接失败）时降级为进程内 async dict 存储，
保证无库仍可跑 Web/测试闭环。所有方法均 async。
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Optional

from sqlalchemy import func, or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.base import Base
from app.db.engine import get_session_factory
from app.db.models import Belief, Book, BookMemory, ChatHistory, Chapter, Character, Foreshadow, InspirationCard, ProseNote, Scene, Simulation, WorldState
from app.schemas.models import CharacterCard, SimulationState

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------- T9 降级可观测
# 背景：B18（列漂移）/B19（内存态语义）/B21（反序列化失败）/B22（被空局盖掉）都是
# "异常被吞 → 静默落内存态"的同一根因。这里把降级事实集中记账并对外暴露（/health），
# 且**区分两类**：DB 不可达（预期内，兜底可用）与结构性错误（bug，必须报警）。
DEGRADATION: dict = {"count": 0, "structural": 0, "last_reason": "", "last_op": "",
                     "last_at": 0, "kinds": {}}


def note_degradation(e: Exception, op: str = "") -> None:
    """记录一次降级。结构性错误按 ERROR 打日志（不再只 warning 后继续跑）。"""
    import time as _time

    import pydantic
    from sqlalchemy import exc as sa_exc

    structural = isinstance(e, (pydantic.ValidationError, sa_exc.ProgrammingError,
                                sa_exc.IntegrityError))
    DEGRADATION["count"] += 1
    if structural:
        DEGRADATION["structural"] += 1
    reason = "%s: %s" % (type(e).__name__, str(e)[:200])
    DEGRADATION["last_reason"] = reason
    DEGRADATION["last_op"] = op
    DEGRADATION["last_at"] = int(_time.time() * 1000)
    kinds = DEGRADATION["kinds"]
    kinds[type(e).__name__] = kinds.get(type(e).__name__, 0) + 1
    if structural:
        logger.error("[Repo] 结构性错误导致降级（疑似 bug，op=%s）：%s", op, reason)
    else:
        logger.warning("[Repo] 降级内存态（op=%s）：%s", op, reason)


def degradation_status() -> dict:
    """当前降级状态：active=是否发生过；structural 是"疑似 bug"的计数。"""
    return {
        "active": DEGRADATION["count"] > 0,
        "count": DEGRADATION["count"],
        "structural": DEGRADATION["structural"],
        "last_reason": DEGRADATION["last_reason"],
        "last_op": DEGRADATION["last_op"],
        "last_at": DEGRADATION["last_at"],
        "kinds": dict(DEGRADATION["kinds"]),
    }


class Repo:
    """多级存储门面：优先 ORM（异步），内存态兜底。"""

    def __init__(self, session_factory: Any = None, use_db: Optional[bool] = None) -> None:
        self._session_factory = session_factory or get_session_factory()
        self._use_db = settings.persist if use_db is None else use_db
        self._mem: dict[str, Any] = {}          # 内存兜底：id -> 领域对象/dict
        self._lock = asyncio.Lock()

    # ------------------------------------------------------------------
    # 通用：内存态判定（测试可用 use_db=False 强制内存态）
    # ------------------------------------------------------------------
    @property
    def use_db(self) -> bool:
        return self._use_db

    def _set_mem(self, key: str, value: Any) -> None:
        self._mem[key] = value

    def _get_mem(self, key: str) -> Optional[Any]:
        return self._mem.get(key)

    async def _session(self) -> AsyncSession:
        async with self._session_factory() as session:
            yield session

    # ------------------------------------------------------------------
    # Schema 管理（委托 Alembic）
    # ------------------------------------------------------------------
    async def init_schema(self) -> None:
        """执行 Alembic 迁移到 head（幂等）。DB 不可用时静默跳过。

        替代手写 DDL：schema 的唯一权威是 backend/alembic/versions/。
        """
        if not self.use_db:
            return

        try:
            from alembic import command as alembic_cmd
            from alembic.config import Config
            from pathlib import Path

            ini = Path(__file__).resolve().parent.parent.parent / "alembic.ini"
            cfg = Config(str(ini))
            await asyncio.to_thread(alembic_cmd.upgrade, cfg, "head")
            logger.info("[Repo] Alembic 迁移到 head 完成")
        except Exception as e:  # noqa: BLE001
            logger.warning("[Repo] Alembic 迁移失败，走内存态：%s", e)

    # ------------------------------------------------------------------
    # simulation（第四层 · 整份快照）
    # ------------------------------------------------------------------
    async def save(self, sim_id: str, sim: SimulationState) -> None:
        if not self.use_db:
            self._set_mem(sim_id, sim)
            return
        try:
            async with self._session_factory() as session:
                row = await session.get(Simulation, sim_id)
                if row is None:
                    row = Simulation(id=sim_id)
                    session.add(row)
                row.scenario = sim.scenario
                row.book_id = sim.book_id or None
                row.chapter_id = sim.chapter_id or None
                row.scene_id = sim.scene_id or None
                row.turn = sim.world.turn
                row.state_json = sim.model_dump_json()
                row.ended = sim.ended
                await session.commit()
        except Exception as e:  # noqa: BLE001
            logger.warning("[Repo] save 失败，降级内存态：%s", e)
            self._set_mem(sim_id, sim)

    async def load(self, sim_id: str) -> Optional[SimulationState]:
        if not self.use_db:
            return self._get_mem(sim_id)
        try:
            async with self._session_factory() as session:
                row = await session.get(Simulation, sim_id)
                if row is None:
                    return None
                return SimulationState.model_validate_json(row.state_json)
        except Exception as e:  # noqa: BLE001
            logger.warning("[Repo] load 失败，走内存态：%s", e)
            return self._get_mem(sim_id)

    async def delete(self, sim_id: str) -> None:
        self._mem.pop(sim_id, None)
        if not self.use_db:
            return
        try:
            async with self._session_factory() as session:
                row = await session.get(Simulation, sim_id)
                if row is not None:
                    await session.delete(row)
                    await session.commit()
        except Exception as e:  # noqa: BLE001
            logger.warning("[Repo] delete 失败：%s", e)

    async def resume_latest(self, scene_id: str) -> Optional[str]:
        """该场景下「可继续推演」的最新 sim_id（恢复上次工作流）。

        排除：已收敛(converged) / 已完结(ended) —— 它们都是终点，不该被恢复。
        举手暂停等中间态可恢复继续推演。
        ORM 层只取最新 id，完整判定在 service.start 里 load 后判断。
        """
        if not self.use_db:
            # 内存态：找该 scene 未收敛且未完结的最新 sim
            latest = None
            for sim_id, sim in self._mem.items():
                if (getattr(sim, "scene_id", None) == scene_id
                        and not sim.director.converged and not sim.ended):
                    latest = sim_id
            return latest
        try:
            async with self._session_factory() as session:
                stmt = (
                    select(Simulation.id)
                    .where(Simulation.scene_id == scene_id, Simulation.ended.is_(False))
                    .order_by(Simulation.updated_at.desc())
                    .limit(10)
                )
                rows = (await session.execute(stmt)).scalars().all()
                for sid in rows:
                    sim = await self.load(sid)
                    if sim is not None and not sim.director.converged:
                        return sid
                return None
        except Exception as e:  # noqa: BLE001
            logger.warning("[Repo] resume_latest 失败：%s", e)
            return None

    # ------------------------------------------------------------------
    # books（第一层）
    # ------------------------------------------------------------------
    async def list_books(self) -> list[dict]:
        return await self._list_rows(Book, lambda r: {
            "id": r.id, "title": r.title, "genre": r.genre,
            "status": r.status, "cover_init": r.cover_init,
            "chapter_count": r.chapter_count, "created_at": str(r.created_at),
            "synopsis": r.synopsis,
        })

    async def get_book(self, book_id: str) -> Optional[dict]:
        async def _q(session: AsyncSession):
            r = await session.get(Book, book_id)
            if r is None:
                return None
            return {
                "id": r.id, "title": r.title, "genre": r.genre, "status": r.status,
                "cover_init": r.cover_init, "chapter_count": r.chapter_count,
                "synopsis": r.synopsis, "worldview_json": r.worldview_json,
                "world_rules_json": r.world_rules_json,
            }

        if not self.use_db:
            return self._mem.get(book_id)
        return await self._query_or_mem(_q, None)

    async def save_book(self, data: dict) -> None:
        await self._upsert(Book, data)
        # 同步 chapter_count（新建书用，幂等）
        await self._sync_book_chapter_count(str(data.get("id", "")))

    async def _sync_book_chapter_count(self, book_id: str) -> None:
        if not book_id:
            return
        try:
            async with self._session_factory() as session:
                cnt = (
                    await session.execute(
                        select(func.count()).select_from(Chapter).where(Chapter.book_id == book_id)
                    )
                ).scalar_one()
                book = await session.get(Book, book_id)
                if book is not None:
                    book.chapter_count = cnt
                    await session.commit()
        except Exception:  # noqa: BLE001
            pass

    # ------------------------------------------------------------------
    # chapters（第二层）
    # ------------------------------------------------------------------
    async def list_chapters_by_book(self, book_id: str) -> list[dict]:
        if not self.use_db:
            rows = [v for v in self._mem.values()
                    if isinstance(v, dict) and v.get("book_id") == book_id and "order_no" in v]
            return sorted(rows, key=lambda c: c.get("order_no") or 0)

        async def _q(session: AsyncSession):
            stmt = (
                select(Chapter)
                .where(Chapter.book_id == book_id)
                .order_by(Chapter.order_no)
            )
            rows = (await session.execute(stmt)).scalars().all()
            return [{
                "id": r.id, "book_id": r.book_id, "title": r.title,
                "summary": r.summary, "order_no": r.order_no,
                "tone": r.tone, "tension_curve": r.tension_curve, "word_target": r.word_target,
            } for r in rows]

        return await self._query_or_mem(_q, [])

    async def get_chapter(self, chapter_id: str) -> Optional[dict]:
        async def _q(session: AsyncSession):
            r = await session.get(Chapter, chapter_id)
            if r is None:
                return None
            return {
                "id": r.id, "book_id": r.book_id, "title": r.title,
                "summary": r.summary, "order_no": r.order_no,
                "tone": r.tone, "tension_curve": r.tension_curve, "word_target": r.word_target,
            }

        if not self.use_db:
            return self._mem.get(chapter_id)
        return await self._query_or_mem(_q, None)

    async def save_chapter(self, data: dict) -> None:
        await self._upsert(Chapter, data)

    # ------------------------------------------------------------------
    # scenes（第三层）
    # ------------------------------------------------------------------
    async def list_scenes_by_chapter(self, chapter_id: str) -> list[dict]:
        if not self.use_db:
            # 内存态判据用 chapter_id（场景的必填外键），**不再要求 cursor_pos 存在**：
            # 旧写法把 cursor_pos 当类型标记，导致未显式带该键的场景在内存态整体不可见
            # （树的 scenes 空、概览/S3 全局视图丢场景），与 DB 分支语义不一致。
            rows = [v for v in self._mem.values()
                    if isinstance(v, dict) and v.get("chapter_id") == chapter_id
                    and "book_id" not in v]
            return sorted(rows, key=lambda s: s.get("cursor_pos") or 0)

        async def _q(session: AsyncSession):
            stmt = select(Scene).where(Scene.chapter_id == chapter_id).order_by(Scene.cursor_pos)
            rows = (await session.execute(stmt)).scalars().all()
            return [{
                "id": r.id, "chapter_id": r.chapter_id, "title": r.title,
                "scenario_def": r.scenario_def, "cursor_pos": r.cursor_pos,
                "stage_desc": r.stage_desc, "goal": r.goal,
                "content_desc": r.content_desc, "scene_summary": r.scene_summary,
                "final_prose": r.final_prose,
                "created_at": str(r.created_at), "updated_at": str(r.updated_at),
            } for r in rows]

        return await self._query_or_mem(_q, [])

    async def get_scene(self, scene_id: str) -> Optional[dict]:
        async def _q(session: AsyncSession):
            r = await session.get(Scene, scene_id)
            return self._scene_dict(r) if r else None

        if not self.use_db:
            # 内存态：先查 _mem（收束摘要/定稿正文等 upsert 产物），再回退静态场景目录
            return self._mem.get(scene_id) or self._static_scene(scene_id)
        return await self._query_or_mem(_q, None)

    def _static_scene(self, scene_id: str) -> Optional[dict]:
        """静态场景兜底（内存态）：按 id 匹配 betrayal_night 目录。"""
        if scene_id != "scene-betrayal-night" and scene_id != "betrayal_night":
            return None
        import json as _json

        from app.scenarios.betrayal_night import betrayal_night as _spec

        spec = _spec()
        facts = [
            {"id": f.id, "text": f.text, "kind": f.kind,
             "visible_to": f.visible_to, "created_event_id": f.created_event_id, "active": f.active}
            for f in spec["initial_facts"]
        ]
        cfg = spec["plan_cfg"]
        plan = {
            "secret_fact_ids": list(cfg.secret_fact_ids),
            "expose_target": dict(cfg.expose_target),
            "goal_pressure": [dict(p) for p in cfg.goal_pressure],
            "env_pressure_lines": list(cfg.env_pressure_lines),
            "raise_after_turn": cfg.raise_after_turn,
            "ending_options": list(cfg.ending_options),
        }
        return {
            "id": scene_id, "chapter_id": "chapter-01", "title": "书房夜谈 · 雨",
            "scenario_def": "betrayal_night",
            "initial_facts_json": _json.dumps(facts, ensure_ascii=False),
            "plan_cfg_json": _json.dumps(plan, ensure_ascii=False),
            "cursor_pos": 0,
            "stage_desc": "", "scene_summary": "", "final_prose": "",
        }

    async def save_scene(self, data: dict) -> None:
        await self._upsert(Scene, data)

    def _scene_dict(self, r: Scene) -> dict:
        return {
            "id": r.id, "chapter_id": r.chapter_id, "title": r.title,
            "scenario_def": r.scenario_def,
            "initial_facts_json": r.initial_facts_json,
            "plan_cfg_json": r.plan_cfg_json, "cursor_pos": r.cursor_pos,
            "stage_desc": r.stage_desc, "goal": r.goal,
            "content_desc": r.content_desc, "scene_summary": r.scene_summary,
            "final_prose": r.final_prose,
        }

    # ------------------------------------------------------------------
    # foreshadows（伏笔账本 · Book 级三态）
    # ------------------------------------------------------------------
    async def list_foreshadows(self, book_id: str) -> list[dict]:
        async def _q(session: AsyncSession):
            stmt = select(Foreshadow).where(Foreshadow.book_id == book_id).order_by(Foreshadow.created_at)
            rows = (await session.execute(stmt)).scalars().all()
            return [self._foreshadow_dict(r) for r in rows]

        if not self.use_db:
            # 内存态：按 book_id + 特征键（status+text 伏笔独有：belief 无 status、note 无 text 键）
            return [v for v in self._mem.values()
                    if isinstance(v, dict) and "status" in v and "text" in v
                    and v.get("book_id") == book_id]
        return await self._query_or_mem(_q, [])

    async def get_foreshadow(self, fid: str) -> Optional[dict]:
        async def _q(session: AsyncSession):
            r = await session.get(Foreshadow, fid)
            return self._foreshadow_dict(r) if r else None

        return await self._query_or_mem(_q, None)

    async def save_foreshadow(self, data: dict) -> None:
        await self._upsert(Foreshadow, data)

    async def delete_foreshadow(self, fid: str) -> None:
        self._mem.pop(fid, None)
        try:
            async with self._session_factory() as session:
                row = await session.get(Foreshadow, fid)
                if row is not None:
                    await session.delete(row)
                    await session.commit()
        except Exception:  # noqa: BLE001
            pass

    @staticmethod
    def _foreshadow_dict(r: Foreshadow) -> dict:
        return {
            "id": r.id, "book_id": r.book_id, "type": r.type, "text": r.text,
            "status": r.status, "buried_scene": r.buried_scene,
            "expected_close_scene": r.expected_close_scene,
            "related_char_ids": r.related_char_ids, "notes": r.notes,
            "closed_at": str(r.closed_at) if r.closed_at else "",
            "created_at": str(r.created_at),
        }

    # ------------------------------------------------------------------
    # beliefs（信念账本 · 书级实体，sim 每回合同步 upsert）
    # ------------------------------------------------------------------
    async def list_beliefs(self, book_id: str, char_id: Optional[str] = None,
                           channel: Optional[str] = None) -> list[dict]:
        async def _q(session: AsyncSession):
            stmt = select(Belief).where(Belief.book_id == book_id)
            if char_id:
                stmt = stmt.where(Belief.char_id == char_id)
            if channel:
                stmt = stmt.where(Belief.channel == channel)
            stmt = stmt.order_by(Belief.created_at.desc())
            rows = (await session.execute(stmt)).scalars().all()
            return [self._belief_dict(r) for r in rows]

        if not self.use_db:
            rows = [
                v for v in self._mem.values()
                if isinstance(v, dict) and "char_id" in v and v.get("book_id") == book_id
                and (not char_id or v.get("char_id") == char_id)
                and (not channel or v.get("channel") == channel)
            ]
            rows.sort(key=lambda x: x.get("created_at", ""), reverse=True)
            return rows
        return await self._query_or_mem(_q, [])

    async def get_belief(self, belief_id: str) -> Optional[dict]:
        if not self.use_db:
            v = self._mem.get(belief_id)
            return v if isinstance(v, dict) else None

        async def _q(session: AsyncSession):
            r = await session.get(Belief, belief_id)
            return self._belief_dict(r) if r else None

        return await self._query_or_mem(_q, None)

    async def save_belief(self, data: dict) -> None:
        await self._upsert(Belief, data)

    async def delete_belief(self, belief_id: str) -> None:
        self._mem.pop(belief_id, None)
        if not self.use_db:
            return
        try:
            async with self._session_factory() as session:
                row = await session.get(Belief, belief_id)
                if row is not None:
                    await session.delete(row)
                    await session.commit()
        except Exception as e:  # noqa: BLE001
            logger.warning("[Repo] delete belief %s 失败：%s", belief_id, e)

    @staticmethod
    def _belief_dict(r: Belief) -> dict:
        return {
            "id": r.id, "book_id": r.book_id, "char_id": r.char_id,
            "fact_id": r.fact_id, "source_event_id": r.source_event_id,
            "channel": r.channel, "text": r.text, "confidence": r.confidence,
            "edited": bool(r.edited), "ts": r.ts,
            "created_at": str(r.created_at), "updated_at": str(r.updated_at),
        }

    # ------------------------------------------------------------------
    # world states（世界状态账本 · S1 优化 · 书级实体）
    # ------------------------------------------------------------------
    async def list_world_states(self, book_id: str, kind: Optional[str] = None,
                                name: Optional[str] = None) -> list[dict]:
        """按 book_id 取世界状态，可过滤 kind/name（用于感知注入裁剪）。"""
        async def _q(session: AsyncSession):
            stmt = select(WorldState).where(WorldState.book_id == book_id)
            if kind:
                stmt = stmt.where(WorldState.kind == kind)
            if name:
                stmt = stmt.where(WorldState.name == name)
            stmt = stmt.order_by(WorldState.kind, WorldState.name)
            rows = (await session.execute(stmt)).scalars().all()
            return [self._world_state_dict(r) for r in rows]

        if not self.use_db:
            rows = [
                v for v in self._mem.values()
                if isinstance(v, dict) and "kind" in v and v.get("book_id") == book_id
                and (not kind or v.get("kind") == kind)
                and (not name or v.get("name") == name)
            ]
            rows.sort(key=lambda x: (x.get("kind", ""), x.get("name", "")))
            return rows
        return await self._query_or_mem(_q, [])

    async def list_world_states_by_key(self, book_id: str, keys: list[str]) -> list[dict]:
        """按语义键列表精确取状态（写手感知注入"只喂相关"，避免全量）。
        内存态路径与 DB 路径一致：DB 用 key IN，内存态用 key 过滤。"""
        if not keys:
            return []
        async def _q(session: AsyncSession):
            stmt = select(WorldState).where(
                WorldState.book_id == book_id, WorldState.key.in_(keys)
            )
            rows = (await session.execute(stmt)).scalars().all()
            return [self._world_state_dict(r) for r in rows]

        if not self.use_db:
            key_set = set(keys)
            return [
                v for v in self._mem.values()
                if isinstance(v, dict) and "key" in v and v.get("book_id") == book_id
                and v.get("key") in key_set
            ]
        return await self._query_or_mem(_q, [])

    async def get_world_state(self, ws_id: str) -> Optional[dict]:
        if not self.use_db:
            v = self._mem.get(ws_id)
            return v if isinstance(v, dict) else None

        async def _q(session: AsyncSession):
            r = await session.get(WorldState, ws_id)
            return self._world_state_dict(r) if r else None

        return await self._query_or_mem(_q, None)

    async def save_world_state(self, data: dict) -> None:
        await self._upsert(WorldState, data)

    async def delete_world_state(self, ws_id: str) -> None:
        self._mem.pop(ws_id, None)
        if not self.use_db:
            return
        try:
            async with self._session_factory() as session:
                row = await session.get(WorldState, ws_id)
                if row is not None:
                    await session.delete(row)
                    await session.commit()
        except Exception as e:  # noqa: BLE001
            logger.warning("[Repo] delete world_state %s 失败：%s", ws_id, e)

    @staticmethod
    def _world_state_dict(r: WorldState) -> dict:
        return {
            "id": r.id, "book_id": r.book_id, "kind": r.kind, "name": r.name,
            "key": r.key, "value": r.value, "scene_no": r.scene_no,
            "source_event_id": r.source_event_id, "previous_value": r.previous_value,
            "edited": bool(r.edited), "ts": r.ts,
            "created_at": str(r.created_at), "updated_at": str(r.updated_at),
        }

    # ------------------------------------------------------------------
    # book memories（书级记忆 · 主笔 Agent 记忆域）
    # ------------------------------------------------------------------
    async def list_memories(self, book_id: str, topic: Optional[str] = None,
                            limit: int = 20) -> list[dict]:
        """按 book_id 取记忆，可过滤 topic，ts 倒序（最近在前）。"""
        async def _q(session: AsyncSession):
            stmt = select(BookMemory).where(BookMemory.book_id == book_id)
            if topic:
                stmt = stmt.where(BookMemory.topic == topic)
            stmt = stmt.order_by(BookMemory.ts.desc(), BookMemory.created_at.desc()).limit(limit)
            rows = (await session.execute(stmt)).scalars().all()
            return [self._memory_dict(r) for r in rows]

        if not self.use_db:
            rows = [
                v for v in self._mem.values()
                if isinstance(v, dict) and "topic" in v and v.get("book_id") == book_id
                and (not topic or v.get("topic") == topic)
            ]
            rows.sort(key=lambda x: x.get("ts") or 0, reverse=True)
            return rows[:limit]
        return await self._query_or_mem(_q, [])

    async def save_memory(self, data: dict) -> None:
        await self._upsert(BookMemory, data)

    async def delete_memory(self, memory_id: str) -> None:
        self._mem.pop(memory_id, None)
        if not self.use_db:
            return
        try:
            async with self._session_factory() as session:
                row = await session.get(BookMemory, memory_id)
                if row is not None:
                    await session.delete(row)
                    await session.commit()
        except Exception as e:  # noqa: BLE001
            logger.warning("[Repo] delete memory %s 失败：%s", memory_id, e)

    @staticmethod
    def _memory_dict(r: BookMemory) -> dict:
        return {
            "id": r.id, "book_id": r.book_id, "topic": r.topic,
            "content": r.content, "source": r.source, "ts": r.ts,
            "created_at": str(r.created_at), "updated_at": str(r.updated_at),
        }

    # ------------------------------------------------------------------
    # chat_histories（主笔共创对话历史 · 阶段⑧ 按书隔离）
    # ------------------------------------------------------------------
    async def list_chat_histories(self, book_id: str, limit: int = 200) -> list[dict]:
        """按 book_id 取对话历史，ts 正序（旧在前，对话流顺序）。"""
        async def _q(session: AsyncSession):
            stmt = (
                select(ChatHistory).where(ChatHistory.book_id == book_id)
                .order_by(ChatHistory.ts.asc(), ChatHistory.created_at.asc()).limit(limit)
            )
            rows = (await session.execute(stmt)).scalars().all()
            return [self._chat_history_dict(r) for r in rows]

        if not self.use_db:
            rows = [
                v for v in self._mem.values()
                if isinstance(v, dict) and v.get("book_id") == book_id
            ]
            rows.sort(key=lambda x: x.get("ts") or 0)
            return rows[:limit]
        return await self._query_or_mem(_q, [])

    async def append_chat_histories(self, book_id: str, messages: list[dict]) -> None:
        """追加对话消息（不删旧数据，幂等：同 id 跳过）。"""
        if not self.use_db:
            for m in messages:
                self._mem[m["id"]] = {**m, "book_id": book_id}
            return
        try:
            async with self._session_factory() as session:
                for m in messages:
                    # 幂等：已存在则跳过
                    existing = await session.get(ChatHistory, m["id"])
                    if existing is not None:
                        continue
                    row = ChatHistory(
                        id=m["id"], book_id=book_id,
                        role=m.get("role", "user"),
                        content=m.get("content", ""),
                        ts=m.get("ts", 0),
                    )
                    session.add(row)
                await session.commit()
        except Exception as e:  # noqa: BLE001
            logger.warning("[Repo] append_chat_histories 失败：%s", e)

    @staticmethod
    def _chat_history_dict(r: ChatHistory) -> dict:
        return {
            "id": r.id, "book_id": r.book_id, "role": r.role,
            "content": r.content, "ts": r.ts,
            "created_at": str(r.created_at),
        }

    # ------------------------------------------------------------------
    # prose notes（正文审计记录 · 阶段 协作工作区）
    # ------------------------------------------------------------------
    async def list_prose_notes(self, scene_id: str, limit: int = 100) -> list[dict]:
        """场景正文审计记录，ts 倒序（新在前）。"""
        async def _q(session: AsyncSession):
            stmt = (
                select(ProseNote).where(ProseNote.scene_id == scene_id)
                .order_by(ProseNote.ts.desc(), ProseNote.created_at.desc()).limit(limit)
            )
            rows = (await session.execute(stmt)).scalars().all()
            return [self._prose_note_dict(r) for r in rows]

        if not self.use_db:
            rows = [
                v for v in self._mem.values()
                if isinstance(v, dict) and v.get("scene_id") == scene_id
            ]
            rows.sort(key=lambda x: x.get("ts") or 0, reverse=True)
            return rows[:limit]
        return await self._query_or_mem(_q, [])

    async def save_prose_note(self, data: dict) -> None:
        await self._upsert(ProseNote, data)

    async def get_prose_note(self, note_id: str) -> Optional[dict]:
        if not self.use_db:
            v = self._mem.get(note_id)
            return v if isinstance(v, dict) else None

        async def _q(session: AsyncSession):
            r = await session.get(ProseNote, note_id)
            return self._prose_note_dict(r) if r else None

        return await self._query_or_mem(_q, None)

    @staticmethod
    def _prose_note_dict(r: ProseNote) -> dict:
        return {
            "id": r.id, "scene_id": r.scene_id, "kind": r.kind,
            "status": r.status, "suggestion": r.suggestion,
            "before": r.before, "after": r.after, "created_by": r.created_by,
            "reviewed_at": str(r.reviewed_at) if r.reviewed_at else None,
            "payload_json": r.payload_json, "ts": r.ts,
            "created_at": str(r.created_at), "updated_at": str(r.updated_at),
        }

    # ------------------------------------------------------------------
    # simulations（Dashboard「导演中」判定）
    # ------------------------------------------------------------------
    async def list_active_sims_by_book(self, book_id: str) -> list[dict]:
        """该书未完结(ended=False)的 sim（时间倒序）：[{id, scene_id}]，供「导演中」判定。"""
        async def _q(session: AsyncSession):
            stmt = (
                select(Simulation.id, Simulation.scene_id)
                .where(Simulation.book_id == book_id, Simulation.ended.is_(False))
                .order_by(Simulation.updated_at.desc())
            )
            return [{"id": sid, "scene_id": scene_id}
                    for sid, scene_id in (await session.execute(stmt)).all()]

        if not self.use_db:
            return [
                {"id": sid, "scene_id": getattr(sim, "scene_id", None)}
                for sid, sim in self._mem.items()
                if getattr(sim, "book_id", None) == book_id and not getattr(sim, "ended", True)
            ]
        return await self._query_or_mem(_q, [])

    async def list_sims_by_book(self, book_id: str) -> list[dict]:
        """该书**全部** sim（含已完结 · 时间倒序）：[{id, scene_id, ended}]。

        与 list_active_sims_by_book 的区别：不过滤 ended —— S3 全局张力要读
        **已完成场景**的回合归档（张力藏在 state_json.turn_archives 里）。
        """
        async def _q(session: AsyncSession):
            stmt = (
                select(Simulation.id, Simulation.scene_id, Simulation.ended)
                .where(Simulation.book_id == book_id)
                .order_by(Simulation.updated_at.desc())
            )
            return [{"id": sid, "scene_id": scene_id, "ended": bool(ended)}
                    for sid, scene_id, ended in (await session.execute(stmt)).all()]

        if not self.use_db:
            # 内存态用**插入顺序的逆序**模拟 DB 的 updated_at DESC（内存保存=时间序），
            # 否则"每场景取最新 sim"会取到旧局（旧局张力作废语义失效）。
            rows = [
                {"id": sid, "scene_id": getattr(sim, "scene_id", None),
                 "ended": bool(getattr(sim, "ended", True))}
                for sid, sim in self._mem.items()
                if getattr(sim, "book_id", None) == book_id
            ]
            return list(reversed(rows))
        return await self._query_or_mem(_q, [])

    # ------------------------------------------------------------------
    # characters（角色卡 · 一等实体 · 书级 + 场景特设两层）
    # ------------------------------------------------------------------
    async def list_characters_by_book(self, book_id: str) -> list[dict]:
        """书级角色（scene_id 为空）—— 人物页「角色库」数据源。"""
        async def _q(session: AsyncSession):
            stmt = (
                select(Character)
                .where(Character.book_id == book_id, Character.scene_id.is_(None))
                .order_by(Character.id)
            )
            rows = (await session.execute(stmt)).scalars().all()
            return [self._char_dict(r) for r in rows]

        if not self.use_db:
            return [self._char_mem(v) for v in self._mem.values()
                    if isinstance(v, dict) and "name" in v
                    and v.get("book_id") == book_id and not v.get("scene_id")]
        return await self._query_or_mem(_q, [])

    async def list_characters_for_scene(self, scene_id: str) -> list[dict]:
        """场景装配数据源 = 本书书级角色 + 该场景特设角色（sim 启动/场景详情用）。"""
        async def _q(session: AsyncSession):
            sc = await session.get(Scene, scene_id)
            if sc is None:
                return []
            book_id = sc.chapter_id and (await session.get(Chapter, sc.chapter_id)).book_id if sc.chapter_id else None
            stmt = select(Character).where(
                Character.book_id == book_id,  # type: ignore[arg-type]
                or_(Character.scene_id.is_(None), Character.scene_id == scene_id),
            ).order_by(Character.id)
            rows = (await session.execute(stmt)).scalars().all()
            return [self._char_dict(r) for r in rows]

        if not self.use_db:
            # 内存态：查 mem 里归属该场景（书级或特设）的角色
            scene_row = self._mem.get(scene_id) or self._static_scene(scene_id) or {}
            book_id = scene_row.get("book_id") or (self._mem.get(scene_row.get("chapter_id", "")) or {}).get("book_id", "")
            return [self._char_mem(v) for v in self._mem.values()
                    if isinstance(v, dict)
                    and (v.get("scene_id") == scene_id
                         or (v.get("book_id") == book_id and not v.get("scene_id")))]
        return await self._query_or_mem(_q, [])

    async def list_characters_by_scene(self, scene_id: str) -> list[dict]:
        """兼容别名：= list_characters_for_scene（旧调用方/测试）。"""
        return await self.list_characters_for_scene(scene_id)

    async def get_character(self, char_id: str) -> Optional[dict]:
        async def _q(session: AsyncSession):
            r = await session.get(Character, char_id)
            return self._char_dict(r) if r else None

        return await self._query_or_mem(_q, None)

    async def save_character(self, data: dict) -> None:
        await self._upsert(Character, data)

    @staticmethod
    def _char_dict(r: Character) -> dict:
        return {"id": r.id, "book_id": r.book_id, "scene_id": r.scene_id,
                "name": r.name, "spec_json": r.spec_json}

    @staticmethod
    def _char_mem(v: dict) -> dict:
        return {"id": v.get("id"), "book_id": v.get("book_id"), "scene_id": v.get("scene_id"),
                "name": v.get("name"), "spec_json": v.get("spec_json", "{}")}

    # ------------------------------------------------------------------
    # 树（书 → 章 → 场景 → 角色 一次拉全，防客户端 N+1）
    # ------------------------------------------------------------------
    async def get_book_tree(self, book_id: str) -> Optional[dict]:
        if not self.use_db:
            # 内存态：从 _mem 聚合 book/chapter/scene/character dict（结构对齐 ORM 分支）
            book = self._mem.get(book_id)
            if not isinstance(book, dict):
                return None
            chapters = [
                v for v in self._mem.values()
                if isinstance(v, dict) and v.get("book_id") == book_id and "order_no" in v
            ]
            chapters.sort(key=lambda c: c.get("order_no") or 0)
            # 同 list_scenes_by_chapter：场景判据用 chapter_id，不用 cursor_pos（脆弱）
            scenes = [v for v in self._mem.values()
                      if isinstance(v, dict) and v.get("chapter_id") and "title" in v
                      and "book_id" not in v]
            scenes.sort(key=lambda s: s.get("cursor_pos") or 0)
            scenes_by_ch: dict[str, list[dict]] = {}
            for s in scenes:
                scenes_by_ch.setdefault(s.get("chapter_id", ""), []).append({
                    "id": s.get("id"), "title": s.get("title"),
                    "scenario_def": s.get("scenario_def", "betrayal_night"),
                    "cursor_pos": s.get("cursor_pos", 0),
                    "stage_desc": s.get("stage_desc") or "",
                    "goal": s.get("goal") or "",
                    "content_desc": s.get("content_desc") or "",
                    "scene_summary": s.get("scene_summary") or "",
                })
            chars_by_scene: dict[str, list[dict]] = {}
            for ch in self._mem.values():
                if not (isinstance(ch, dict) and "spec_json" in ch and ch.get("scene_id")):
                    continue
                chars_by_scene.setdefault(ch["scene_id"], []).append({
                    "id": ch.get("id"), "name": ch.get("name"),
                    "spec": json.loads(ch.get("spec_json") or "{}"),
                })
            chapter_list: list[dict] = []
            for c in chapters:
                scene_list = scenes_by_ch.get(c.get("id", ""), [])
                for s in scene_list:
                    s["characters"] = chars_by_scene.get(s["id"], [])
                chapter_list.append({
                    "id": c.get("id"), "title": c.get("title"),
                    "summary": c.get("summary") or "",
                    "order_no": c.get("order_no", 0), "tone": c.get("tone") or "",
                    "tension_curve": c.get("tension_curve") or "",
                    "word_target": c.get("word_target") or 0,
                    "scenes": scene_list,
                })
            return {
                "id": book.get("id"), "title": book.get("title"),
                "genre": book.get("genre") or "", "status": book.get("status") or "",
                "cover_init": book.get("cover_init") or "墨",
                "chapter_count": book.get("chapter_count") or 0,
                "synopsis": book.get("synopsis") or "",
                "worldview_json": book.get("worldview_json") or "{}",
                "world_rules_json": book.get("world_rules_json") or "[]",
                "chapters": chapter_list,
            }

        async def _q(session: AsyncSession):
            book = await session.get(Book, book_id)
            if book is None:
                return None
            chapters = (
                (await session.execute(
                    select(Chapter).where(Chapter.book_id == book_id).order_by(Chapter.order_no)
                )).scalars().all()
            )
            all_scenes = (
                (await session.execute(select(Scene))).scalars().all()
                if chapters else []
            )
            scenes_by_ch: dict[str, list[dict]] = {}
            for sc in all_scenes:
                scenes_by_ch.setdefault(sc.chapter_id, []).append({
                    "id": sc.id, "title": sc.title, "scenario_def": sc.scenario_def,
                    "cursor_pos": sc.cursor_pos, "stage_desc": sc.stage_desc,
                    "goal": sc.goal, "content_desc": sc.content_desc,
                    "scene_summary": sc.scene_summary,
                })
            all_chars = (
                (await session.execute(select(Character))).scalars().all()
                if chapters else []
            )
            # 场景内联只放「特设角色」；书级角色由 books/{id}/characters 单独拉取（避免每场景重复）
            chars_by_scene: dict[str, list[dict]] = {}
            for ch in all_chars:
                if not ch.scene_id:
                    continue
                chars_by_scene.setdefault(ch.scene_id, []).append({
                    "id": ch.id, "name": ch.name, "spec": json.loads(ch.spec_json or "{}"),
                })
            chapter_list: list[dict] = []
            for c in chapters:
                scene_list = scenes_by_ch.get(c.id, [])
                for s in scene_list:
                    s["characters"] = chars_by_scene.get(s["id"], [])
                chapter_list.append({
                    "id": c.id, "title": c.title, "summary": c.summary,
                    "order_no": c.order_no, "tone": c.tone,
                    "tension_curve": c.tension_curve, "word_target": c.word_target,
                    "scenes": scene_list,
                })
            return {
                "id": book.id, "title": book.title, "genre": book.genre,
                "status": book.status, "cover_init": book.cover_init,
                "chapter_count": book.chapter_count,
                "synopsis": book.synopsis, "worldview_json": book.worldview_json,
                "world_rules_json": book.world_rules_json,
                "chapters": chapter_list,
            }

        return await self._query_or_mem(_q, None)

    # ------------------------------------------------------------------
    # 删除（四层级联：ORM cascade 已配；sim 外键 nullable 不清）
    # ------------------------------------------------------------------
    async def delete_book(self, book_id: str) -> None:
        await self._delete_row(Book, book_id)

    async def delete_chapter(self, chapter_id: str) -> None:
        ch = await self.get_chapter(chapter_id)
        book_id = ch["book_id"] if ch else ""
        await self._delete_row(Chapter, chapter_id)
        if book_id:
            await self._sync_book_chapter_count(book_id)

    async def delete_scene(self, scene_id: str) -> None:
        await self._delete_row(Scene, scene_id)

    async def delete_character(self, char_id: str) -> None:
        await self._delete_row(Character, char_id)

    async def _delete_row(self, model, pk: str) -> None:
        self._mem.pop(pk, None)
        if not self.use_db:
            return
        try:
            async with self._session_factory() as session:
                row = await session.get(model, pk)
                if row is not None:
                    await session.delete(row)
                    await session.commit()
        except Exception as e:  # noqa: BLE001
            logger.warning("[Repo] delete %s 失败：%s", pk, e)

    # ------------------------------------------------------------------
    # inspirations（灵感卡 · Book 级主笔共创资产）
    # ------------------------------------------------------------------
    async def list_inspirations(self, book_id: str) -> list[dict]:
        async def _q(session: AsyncSession):
            stmt = (
                select(InspirationCard)
                .where(InspirationCard.book_id == book_id)
                .order_by(InspirationCard.sort_order, InspirationCard.created_at)
            )
            rows = (await session.execute(stmt)).scalars().all()
            return [self._inspiration_dict(r) for r in rows]

        if not self.use_db:
            return [v for v in self._mem.values()
                    if getattr(v, "get", lambda k: None)("book_id") == book_id]
        return await self._query_or_mem(_q, [])

    async def get_inspiration(self, card_id: str) -> Optional[dict]:
        async def _q(session: AsyncSession):
            r = await session.get(InspirationCard, card_id)
            return self._inspiration_dict(r) if r else None

        if not self.use_db:
            return self._mem.get(card_id)
        return await self._query_or_mem(_q, None)

    async def save_inspiration(self, data: dict) -> None:
        await self._upsert(InspirationCard, data)

    async def delete_inspiration(self, card_id: str) -> None:
        self._mem.pop(card_id, None)
        if not self.use_db:
            return
        try:
            async with self._session_factory() as session:
                row = await session.get(InspirationCard, card_id)
                if row is not None:
                    await session.delete(row)
                    await session.commit()
        except Exception as e:  # noqa: BLE001
            logger.warning("[Repo] delete inspiration %s 失败：%s", card_id, e)

    async def update_inspiration_adopted(self, card_id: str, adopted: bool) -> bool:
        """只更新 adopted 字段；返回是否命中。"""
        cur = await self.get_inspiration(card_id)
        if cur is None:
            return False
        await self._upsert(InspirationCard, {**cur, "adopted": adopted})
        return True

    @staticmethod
    def _inspiration_dict(r: InspirationCard) -> dict:
        return {
            "id": r.id, "book_id": r.book_id, "icon": r.icon, "title": r.title,
            "desc": r.desc, "type": r.type, "source": r.source,
            "adopted": bool(r.adopted), "sort_order": r.sort_order,
        }

    # ------------------------------------------------------------------
    # 通用 ORM 辅助
    # ------------------------------------------------------------------
    async def _query_or_mem(self, q, fallback: Any) -> Any:
        if not self.use_db:
            return fallback
        try:
            async with self._session_factory() as session:
                return await q(session)
        except Exception as e:  # noqa: BLE001
            note_degradation(e, "query")     # T9：降级必须被记录（不再纯静默）
            return fallback

    async def _list_rows(self, model, mapper) -> list[dict]:
        async def _q(session: AsyncSession):
            stmt = select(model).order_by(model.id)
            rows = (await session.execute(stmt)).scalars().all()
            return [mapper(r) for r in rows]

        return await self._query_or_mem(_q, [])

    async def _upsert(self, model, data: dict) -> None:
        if not self.use_db:
            # 内存态：并入已有字段（部分更新不丢其他列）
            cur = self._mem.get(data.get("id"))
            if isinstance(cur, dict):
                self._mem[data.get("id")] = {**cur, **data}
            else:
                self._mem[data.get("id")] = data
            return
        try:
            async with self._session_factory() as session:
                pk = data.get("id")
                row = await session.get(model, pk)
                if row is None:
                    row = model(id=pk)
                    session.add(row)
                for k, v in data.items():
                    if k != "id" and hasattr(row, k):
                        setattr(row, k, v)
                await session.commit()
        except Exception as e:  # noqa: BLE001
            logger.warning("[Repo] upsert 失败，降级内存态：%s", e)
            self._mem[data.get("id")] = data