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

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.base import Base
from app.db.engine import get_session_factory
from app.db.models import Book, Chapter, Character, Foreshadow, InspirationCard, Scene, Simulation
from app.schemas.models import CharacterCard, SimulationState

logger = logging.getLogger(__name__)


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

        return await self._query_or_mem(_q, None)

    async def save_chapter(self, data: dict) -> None:
        await self._upsert(Chapter, data)

    # ------------------------------------------------------------------
    # scenes（第三层）
    # ------------------------------------------------------------------
    async def list_scenes_by_chapter(self, chapter_id: str) -> list[dict]:
        async def _q(session: AsyncSession):
            stmt = select(Scene).where(Scene.chapter_id == chapter_id).order_by(Scene.cursor_pos)
            rows = (await session.execute(stmt)).scalars().all()
            return [{
                "id": r.id, "chapter_id": r.chapter_id, "title": r.title,
                "scenario_def": r.scenario_def, "cursor_pos": r.cursor_pos,
                "stage_desc": r.stage_desc, "scene_summary": r.scene_summary,
            } for r in rows]

        return await self._query_or_mem(_q, [])

    async def get_scene(self, scene_id: str) -> Optional[dict]:
        async def _q(session: AsyncSession):
            r = await session.get(Scene, scene_id)
            return self._scene_dict(r) if r else None

        if not self.use_db:
            # 内存态：回退静态场景目录（保证无 DB 也能 start/step）
            return self._static_scene(scene_id)
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
        }

    async def save_scene(self, data: dict) -> None:
        await self._upsert(Scene, data)

    def _scene_dict(self, r: Scene) -> dict:
        return {
            "id": r.id, "chapter_id": r.chapter_id, "title": r.title,
            "scenario_def": r.scenario_def,
            "initial_facts_json": r.initial_facts_json,
            "plan_cfg_json": r.plan_cfg_json, "cursor_pos": r.cursor_pos,
            "stage_desc": r.stage_desc, "scene_summary": r.scene_summary,
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
            # 内存态：按 book_id 过滤
            return [v for v in self._mem.values()
                    if getattr(v, "get", lambda k: None)("book_id") == book_id]
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
    # characters（角色卡 · 一等实体）
    # ------------------------------------------------------------------
    async def list_characters_by_scene(self, scene_id: str) -> list[dict]:
        async def _q(session: AsyncSession):
            stmt = select(Character).where(Character.scene_id == scene_id)
            rows = (await session.execute(stmt)).scalars().all()
            return [self._char_dict(r) for r in rows]

        if not self.use_db:
            # 内存态：回退静态角色卡目录
            from app.scenarios.betrayal_night import betrayal_night as _spec

            spec = _spec()
            return [
                {"id": cid, "scene_id": scene_id, "name": card.name,
                 "spec_json": card.model_dump_json()}
                for cid, card in spec["characters"].items()
            ]
        return await self._query_or_mem(_q, [])

    async def get_character(self, char_id: str) -> Optional[dict]:
        async def _q(session: AsyncSession):
            r = await session.get(Character, char_id)
            return self._char_dict(r) if r else None

        return await self._query_or_mem(_q, None)

    async def save_character(self, data: dict) -> None:
        await self._upsert(Character, data)

    @staticmethod
    def _char_dict(r: Character) -> dict:
        return {"id": r.id, "scene_id": r.scene_id, "name": r.name, "spec_json": r.spec_json}

    # ------------------------------------------------------------------
    # 树（书 → 章 → 场景 → 角色 一次拉全，防客户端 N+1）
    # ------------------------------------------------------------------
    async def get_book_tree(self, book_id: str) -> Optional[dict]:
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
                    "scene_summary": sc.scene_summary,
                })
            all_chars = (
                (await session.execute(select(Character))).scalars().all()
                if chapters else []
            )
            chars_by_scene: dict[str, list[dict]] = {}
            for ch in all_chars:
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
            logger.warning("[Repo] 查询降级内存态：%s", e)
            return fallback

    async def _list_rows(self, model, mapper) -> list[dict]:
        async def _q(session: AsyncSession):
            stmt = select(model).order_by(model.id)
            rows = (await session.execute(stmt)).scalars().all()
            return [mapper(r) for r in rows]

        return await self._query_or_mem(_q, [])

    async def _upsert(self, model, data: dict) -> None:
        if not self.use_db:
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