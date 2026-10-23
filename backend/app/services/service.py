"""服务层 · SimulationService：面向外部（API/脚本）的业务编排（async）。

能力：启动叙事（四层装配）/ 恢复上次 / step 回合 / 查看状态 / 作者介入 /
四层目录查询（books/chapters/scenes/characters/book-tree）。
数据经 async Repo（SQLAlchemy 2.0 ORM）走 DB；DB 未就绪时内存态兜底。
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
import time
import uuid
from datetime import datetime
from typing import AsyncIterator

from langgraph.graph.state import CompiledStateGraph

from app import scenarios
from app.config import settings
from app.db.repo import Repo
from app.errors import (ChapterNotFoundError, ConflictError, InvalidActionError,
                        SceneNotFoundError, SimNotFoundError)
from app.schemas import models
from app.services.engine.director import PlanCfg
from app.services.engine.style_checks import voice_prior as style_voice_prior
from app.services.text_metrics import (chapter_body, chapter_chars, prose_chars,  # 字数唯一口径（B39/v1.46）
                                      scenes_chars)
from app.services.engine.book_audit import voice_drift_by_scene, timeline_replay   # 整书体检（v1.38）
from app.services.llm.client import client as llm_client  # 模块级引用：便于测试统一替换（conftest）

logger = logging.getLogger(__name__)


def _parse_json_list(raw: object) -> list:
    """把 JSON 数组字符串解析成 list（坏数据一律当空）。"""
    if isinstance(raw, list):
        return list(raw)
    try:
        data = json.loads(str(raw or "[]"))
    except Exception:  # noqa: BLE001
        return []
    return data if isinstance(data, list) else []


def _validate_chapter_digest(raw: object) -> str | None:
    """0-token 校验：前情概要至少要有 one_line 或 events（其余字段可空但会被规范化）。"""
    if not isinstance(raw, dict):
        return "顶层必须是 JSON 对象"
    if not str(raw.get("one_line") or "").strip() and not raw.get("events"):
        return "one_line 与 events 至少要有一个"
    return None


def _validate_volume_digest(raw: object) -> str | None:
    if not isinstance(raw, dict):
        return "顶层必须是 JSON 对象"
    if not str(raw.get("arc") or "").strip() and not raw.get("characters"):
        return "arc 与 characters 至少要有一个"
    return None


def _parse_json_obj(raw: object) -> dict:
    """把 JSON 对象字符串解析成 dict（坏数据一律当空，不抛）。"""
    if isinstance(raw, dict):
        return dict(raw)
    try:
        data = json.loads(str(raw or "{}"))
    except Exception:  # noqa: BLE001
        return {}
    return data if isinstance(data, dict) else {}


def _ensure_cast(sim: models.SimulationState) -> None:
    """空 cast 硬拦（P0）：没有上场角色时**不许推演**。

    否则导演会照常跑回合，但每回合只有导演提示事件（不计入角色行动）→ 归档里
    无行动/无对话/无成文，前端显示成"演绎了但什么都没有"（真实数据实测 19 个空回合）。
    正确路径：先选角（PUT /sims/{id}/cast）再推演。
    """
    if not sim.characters:
        raise InvalidActionError(
            "尚未配置上场角色，无法推演：可在导演台『配置上场角色』选角，"
            "或到人物页点『✨ 让主笔生成角色』一键补齐角色卡")


def _archive_is_empty(a: models.TurnArchive) -> bool:
    """回合归档是不是"空壳"（无角色行动、也无成文）。

    v1.72：**唯一口径** —— GET /sims/{id}/history 的过滤、cleaning 端点、归档时顺手
    清理，三处都调这里。此前只有 history 过滤（DB 里空壳一直留着，total_archived
    带着 19 条历史包袱，作者看到的"共 N 回合"和真实回合数不一致）。
    """
    return not (getattr(a, "events", None) or []) and not str(getattr(a, "prose", "") or "").strip()


def _preview(text: str, n: int = 60) -> str:
    """正文预览：压平空白后截前 n 字（校对视图用，不参与字数统计）。"""
    flat = " ".join(str(text or "").split())
    return flat[:n] + ("…" if len(flat) > n else "")


def _scene_plan_cfg(plan_cfg_json: str, static_cfg: PlanCfg) -> PlanCfg:
    """融合 DB 场景配置（优先）与静态回退：缺哪个补哪个。"""
    from app.services.engine.beats import parse_beats

    db = json.loads(plan_cfg_json or "{}")
    return PlanCfg(
        secret_fact_ids=list(db.get("secret_fact_ids") or static_cfg.secret_fact_ids),
        expose_target=dict(db.get("expose_target") or static_cfg.expose_target),
        goal_pressure=[dict(p) for p in (db.get("goal_pressure") or static_cfg.goal_pressure)],
        env_pressure_lines=list(db.get("env_pressure_lines") or static_cfg.env_pressure_lines),
        raise_after_turn=db.get("raise_after_turn") or static_cfg.raise_after_turn,
        ending_options=list(db.get("ending_options") or static_cfg.ending_options),
        # v1.54：场级必然事件（主笔写进 plan_cfg_json.beats；坏数据 parse_beats 会跳过）
        beats=parse_beats(db.get("beats") or static_cfg.beats),
    )



# v1.70：同一 note 的并发 apply 互斥（进程内锁；跨进程由 payload.applied 幂等兜底）
_APPLY_NOTE_LOCKS: dict[str, asyncio.Lock] = {}


def _apply_note_lock(note_id: str) -> asyncio.Lock:
    lock = _APPLY_NOTE_LOCKS.get(note_id)
    if lock is None:
        lock = asyncio.Lock()
        _APPLY_NOTE_LOCKS[note_id] = lock
    return lock


class SimulationService:
    def __init__(self, repo: Repo):
        self.repo = repo

    _graphs: dict = {}

    def _graph(self, plan_cfg: PlanCfg, decide_fn, converge_fn) -> CompiledStateGraph:
        from app.services.engine.graph import build_graph

        # 缓存键含 plan_cfg 指纹：不同场景的导演配置不会共用同一张图
        key = (settings.guard_retry_max, settings.max_turns,
               json.dumps(plan_cfg.__dict__, ensure_ascii=False, sort_keys=True))
        g = self._graphs.get(key)
        if g is None:
            g = build_graph(plan_cfg, decide_fn, converge_fn,
                            settings.guard_retry_max, settings.max_turns)
            self._graphs[key] = g
        return g

    # ---------------------------------------------------------------- 四层目录查询
    async def list_books(self) -> list[dict]:
        return await self.repo.list_books()

    async def list_chapters(self, book_id: str) -> list[dict]:
        return await self.repo.list_chapters_by_book(book_id)

    async def list_scenes(self, chapter_id: str) -> list[dict]:
        # v1.55：带解析后的 beats，前端才能显示"这场必然发生什么"
        rows = await self.repo.list_scenes_by_chapter(chapter_id)
        return [self._scene_with_beats(s) or {} for s in rows]

    async def get_chapter(self, chapter_id: str) -> dict | None:
        """章节详情（含 book_id，供导演台 scene→chapter→book 反查书树）。"""
        return await self.repo.get_chapter(chapter_id)

    async def _persist_beats_status(self, sim: models.SimulationState) -> None:
        """把本场 beats 判定结果写回 `scenes.plan_cfg_json.beats_status`（v1.57）。

        为什么落场景而不只留 sim.scratch：sim 只是**一次推演的快照**（会被新推演覆盖），
        而"这一场的必然事件到底发生了没有"是**场景级结论**——作者、章级巡检、骨架体检
        都要读它。不落场景，这个结论就只存在于日志里。
        """
        status = (sim.scratch or {}).get("beats_status")
        if not status or not sim.scene_id:
            return
        try:
            import time as _time

            scene = await self.repo.get_scene(sim.scene_id)
            if not scene:
                return
            cfg = _parse_json_obj(str(scene.get("plan_cfg_json") or "{}"))
            cfg["beats_status"] = {**status, "turn": int(sim.world.turn),
                                   "at": int(_time.time() * 1000)}
            await self.repo.save_scene({**scene, "id": sim.scene_id,
                                        "plan_cfg_json": json.dumps(cfg, ensure_ascii=False)})
        except Exception as e:  # noqa: BLE001  落库失败不能挡住推演收尾
            logger.warning("[beats] 判定结果落库失败（忽略）：%s", e)

    @staticmethod
    def _merge_beats_into_plan_cfg(plan_cfg_json: str, beats: object) -> str:
        """把 beats 合并进 plan_cfg_json（v1.55）：只动 beats 键，其它导演配置一律保留。"""
        from app.services.engine.beats import parse_beats

        try:
            cur = json.loads(plan_cfg_json or "{}")
        except Exception:  # noqa: BLE001
            cur = {}
        if not isinstance(cur, dict):
            cur = {}
        cur["beats"] = parse_beats(beats or [])
        return json.dumps(cur, ensure_ascii=False)

    @staticmethod
    def _scene_with_beats(scene: dict | None) -> dict | None:
        """场景返回里带解析后的 beats（前端要显示"这场必然发生什么"）。"""
        if not scene:
            return scene
        from app.services.engine.beats import parse_beats

        try:
            cfg = json.loads(str(scene.get("plan_cfg_json") or "{}"))
        except Exception:  # noqa: BLE001
            cfg = {}
        out = dict(scene)
        out["beats"] = parse_beats((cfg or {}).get("beats") if isinstance(cfg, dict) else [])
        # v1.57：最近一次推演的判定结论（未达成时含 missed_texts，供 UI/巡检展示）
        out["beats_status"] = (cfg or {}).get("beats_status") if isinstance(cfg, dict) else None
        return out

    async def get_scene(self, scene_id: str) -> dict | None:
        return self._scene_with_beats(await self.repo.get_scene(scene_id))

    async def get_scene_characters(self, scene_id: str) -> list[dict]:
        """场景可用角色 = 本书书级角色 + 场景特设角色。"""
        return await self.repo.list_characters_for_scene(scene_id)

    async def generate_book_characters(self, book_id: str, direction: str = "") -> dict:
        """主笔生成角色卡（补齐"从零开局"缺的一环）：生成 → 落书级角色卡（作者可编辑）。

        背景：骨架能自动生成、角色卡却只能手写/seed → "空 cast 推演空转"（P0 已拦）。
        这里给出**一步解决**的出口：方向/书名 → 3-5 张可区分腔调的卡。
        """
        from app.services.engine.chief_planner import generate_characters

        book = await self.repo.get_book(book_id) or {}
        existing = await self.repo.list_characters_by_book(book_id)
        context = "书名：%s；已有角色：%s" % (
            book.get("title") or "", "、".join(str(c.get("name") or "") for c in existing) or "无")
        cards = await generate_characters(direction, context)
        created: list[dict] = []
        for c in cards:
            cid = f"char-{uuid.uuid4().hex[:8]}"
            spec = {
                "id": cid, "name": c["name"], "summary": c["summary"],
                "traits": c["traits"], "voice": c["voice"],
                "core_beliefs": c["core_beliefs"], "bottom_lines": c["bottom_lines"],
                "dynamic_goals": [{"id": f"g-{uuid.uuid4().hex[:6]}", "text": g, "weight": 0.5}
                                  for g in c["goals"]],
                "system_prompt": "", "think_schema": "", "decide_schema": "", "static_world": "",
            }
            from app.services.engine.cast_roles import clean_tier

            data = {"id": cid, "book_id": book_id, "name": c["name"], "scene_id": None,
                    "spec_json": json.dumps(spec, ensure_ascii=False),
                    # v1.53：主笔生成时就给档位（只有主角/关键/反派会进涌现回合）
                    "role_tier": clean_tier(c.get("role_tier"))}
            await self.repo.save_character(data)
            created.append(data)
        return {"book_id": book_id, "created": len(created), "characters": created}

    async def infer_book_role_tiers(self, book_id: str, *, apply: bool = False) -> dict:
        """让主笔给存量角色卡**分档**（一次 LLM）；apply=False 只出预览，作者确认再落库。

        v1.53：迁移把 role_tier 默认成 minor（不猜谁是主角）→ 存量书默认 cast 会退化成
        "取最相关的前 5 位"。这个一次性动作把档位补齐，之后导演台默认选角就有依据。
        """
        from app.services.engine.cast_roles import TIER_LABEL, infer_role_tiers, tier_of

        book = await self.repo.get_book(book_id)
        if book is None:
            raise BookNotFoundError(f"未找到书籍: {book_id}")
        chars = await self.repo.list_characters_by_book(book_id)
        if not chars:
            return {"book_id": book_id, "assignments": [], "counts": {},
                    "skipped": "这本书还没有角色卡（先让主笔生成角色）"}
        if not llm_client.available:
            return {"book_id": book_id, "assignments": [], "counts": {},
                    "skipped": "LLM 未接入（未补档位）"}

        rows = []
        for c in chars:
            spec = _parse_json_obj(c.get("spec_json"))
            rows.append({"id": str(c.get("id") or ""), "name": c.get("name") or "",
                         "summary": str(spec.get("summary") or "")})
        context = "书名：%s；类型：%s；方向：%s" % (
            book.get("title") or "", book.get("genre") or "",
            str(book.get("synopsis") or "")[:120] or "（未填）")
        got = await infer_role_tiers(llm_client, rows, context=context)
        if not got:
            return {"book_id": book_id, "assignments": [], "counts": {},
                    "error": "模型没返回可用分档（已重试，未改动任何角色）"}

        by_id = {str(c.get("id")): c for c in chars}
        preview: list[dict] = []
        counts: dict[str, int] = {}
        for a in got:
            row = by_id.get(a["id"]) or {}
            preview.append({"id": a["id"], "name": row.get("name") or "",
                            "role_tier": a["role_tier"],
                            "role_tier_label": TIER_LABEL[a["role_tier"]],
                            "current": tier_of(row), "why": a.get("why") or ""})
            counts[a["role_tier"]] = counts.get(a["role_tier"], 0) + 1

        applied = 0
        if apply:
            for p in preview:
                row = by_id.get(p["id"])
                if not row or tier_of(row) == p["role_tier"]:
                    continue
                await self.repo.save_character({**row, "role_tier": p["role_tier"]})
                applied += 1
        return {"book_id": book_id, "assignments": preview, "counts": counts,
                "applied": applied, "total": len(chars)}

    async def get_book_characters(self, book_id: str) -> list[dict]:
        """书级角色库（人物页数据源）。"""
        return await self.repo.list_characters_by_book(book_id)

    # ---------------------------------------------------------------- 信念账本（书级 CRUD）
    @staticmethod
    def _valid_channel(channel: str) -> bool:
        return channel in {"perceived", "told", "inferred"}

    async def list_beliefs(self, book_id: str, char_id: str | None = None,
                           channel: str | None = None) -> list[dict]:
        return await self.repo.list_beliefs(book_id, char_id, channel)

    async def create_belief(self, book_id: str, data: dict) -> dict:
        """作者手写信念：edited=True、source=AUTHOR；channel 枚举/置信度 clamp 校验。"""
        channel = str(data.get("channel") or "perceived")
        if not self._valid_channel(channel):
            raise InvalidActionError(f"非法 channel: {channel!r}")
        cid = str(data.get("char_id") or "").strip()
        if not cid:
            raise InvalidActionError("必须指定 char_id")
        text = str(data.get("text") or "").strip()
        if not text:
            raise InvalidActionError("信念内容 text 不能为空")
        confidence = self._clamp_conf(data.get("confidence"))
        bid = data.get("id") or f"bel-{uuid.uuid4().hex[:12]}"
        await self.repo.save_belief({
            "id": bid, "book_id": book_id, "char_id": cid,
            "fact_id": str(data.get("fact_id") or ""),
            "source_event_id": str(data.get("source_event_id") or "AUTHOR"),
            "channel": channel, "text": text, "confidence": confidence,
            "edited": True, "ts": int(time.time() * 1000),
        })
        return {"id": bid}

    async def update_belief(self, belief_id: str, data: dict) -> dict:
        cur = await self.repo.get_belief(belief_id)
        if cur is None:
            raise InvalidActionError(f"信念不存在: {belief_id}")
        channel = str(data.get("channel") or cur["channel"])
        if not self._valid_channel(channel):
            raise InvalidActionError(f"非法 channel: {channel!r}")
        text = str(data.get("text") if "text" in data else cur["text"]).strip()
        if not text:
            raise InvalidActionError("信念内容 text 不能为空")
        await self.repo.save_belief({
            "id": belief_id, "book_id": cur["book_id"], "char_id": cur["char_id"],
            "fact_id": str(data.get("fact_id", cur["fact_id"]) or ""),
            "source_event_id": str(data.get("source_event_id", cur["source_event_id"]) or ""),
            "channel": channel, "text": text,
            "confidence": self._clamp_conf(data.get("confidence", cur["confidence"])),
            "edited": True, "ts": int(time.time() * 1000),
        })
        return {"id": belief_id}

    async def delete_belief(self, belief_id: str) -> None:
        await self.repo.delete_belief(belief_id)

    # ---------------------------------------------------------------- 世界状态（S1 · 作者编辑）
    _WS_KINDS = {"character", "item", "time", "term", "numeric"}

    async def create_world_state(self, book_id: str, data: dict) -> dict:
        """作者手写世界状态：edited=True、source=AUTHOR。key 缺省按语义模板生成。"""
        kind = str(data.get("kind") or "").strip()
        if kind not in self._WS_KINDS:
            raise InvalidActionError(f"非法 kind: {kind!r}（可选 {sorted(self._WS_KINDS)}）")
        name = str(data.get("name") or "").strip()
        value = str(data.get("value") or "").strip()
        if not name:
            raise InvalidActionError("状态对象名 name 不能为空")
        if not value:
            raise InvalidActionError("状态值 value 不能为空")
        key = str(data.get("key") or "").strip()
        if not key:
            prefix = {"character": "char", "item": "item", "time": "time",
                      "term": "term", "numeric": "num"}[kind]
            attr = str(data.get("attr") or "状态").strip()
            key = f"{prefix}:{name}:{attr}"
        # 同 (kind,key) 已有行 → 视为更新（防 UNIQUE 冲突插重复）
        existing = {}
        for e in await self.repo.list_world_states(book_id):
            existing[(str(e.get("kind") or ""), str(e.get("key") or ""))] = e
        prev = existing.get((kind, key))
        if prev is not None:
            wid = str(prev.get("id") or "")
            prev_value = str(prev.get("value") or "")
        else:
            wid = f"ws-{uuid.uuid4().hex[:14]}"
            prev_value = ""
        await self.repo.save_world_state({
            "id": wid, "book_id": book_id, "kind": kind, "name": name, "key": key,
            "value": value,
            "scene_no": int(data.get("scene_no") or (prev or {}).get("scene_no") or 0),
            "source_event_id": "AUTHOR", "previous_value": prev_value,
            "edited": True, "ts": int(time.time() * 1000),
        })
        return {"id": wid}

    async def update_world_state(self, ws_id: str, data: dict) -> dict:
        """作者修改世界状态（**增量**：只传要改的字段，缺失或 None 保持原值；落库置 edited=True）。"""
        cur = await self.repo.get_world_state(ws_id)
        if cur is None:
            raise InvalidActionError(f"世界状态不存在: {ws_id}")
        def pick(field: str, current: object) -> str:
            """增量取值（B30）：未传 / 显式 None → 保持原值，绝不把 None 写成字符串 "None"。"""
            got = data.get(field)
            return str(current or "") if got is None else str(got)

        kind = pick("kind", cur["kind"]).strip()
        if kind not in self._WS_KINDS:
            raise InvalidActionError(f"非法 kind: {kind!r}（可选 {sorted(self._WS_KINDS)}）")
        name = pick("name", cur["name"]).strip()
        value = pick("value", cur["value"]).strip()
        if not name or not value:
            raise InvalidActionError("name / value 不能为空")
        scene_no = data.get("scene_no")
        await self.repo.save_world_state({
            "id": ws_id, "book_id": cur["book_id"], "kind": kind, "name": name,
            "key": pick("key", cur["key"]).strip() or cur["key"],
            "value": value,
            "scene_no": int(scene_no) if scene_no is not None else int(cur.get("scene_no") or 0),
            "source_event_id": "AUTHOR", "previous_value": cur.get("value") or "",
            "edited": True, "ts": int(time.time() * 1000),
        })
        return {"id": ws_id}

    async def delete_world_state(self, ws_id: str) -> None:
        await self.repo.delete_world_state(ws_id)

    @staticmethod
    def _clamp_conf(value) -> float:
        try:
            return max(0.0, min(1.0, float(value)))
        except (TypeError, ValueError):
            return 0.5

    # ---------------------------------------------------------------- Dashboard 聚合
    async def book_rehearsal_plan(self, book_id: str) -> dict:
        """C 批：全书**彩排建议**（0-token 重头戏判定）+ 每场已排演回合数。"""
        from app.services.agents.rehearsal import build_plan

        archives = await self._archives_by_scene(book_id)
        foreshadows = await self.repo.list_foreshadows(book_id)
        return await build_plan(self.repo, book_id, archives, foreshadows)

    async def book_material_usage(self, book_id: str) -> dict:
        """C 批：**素材使用率**（已采纳素材 vs 正文命中）——启发式，给"值不值得继续演"的信号。"""
        from app.services.agents.material_usage import build_usage

        return await build_usage(self.repo, book_id, await self._archives_by_scene(book_id))

    async def book_global_view(self, book_id: str) -> dict:
        """S3 全局结构与张力视图（0-token 派生，零新表）。

        装配：章/场景 + 每场景**最新** sim 的回合归档（张力）+ 伏笔账本
        → engine/global_view.build_global_view 派生曲线与诊断。
        """
        from app.services.engine.global_view import build_global_view

        book = await self.repo.get_book(book_id) or {}
        tree = await self.repo.get_book_tree(book_id)
        chapters = sorted((tree or {}).get("chapters") or [],
                          key=lambda c: c.get("order_no") or 0)
        scenes_all: list[dict] = []
        for ch in chapters:
            scenes_all += await self.repo.list_scenes_by_chapter(ch["id"])

        # 归档取数与 S4 剧本共用一套：最新且**有归档**的 sim（避免被空 sim 盖掉）
        archives_by_scene = await self._archives_by_scene(book_id)

        foreshadows = await self.repo.list_foreshadows(book_id)
        view = build_global_view(chapters, scenes_all, archives_by_scene, foreshadows)
        view["book_id"] = book_id
        view["title"] = book.get("title") or ""
        view["sims"] = len(archives_by_scene)
        return view

    # ---------------------------------------------------------------- 跨书搜索（B38 后续）
    async def search_all(self, q: str, limit: int = 8) -> dict:
        """跨书搜索 → 统一命中列表（带前端可直接跳转的 path）。

        匹配本身在 repo（字面包含）；这里只做「补书名/章节名 + 拼路由」，
        路由与前端 ContextBar/深链约定保持一致（?book=/&chapter=/深链 /director/:sceneId）。
        """
        q = (q or "").strip()
        if not q:
            return {"q": "", "hits": [], "counts": {}}
        raw = await self.repo.search_all(q, limit)
        titles = {b["id"]: (b.get("title") or "") for b in await self.repo.list_books()}
        hits: list[dict] = []

        for b in raw["books"]:
            hits.append({"kind": "book", "id": b["id"], "label": b.get("title") or "",
                         "subtitle": b.get("genre") or "作品", "book_id": b["id"],
                         "path": f"/dashboard?book={b['id']}"})
        for c in raw["chapters"]:
            bid = c.get("book_id") or ""
            hits.append({"kind": "chapter", "id": c["id"],
                         "label": f"第 {c.get('order_no') or '?'} 章 · {c.get('title') or ''}".strip(),
                         "subtitle": f"{titles.get(bid, '')} · 章节", "book_id": bid,
                         "path": f"/maestro?book={bid}&chapter={c['id']}"})
        for s in raw["scenes"]:
            ch = await self.repo.get_chapter(s.get("chapter_id") or "")
            bid = (ch or {}).get("book_id") or ""
            hits.append({"kind": "scene", "id": s["id"], "label": s.get("title") or "",
                         "subtitle": f"{titles.get(bid, '')} · {(ch or {}).get('title') or ''} · 场景",
                         "book_id": bid, "chapter_id": s.get("chapter_id") or "",
                         "path": f"/director/{s['id']}?book={bid}&chapter={s.get('chapter_id') or ''}"})
        for c in raw["characters"]:
            bid = c.get("book_id") or ""
            hits.append({"kind": "character", "id": c["id"], "label": c.get("name") or "",
                         "subtitle": f"{titles.get(bid, '')} · 角色卡", "book_id": bid,
                         "path": f"/characters?book={bid}"})
        for f_ in raw["foreshadows"]:
            bid = f_.get("book_id") or ""
            hits.append({"kind": "foreshadow", "id": f_["id"], "label": (f_.get("text") or "")[:40],
                         "subtitle": f"{titles.get(bid, '')} · 伏笔 · {f_.get('status') or ''}",
                         "book_id": bid, "path": f"/dashboard?book={bid}"})
        return {"q": q, "hits": hits, "counts": {k: len(v) for k, v in raw.items()}}

    # ---------------------------------------------------------------- 逐章校对（成书收口 · C）
    async def get_collation(self, book_id: str) -> dict:
        """逐章校对数据（0-token 派生）：每章的场景清单 + 字数 + 定稿状态 + 全书统计。

        字数与概览 KPI / 导出头部同口径（text_metrics）。校对视图据此回答作者最关心的问题：
        「哪章还差几场没定稿、点哪一场去改、现在够不够成书」。
        """
        book = await self.repo.get_book(book_id)
        if book is None:
            raise SceneNotFoundError(f"未找到书: {book_id}")
        chapters = sorted(await self.repo.list_chapters_by_book(book_id),
                          key=lambda c: c.get("order_no") or 0)
        out: list[dict] = []
        total_chars = 0
        done_chapters = 0
        unfinished = 0
        scene_total = 0
        for ch in chapters:
            scenes = await self.repo.list_scenes_by_chapter(ch["id"])
            items: list[dict] = []
            for s in scenes:
                prose = str(s.get("final_prose") or "")
                chars = prose_chars(prose)
                finalized = bool(prose.strip())
                scene_total += 1
                if not finalized:
                    unfinished += 1
                items.append({
                    "scene_id": s["id"], "title": s.get("title") or "",
                    "word_count": chars, "finalized": finalized,
                    "preview": _preview(prose), "goal": s.get("goal") or "",
                })
            # v1.46 直写链路：章正文优先、场景聚合回退（口径统一走 text_metrics）
            body = chapter_body(ch, scenes)
            ch_chars = prose_chars(body)
            has_body = bool(str(ch.get("prose_md") or "").strip())
            total_chars += ch_chars
            # 「这一章写完没有」判据（v1.46）：
            #   · 直写模式（有章正文）→ 就算写完
            #   · 场景模式（无章正文）→ **仍是"所有场都定稿"**（1/3 场有正文不算写完）
            done = has_body or (bool(items) and all(i["finalized"] for i in items))
            if done:
                done_chapters += 1
            out.append({
                "chapter_id": ch["id"], "order_no": int(ch.get("order_no") or 0),
                "title": ch.get("title") or "", "tone": ch.get("tone") or "",
                "summary": ch.get("summary") or "", "word_target": int(ch.get("word_target") or 0),
                "word_count": ch_chars, "scene_count": len(items),
                "has_body": has_body, "body_chars": prose_chars(str(ch.get("prose_md") or "")),
                "done": done, "scenes": items,
            })
        return {
            "book_id": book_id, "title": book.get("title") or "",
            "chapters": out,
            "kpi": {"chapters_total": len(out), "chapters_done": done_chapters,
                    "word_count": total_chars, "unfinished_scenes": unfinished,
                    "scene_total": scene_total},
        }

    # ---------------------------------------------------------------- 整书体检（v1.38）
    async def get_health_report(self, book_id: str) -> dict:
        """整书体检：结构诊断（global_view）+ 口音漂移 + 时间线回放（0-token，零 LLM）。

        这是"成书前最后一道质检"：结构分与 R1-R7 说明骨架是否有病，
        口音漂移管"人物说话越来越不像他自己"，时间线回放管"世界状态数值倒流"。
        与导出/概览同口径；每条结论带 evidence 与可跳转定位。
        """
        book = await self.repo.get_book(book_id)
        if book is None:
            raise SceneNotFoundError(f"未找到书: {book_id}")

        gv = await self.book_global_view(book_id)
        chapters = sorted(await self.repo.list_chapters_by_book(book_id),
                          key=lambda c: c.get('order_no') or 0)
        chars = await self.repo.list_characters_by_book(book_id)
        cards = []
        for c in chars:
            try:
                cards.append(models.CharacterCard.model_validate_json(c.get("spec_json") or "{}"))
            except Exception:  # noqa: BLE001
                continue

        # 逐个**写作单元**正文（v1.46：章正文优先、场景聚合回退；含章号供前端定位）
        # 此前只扫场景 → 章级直写的书（场景为空）体检得到"还没有可分析的角色对白"，等于空转。
        scene_texts: list[dict] = []
        for ch in chapters:
            ch_scenes = await self.repo.list_scenes_by_chapter(ch["id"])
            body = chapter_body(ch, ch_scenes).strip()
            if body:
                scene_texts.append({
                    "scene_id": str(ch["id"]) if str(ch.get("prose_md") or "").strip()
                                else (ch_scenes[0]["id"] if ch_scenes else str(ch["id"])),
                    "order": int(ch.get("order_no") or 0),
                    "title": ch.get("title") or "",
                    "text": body,
                })

        voice = voice_drift_by_scene(scene_texts, [{'name': c.name} for c in cards])
        timeline = timeline_replay(await self.repo.list_world_states(book_id))

        voice_flagged = sum(len(v.get("flagged") or []) for v in voice)
        return {
            "book_id": book_id, "title": book.get("title") or "",
            "structure": {
                "score": gv.get("structure_score"),
                "diagnostics": gv.get("diagnostics") or [],
                "summary": gv.get("summary") or {},
            },
            "voice": {"characters": voice, "flagged": voice_flagged,
                      "scenes_checked": len(scene_texts)},
            "timeline": {"flows": timeline.get("flows") or [],
                         "rewinds": timeline.get("rewinds") or [],
                         "keys_checked": len(timeline.get("flows") or [])},
            "checked_at": int(time.time() * 1000),
        }

    async def get_dashboard(self, book_id: str) -> dict:
        from datetime import datetime

        book = await self.repo.get_book(book_id) or {}
        tree = await self.repo.get_book_tree(book_id)
        chapters = sorted((tree or {}).get("chapters") or [], key=lambda c: c.get("order_no") or 0)
        foreshadows = await self.repo.list_foreshadows(book_id)
        beliefs = await self.repo.list_beliefs(book_id)
        active_sims = await self.repo.list_active_sims_by_book(book_id)
        active_scene_ids = {s["scene_id"] for s in active_sims if s.get("scene_id")}

        # 场景全量（含 final_prose / 时间戳）
        scenes_all: list[dict] = []
        for ch in chapters:
            scenes_all += await self.repo.list_scenes_by_chapter(ch["id"])
        scenes_by_ch: dict[str, list[dict]] = {}
        for s in scenes_all:
            scenes_by_ch.setdefault(s["chapter_id"], []).append(s)

        # 唯一口径（B39/v1.46）：章正文优先、场景聚合回退
        word_count = sum(chapter_chars(ch, scenes_by_ch.get(ch["id"], [])) for ch in chapters)

        # ---- 时间线：章状态 + 章字数 ----
        timeline: list[dict] = []
        chapters_done = 0
        for ch in chapters:
            ch_scenes = scenes_by_ch.get(ch["id"], [])
            ch_has_body = bool(str(ch.get("prose_md") or "").strip())
            # 章写完的判据（v1.46）：直写模式看章正文；场景模式仍看"所有场定稿"
            fully_done = ch_has_body or (bool(ch_scenes) and all(
                (s.get("final_prose") or "").strip() for s in ch_scenes
            ))
            if fully_done:
                status, label, badge = "done", "已定稿", "done"
                chapters_done += 1
            elif any(s["id"] in active_scene_ids for s in ch_scenes):
                status, label, badge = "current", "导演中", "draft"
            elif ch_scenes:
                status, label, badge = "draft", "创作中", "draft"
            else:
                status, label, badge = "planned", "规划中", "planned"
            timeline.append({
                "chapter_id": ch["id"], "title": ch.get("title") or "",
                "order_no": ch.get("order_no") or 0, "tone": ch.get("tone") or "",
                "status": status, "label": label, "badge": badge,
                "word_count": chapter_chars(ch, ch_scenes),
                "has_body": ch_has_body,
            })

        # ---- 伏笔 KPI + 待办 ----
        open_fs = [f for f in foreshadows if f.get("status") in {"buried", "in_progress"}]
        closed_fs = [f for f in foreshadows if f.get("status") == "closed"]
        open_with_target = [f for f in open_fs if f.get("expected_close_scene")]
        edited_beliefs = sum(1 for b in beliefs if b.get("edited"))

        todos: list[dict] = []
        if open_with_target:
            todos.append({
                "severity": "warn", "title": "伏笔待回收",
                "desc": f"{len(open_with_target)} 条伏笔已设定期望回收场景，注意按节推进",
            })
        elif open_fs:
            todos.append({
                "severity": "warn", "title": "伏笔推进中",
                "desc": f"{len(open_fs)} 条伏笔仍处埋设/推进态",
            })
        if edited_beliefs:
            todos.append({
                "severity": "info", "title": "手改信念",
                "desc": f"角色信念账本中有 {edited_beliefs} 条作者手改/编辑，注意与推演事实保持一致",
            })
        if chapters_done == len(chapters) and chapters:
            todos.append({"severity": "ok", "title": "全书可发布", "desc": "所有章节已定稿，建议进阅读台做最终校对"})

        # ---- 健康分（派生占位）----
        health = 100 - min(20, len(open_fs) * 3) - min(30, (len(chapters) - chapters_done) * 5)
        health = max(0, min(100, health))

        # ---- 逐章正文（章正文优先、场景聚合回退）----
        # v1.46 直写链路：金句与热力都必须跟着"正文真源"走，否则直写的章在概览里
        # 会表现为「金句池暂无 / 写作热力暂无成文记录」——看起来像按钮没接线（实测踩到）。
        chapter_bodies: list[tuple[str, str]] = [
            (str(ch.get("title") or ""), chapter_body(ch, scenes_by_ch.get(ch["id"], [])))
            for ch in chapters
        ]

        # ---- 金句（从正文抽对话行：优先「」引号，其次整行双引号）----
        quotes: list[dict] = []
        for title, prose in chapter_bodies:
            for line in (prose or "").splitlines():
                line = line.strip()
                is_quote = bool(line) and (("「" in line and "」" in line)
                                          or ("“" in line and "”" in line)
                                          or (line.startswith('"') and line.endswith('"')))
                # v1.46：过短的对白（如「“滴。”」）不算金句 —— 去掉引号后不足 8 字就跳过
                if is_quote and len(line.strip('「」“”"')) >= 8:
                    quotes.append({"text": line[:60], "author": title or "—— 正文"})
                    if len(quotes) >= 3:
                        break
            if len(quotes) >= 3:
                break

        # ---- 近 4 周热力（按"这一章最后一次改动"的时间散布本章字数）----
        heat = [0.0] * 28
        now = datetime.now()
        for ch in chapters:
            body = chapter_body(ch, scenes_by_ch.get(ch["id"], []))
            if not body.strip():
                continue
            ts = ch.get("updated_at") or ch.get("created_at")
            if not ts:
                continue
            try:
                d = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
                if d.tzinfo is not None:
                    d = d.astimezone().replace(tzinfo=None)
            except ValueError:
                continue
            days = (now - d).days
            if 0 <= days < 28:
                heat[27 - days] += prose_chars(body)

        return {
            "book": {"id": book.get("id"), "title": book.get("title") or "",
                     "genre": book.get("genre") or "", "status": book.get("status") or "",
                     "chapter_count": book.get("chapter_count") or 0,
                     "cover_init": book.get("cover_init") or "墨"},
            "kpi": {
                "chapters_done": chapters_done,
                "chapters_total": len(chapters),
                "word_count": word_count,
                "foreshadow_open": len(open_fs),
                "foreshadow_closed": len(closed_fs),
                "health": health,
            },
            "timeline": timeline,
            "todos": todos,
            "quotes": quotes,
            "heat": [round(x) for x in heat],
        }

    async def get_book_tree(self, book_id: str) -> dict | None:
        tree = await self.repo.get_book_tree(book_id)
        if not tree:
            return tree
        # v1.55：场景带解析后的 beats（场景编辑器要显示/编辑"本场必然事件"）
        for ch in tree.get("chapters") or []:
            ch["scenes"] = [self._scene_with_beats(s) or {} for s in (ch.get("scenes") or [])]
        return tree

    # ---------------------------------------------------------------- 四层写接口（S1）
    async def create_book(self, data: dict) -> dict:
        bid = data.get("id") or f"book-{uuid.uuid4().hex[:8]}"
        data = {**data, "id": bid}
        await self.repo.save_book(data)
        return {"id": bid}

    async def update_book(self, book_id: str, data: dict) -> dict:
        await self.repo.save_book({**data, "id": book_id})
        return {"id": book_id}

    async def delete_book(self, book_id: str) -> None:
        await self.repo.delete_book(book_id)

    async def create_chapter(self, book_id: str, data: dict) -> dict:
        cid = data.get("id") or f"chapter-{uuid.uuid4().hex[:8]}"
        data = {**data, "id": cid, "book_id": book_id}
        # B38：order_no 是 1-based 显示口径（时间线渲染「第 N 章」）。此前不传就落 ORM 默认 0
        # → 出现「第 0 章」，导出/排序的口径也跟着偏。缺省时按"现有最大章号 + 1"补齐。
        if int(data.get("order_no") or 0) <= 0:
            existing = await self.repo.list_chapters_by_book(book_id)
            data["order_no"] = max((int(c.get("order_no") or 0) for c in existing), default=0) + 1
        await self.repo.save_chapter(data)
        self._drop_chapters_l1(book_id)          # L1：章清单变了，立即失效（不等 45s TTL）
        # v1.59：新增章可能落进某卷 range → 归属同步（幂等；失败不影响建章）
        try:
            await self.sync_volume_assignments(book_id)
        except Exception as e:  # noqa: BLE001
            logger.warning("[volume] 归属同步失败 book=%s：%s", book_id, e)
        return {"id": cid}

    def _drop_chapters_l1(self, book_id: str) -> None:
        from app.services import cache as cache_mod

        if book_id:
            cache_mod.l1_drop("chapters", book_id)

    async def update_chapter(self, chapter_id: str, data: dict) -> dict:
        await self.repo.save_chapter({**data, "id": chapter_id})
        self._drop_chapters_l1(str((data or {}).get("book_id") or ""))
        # 章号/章序可能改了 → 归属重算
        if "order_no" in data:
            try:
                ch = await self.repo.get_chapter(chapter_id) or {}
                await self.sync_volume_assignments(str(ch.get("book_id") or ""))
            except Exception as e:  # noqa: BLE001
                logger.warning("[volume] 归属同步失败 chapter=%s：%s", chapter_id, e)
        return {"id": chapter_id}

    async def delete_chapter(self, chapter_id: str) -> None:
        ch = await self.repo.get_chapter(chapter_id) or {}
        book_id = str(ch.get("book_id") or "")
        await self.repo.delete_chapter(chapter_id)
        self._drop_chapters_l1(book_id)          # L1：删章后章清单立即失效
        # v1.59：删章后卷归属要重算（否则 volume_id 指向已删章的位置）
        if book_id:
            try:
                await self.sync_volume_assignments(book_id)
            except Exception as e:  # noqa: BLE001
                logger.warning("[volume] 归属同步失败 book=%s：%s", book_id, e)

    async def create_scene(self, chapter_id: str, data: dict) -> dict:
        sid = data.get("id") or f"scene-{uuid.uuid4().hex[:8]}"
        data = {**data, "id": sid, "chapter_id": chapter_id}
        await self.repo.save_scene(data)
        return {"id": sid}

    async def update_scene(self, scene_id: str, data: dict) -> dict:
        payload = dict(data)
        # v1.55：beats 单独处理——合进 plan_cfg_json，别覆盖其它导演配置（goal_pressure 等）
        if "beats" in payload:
            cur = await self.repo.get_scene(scene_id)
            base = str((cur or {}).get("plan_cfg_json") or payload.get("plan_cfg_json") or "{}")
            payload["plan_cfg_json"] = self._merge_beats_into_plan_cfg(base, payload.pop("beats"))
        await self.repo.save_scene({**payload, "id": scene_id})
        return {"id": scene_id}

    async def delete_scene(self, scene_id: str) -> None:
        await self.repo.delete_scene(scene_id)

    async def replace_chapter_scenes(self, chapter_id: str, scenes: list[dict]) -> dict:
        """场景级全量替换（主笔升级 · 部分修改）：传入该章完整场景数组。

        服务端 diff：带 id 且存在的 → 更新；无 id/不存在的 → 新建；
        已存在但不在传入数组 → 删除（cursor_pos 按数组序重排）。
        不触碰 scenario_def / initial_facts / 角色卡，只改结构字段。
        """
        chapter = await self.repo.get_chapter(chapter_id)
        if chapter is None:
            raise SceneNotFoundError(f"未找到章节: {chapter_id}")

        existing = await self.repo.list_scenes_by_chapter(chapter_id)
        existing_map = {s["id"]: s for s in existing}
        keep: list[str] = []
        for j, sc in enumerate(scenes):
            sid = (sc.get("id") or "").strip()
            fields = {
                "title": str(sc.get("title") or f"场景{j + 1}"),
                "stage_desc": str(sc.get("stage_desc") or ""),
                "goal": str(sc.get("goal") or ""),
                "content_desc": str(sc.get("content_desc") or ""),
                "cursor_pos": j + 1,
            }
            if "beats" in sc:
                # v1.55：beats 合进 plan_cfg_json（保留既有导演配置）
                base_cfg = str((existing_map.get(sid) or {}).get("plan_cfg_json") or "{}")
                fields["plan_cfg_json"] = self._merge_beats_into_plan_cfg(base_cfg, sc.get("beats"))
            if sid and sid in existing_map:
                await self.repo.save_scene({**fields, "id": sid})
                keep.append(sid)
            else:
                r = await self.create_scene(chapter_id, fields)
                keep.append(r["id"])
        for sid in existing_map:
            if sid not in keep:
                await self.repo.delete_scene(sid)
        return {"chapter_id": chapter_id, "scenes": len(keep)}

    async def create_character(self, book_id: str, data: dict, scene_id: Optional[str] = None) -> dict:
        """书级创建：scene_id 省略 → 全书共享；给定 → 该场景特设角色（群演/NPC）。"""
        cid = data.get("id") or f"char-{uuid.uuid4().hex[:8]}"
        data = {**data, "id": cid, "book_id": book_id}
        if scene_id:
            data["scene_id"] = scene_id
        else:
            data.pop("scene_id", None)
        if data.get("spec_json"):
            models.CharacterCard.model_validate_json(data["spec_json"])  # 校验④层字段
        await self.repo.save_character(data)
        return {"id": cid}

    async def update_character(self, char_id: str, data: dict) -> dict:
        if data.get("spec_json"):
            models.CharacterCard.model_validate_json(data["spec_json"])
        # v1.53：档位归一（作者可能在 UI 选中文档位），非法值按 minor 兜底，不静默存脏值
        if "role_tier" in data:
            from app.services.engine.cast_roles import clean_tier

            tier = str(data.get("role_tier") or "").strip()
            if tier:
                data = {**data, "role_tier": clean_tier(tier)}
            else:
                data = {k: v for k, v in data.items() if k != "role_tier"}
        await self.repo.save_character({**data, "id": char_id})
        return {"id": char_id}

    async def delete_character(self, char_id: str) -> None:
        await self.repo.delete_character(char_id)

    # ---------------------------------------------------------------- 主笔规划（S2）
    async def plan_book(self, book_id: str, direction: str = "", inspiration_ids: list[str] | None = None,
                        *, batch_size: int = 0, batch_from: int = 0) -> dict:
        """主笔产出骨架预览（不落库）：POST /books/{id}/plan。

        inspiration_ids 非空时，把已采纳灵感卡作为"已在酝酿的设定"注入主笔（P1-1 灵感落地）。
        上下文统一走 chief_perceive（书树+记忆+灵感+伏笔），杜绝从零重排。

        A4 分批交付：batch_size > 0 时只排 batch_from（默认＝已落库章数＋1）起的 batch_size 章，
        已落库章节（title/summary）作为"必须承接"喂给主笔；返回 batch 元信息与已落库章数。
        """
        book = await self.repo.get_book(book_id)
        if book is None:
            raise SceneNotFoundError(f"未找到书: {book_id}")
        from app.services.engine.chief_planner import plan_skelly

        inspirations: list[dict] = []
        if inspiration_ids:
            all_cards = await self.repo.list_inspirations(book_id)
            ids = set(inspiration_ids)
            inspirations = [c for c in all_cards
                            if c.get("id") in ids and c.get("adopted")]

        context = await self.chief_perceive(book_id)
        existing = await self.list_chapters(book_id)
        locked = [{"order_no": c.get("order_no") or i + 1,
                   "title": c.get("title") or "", "summary": c.get("summary") or ""}
                  for i, c in enumerate(existing)]
        frm = int(batch_from or 0) or (len(existing) + 1)

        # ---- v1.51 主笔自主调研：先让主笔点名"还要确认什么"，再由代码去查 ----
        # 只对**续批**做（首批没有已写内容，没什么可查）；查询意图由模型给，执行是本地 SQL。
        research_notes: list[dict] = []
        research_text = ""
        if existing:
            from app.services.engine import chief_research

            brief: dict = {}
            try:
                brief = await self.get_book_brief(book_id)
            except Exception as e:  # noqa: BLE001  脉络读不到就少给点线索，不挡规划
                logger.warning("[主笔] 调研前读脉络失败 book=%s：%s", book_id, e)
            queries = await chief_research.plan_queries(
                llm_client, str(brief.get("text") or ""), direction, frm)
            if not queries:
                queries = chief_research.fallback_queries(brief)
            if queries:
                research_notes = await chief_research.run_queries(self.repo, book_id, queries)
                research_text = chief_research.render_research(research_notes)
                logger.info("[主笔] 自主调研 %d 条查询：%s", len(queries),
                            [q.get("q") for q in queries])

        plan = await plan_skelly(direction, context or book.get("synopsis", ""),
                                 inspirations=inspirations or None,
                                 batch_from=frm, batch_size=max(0, int(batch_size or 0)),
                                 locked=locked, research=research_text)
        chs = plan.get("chapters") or []
        batch = plan.get("batch") or {"from": 1, "to": len(chs), "asked": 0,
                                      "got": len(chs), "trimmed": 0}
        return {"book_id": book_id, "plan": plan,
                "existing_chapters": len(existing), "batch": batch,
                # v1.51：主笔这次自己查了什么（预览卡里给作者看，不藏）
                "research": research_notes}

    async def _book_prose_stats(self, book_id: str) -> dict:
        """该书已落库正文的规模（B33 骨架替换前的防误删闸门用）。"""
        written, chars = 0, 0
        for ch in await self.repo.list_chapters_by_book(book_id):
            for s in await self.repo.list_scenes_by_chapter(ch["id"]):
                body = str(s.get("final_prose") or "").strip()
                if body:
                    written += 1
                    chars += len(body)
        return {"scenes": written, "chars": chars}

    async def commit_book_plan(self, book_id: str, plan: dict, *, mode: str = "append",
                               allow_overwrite: bool = False) -> dict:
        """确认骨架 → 落库：worldview/world_rules + chapters/scenes + foreshadows。

        mode="append"（默认 · A4 分批交付）：只把本批章节接到书末，**已有章节/场景/正文一律不动**。
        mode="replace"：原"全量替换"语义（先清空该书章节再重建）。替换会级联删除场景及其
        正文（Chapter.scenes cascade=all,delete-orphan）——所以**书里已有正文时必须显式
        allow_overwrite=True**，否则抛 ConflictError(409)：B33「点一下骨架重排就悄悄丢稿」。
        """
        book = await self.repo.get_book(book_id)
        if book is None:
            raise SceneNotFoundError(f"未找到书: {book_id}")

        from app.services.engine.beats import parse_beats

        mode = "replace" if str(mode or "").lower() == "replace" else "append"
        chapters = plan.get("chapters") or []
        if not chapters:
            raise InvalidActionError("骨架预览里没有章节，没什么可落库的（可先让主笔排一批）")

        existing_chapters = await self.list_chapters(book_id)
        stats = await self._book_prose_stats(book_id) if existing_chapters else {"scenes": 0, "chars": 0}
        if mode == "replace" and stats["scenes"] and not allow_overwrite:
            raise ConflictError(
                f"本书已有 {stats['scenes']} 场正文（约 {stats['chars']} 字）：全量替换会连正文一起删掉。"
                "要覆盖请显式传 allow_overwrite=true；只想续写就用默认的增量落库（mode=append）"
            )

        # 1) 世界观 + 规则解析（LLM 转结构化，检测阶段免费）。
        #    追加批次（已有章节）不带 worldview → 不动既有设定，避免每批都把世界观重写一遍。
        first_batch = (mode == "replace") or not existing_chapters
        worldview = plan.get("worldview") or {}
        from app.services.engine.chief_planner import parse_world_rules

        world_rules: list[dict] = []
        if first_batch:
            rules_text = str(worldview.get("rules_text") or "")
            world_rules = await parse_world_rules(rules_text) if rules_text.strip() else []
            await self.repo.save_book({
                "id": book_id,
                "synopsis": worldview.get("premise") or book.get("synopsis") or "",
                "worldview_json": json.dumps(worldview, ensure_ascii=False),
                "world_rules_json": json.dumps(world_rules, ensure_ascii=False),
            })

        # 2) 替换 → 清空既有章节；追加 → 保留，章号从既有章数往后接
        replaced = 0
        base_order = len(existing_chapters)
        if mode == "replace":
            for ch in existing_chapters:
                await self.repo.delete_chapter(ch["id"])
            replaced = len(existing_chapters)
            base_order = 0

        scene_ids: list[str] = []
        for i, ch in enumerate(chapters):
            ch_title = ch.get("title", f"第{base_order + i + 1}章")
            cid = (await self.create_chapter(book_id, {
                "title": ch_title,
                "summary": ch.get("summary", ""),
                "order_no": base_order + i + 1,
                "tone": ch.get("tone", ""),
                "tension_curve": ch.get("tension_curve", ""),
                "word_target": int(ch.get("word_target") or 0),
            }))["id"]

            scenes = ch.get("scenes") or []
            for j, sc in enumerate(scenes):
                sc_title = sc.get("title", f"场景{j + 1}")
                # v1.54：主笔写下的场级必然事件（hard/soft + 锚点）落进 plan_cfg_json，
                # 供导演 prompt 注入 + 收束时 0-token 校验（见 engine/beats.py）
                scene_beats = parse_beats(sc.get("beats") or [])
                r = await self.create_scene(cid, {
                    "title": sc_title,
                    "stage_desc": sc.get("stage_desc", ""),
                    "goal": sc.get("goal", ""),
                    "content_desc": sc.get("content_desc", ""),
                    "plan_cfg_json": json.dumps({"beats": scene_beats}, ensure_ascii=False)
                    if scene_beats else "{}",
                    "cursor_pos": j + 1,
                    "initial_facts_json": json.dumps(
                        [{"id": f"F-{k:03d}", "text": t, "kind": "env", "visible_to": []}
                         for k, t in enumerate(sc.get("initial_facts") or [])],
                        ensure_ascii=False,
                    ),
                })
                scene_ids.append(r["id"])

        # 3) 伏笔埋设（buried 状态）
        f_count = 0
        for f in plan.get("foreshadow_plan") or []:
            if not f.get("text"):
                continue
            await self.repo.save_foreshadow({
                "id": f"f-{uuid.uuid4().hex[:10]}",
                "book_id": book_id,
                "type": f.get("type", "plot"),
                "text": f["text"],
                "status": "buried",
                "buried_scene": f.get("buried_scene", ""),
                "expected_close_scene": f.get("expected_close_scene", ""),
            })
            f_count += 1

        # 4) 分卷（v1.59）：主笔给了 volumes 就落卷表；没给则按 volume_size 兜底物化一次。
        #    稳定性：整本重排时整表替换；续批只补"本批章所在、且还不存在的卷"，合并后重写编号。
        vols_out: list[dict] = []
        try:
            plan_vols = [v for v in (plan.get("volumes") or []) if isinstance(v, dict)]
            existing_vols = await self.repo.list_book_volumes(book_id)
            total_ch = (base_order + len(chapters)) if mode == "append" else len(chapters)
            rows: list[dict] = []
            if plan_vols and (first_batch or mode == "replace" or not existing_vols):
                for i, v in enumerate(plan_vols):
                    try:
                        frm, to = int(v.get("from_seq") or 0), int(v.get("to_seq") or 0)
                    except (TypeError, ValueError):
                        continue
                    if frm <= 0 or to < frm:
                        continue
                    rows.append({"no": i + 1, "from": frm, "to": min(to, total_ch),
                                 "title": str(v.get("title") or ""),
                                 "arc_goal": str(v.get("arc_goal") or ""),
                                 "climax": str(v.get("climax") or "")})
            elif existing_vols and plan_vols:
                rows = [{"no": int(e.get("no") or 0), "from": int(e.get("range_from") or 0),
                         "to": int(e.get("range_to") or 0), "title": str(e.get("title") or ""),
                         "arc_goal": str(e.get("arc_goal") or ""),
                         "climax": str(e.get("climax") or "")} for e in existing_vols]
                known = {r["from"] for r in rows}
                for v in plan_vols:
                    try:
                        frm, to = int(v.get("from_seq") or 0), int(v.get("to_seq") or 0)
                    except (TypeError, ValueError):
                        continue
                    if frm <= 0 or to < frm or frm in known:
                        continue
                    rows.append({"no": 0, "from": frm, "to": min(to, total_ch),
                                 "title": str(v.get("title") or ""),
                                 "arc_goal": str(v.get("arc_goal") or ""),
                                 "climax": str(v.get("climax") or "")})
                rows.sort(key=lambda r: r["from"])
                for i, r in enumerate(rows):
                    r["no"] = i + 1
            if rows:
                await self.set_volume_config(book_id, volumes=rows)
            elif not existing_vols and total_ch > 0:
                await self.set_volume_config(book_id)   # 按 volume_size 物化兜底
            await self.sync_volume_assignments(book_id)
            vols_out = await self.repo.list_book_volumes(book_id)
        except Exception as e:  # noqa: BLE001  分卷失败不能回滚已落库的骨架
            logger.warning("[volume] 骨架落库时分卷失败 book=%s：%s", book_id, e)

        return {
            "book_id": book_id,
            "mode": mode,
            "first_batch": first_batch,
            "chapters": len(chapters),
            "scenes": len(scene_ids),
            "foreshadows": f_count,
            "rules": len(world_rules),
            "replaced_chapters": replaced,
            "volumes": len(vols_out),
            "total_chapters": (base_order + len(chapters)) if mode == "append" else len(chapters),
            "kept_prose_scenes": stats["scenes"] if mode == "append" else 0,
        }

    # ---------------------------------------------------------------- 灵感池接口（P0-1）
    async def list_inspirations(self, book_id: str) -> list[dict]:
        return await self.repo.list_inspirations(book_id)

    async def create_inspiration(self, book_id: str, data: dict) -> dict:
        """作者自建灵感卡（source=author，sort_order 排到末尾）。"""
        cards = await self.repo.list_inspirations(book_id)
        max_order = max([c.get("sort_order") or 0 for c in cards], default=0)
        card_id = f"insp-{uuid.uuid4().hex[:10]}"
        await self.repo.save_inspiration({
            "id": card_id, "book_id": book_id,
            "icon": data.get("icon", "✦"),
            "title": data.get("title", ""),
            "desc": data.get("desc", ""),
            "type": data.get("type", "plot"),
            "source": "author",
            "adopted": False,
            "sort_order": max_order + 1,
        })
        card = await self.repo.get_inspiration(card_id)
        return card or {"id": card_id, "book_id": book_id}

    async def generate_inspirations(self, book_id: str, direction: str = "") -> list[dict]:
        """主笔生成灵感卡（LLM，fallback 模板）并落库，返回新增卡列表。"""
        from app.services.engine.chief_planner import generate_cards

        cards = await generate_cards(direction)
        out: list[dict] = []
        for i, c in enumerate(cards):
            card_id = f"insp-{uuid.uuid4().hex[:10]}"
            await self.repo.save_inspiration({
                "id": card_id, "book_id": book_id,
                "icon": c.get("icon", "✦"), "title": c.get("title", ""),
                "desc": c.get("desc", ""), "type": c.get("type", "plot"),
                "source": "chief", "adopted": False,
                "sort_order": i,
            })
            card = await self.repo.get_inspiration(card_id)
            if card:
                out.append(card)
        return out

    async def set_inspiration_adopted(self, card_id: str, adopted: bool) -> None:
        ok = await self.repo.update_inspiration_adopted(card_id, adopted)
        if not ok:
            raise InvalidActionError(f"未找到灵感卡: {card_id}")

    async def delete_inspiration(self, card_id: str) -> None:
        await self.repo.delete_inspiration(card_id)

    async def update_inspiration(self, card_id: str, data: dict) -> dict:
        """编辑灵感卡（title/desc/type/icon 白名单，改不改 adopted）。"""
        cur = await self.repo.get_inspiration(card_id)
        if cur is None:
            raise InvalidActionError(f"未找到灵感卡: {card_id}")
        merged = dict(cur)
        for k in ("title", "desc", "type", "icon"):
            if k in data:
                merged[k] = data[k]
        await self.repo.save_inspiration(merged)
        return merged

    # ---------------------------------------------------------------- 主笔书级记忆（记忆域）+ 感知装配
    _MEMORY_TOPICS = {"direction", "setting", "constraint", "history", "preference"}

    async def list_memories(self, book_id: str, topic: str | None = None,
                            limit: int = 50) -> list[dict]:
        return await self.repo.list_memories(book_id, topic or None, limit)

    async def create_memory(self, book_id: str, data: dict) -> dict:
        """作者/主笔写下书级记忆。topic 白名单校验，content 非空。"""
        topic = str(data.get("topic") or "direction")
        if topic not in self._MEMORY_TOPICS:
            raise InvalidActionError(f"非法 topic: {topic!r}，可选 {sorted(self._MEMORY_TOPICS)}")
        content = str(data.get("content") or "").strip()
        if not content:
            raise InvalidActionError("记忆内容 content 不能为空")
        mid = data.get("id") or f"mem-{uuid.uuid4().hex[:10]}"
        await self.repo.save_memory({
            "id": mid, "book_id": book_id, "topic": topic,
            "content": content, "source": str(data.get("source") or "author"),
            "ts": int(time.time() * 1000),
        })
        return {"id": mid}

    async def update_memory(self, memory_id: str, data: dict) -> dict:
        topic = data.get("topic")
        if topic and topic not in self._MEMORY_TOPICS:
            raise InvalidActionError(f"非法 topic: {topic!r}")
        await self.repo.save_memory({**data, "id": memory_id})
        return {"id": memory_id}

    async def delete_memory(self, memory_id: str) -> None:
        await self.repo.delete_memory(memory_id)

    async def chief_perceive(self, book_id: str, limit: int = 20,
                             include_world_states: bool = True) -> str:
        """主笔感知装配：书树摘要 + 书级记忆 + 已采纳灵感卡 + 伏笔现状 → 上下文文本。

        所有主笔入口（对话/规划/正文生成）统一走此函数，杜绝「从零重排、多书串味」。

        include_world_states=False（v1.44 · B49）：**写手**侧关掉账本段——写手 prompt 里
        已有按场景相关性筛选过的世界状态块，这里再塞一份未过滤的会导致同一信息两遍、
        两套口径（实测记忆段 349 字里约 320 字是账本副本）。主笔/对话侧保持 True
        （B27b：作者手改是最高权威，要全量可见）。
        """
        parts: list[str] = []
        tree = await self.repo.get_book_tree(book_id)
        if tree:
            title = tree.get("title") or "（本书）"
            synopsis = ((tree.get("synopsis") or "").strip()) or "（未填写）"
            chapters = [(c.get("title") or "") for c in (tree.get("chapters") or [])]
            chap_txt = " / ".join(chapters) if chapters else "（暂无章节）"
            parts.append(f"书名：{title}\n一句方向：{synopsis}\n章节骨架：{chap_txt}")

        memories = await self.repo.list_memories(book_id, limit=limit)
        if memories:
            mem_lines = [f"- [{m['topic']}] {m['content']}" for m in memories]
            parts.append("书级记忆（已定设定/约束，务必遵守）：\n" + "\n".join(mem_lines))

        try:
            inspirations = await self.repo.list_inspirations(book_id)
            adopted = [c for c in inspirations if c.get("adopted")]
            if adopted:
                lines = "\n".join(
                    f"- {c.get('title')}：{c.get('desc', '')}"
                    for c in adopted if c.get("title")
                )
                parts.append("已采纳灵感卡（作为伏笔/设定，不得矛盾）：\n" + lines)
        except Exception:  # noqa: BLE001
            pass

        try:
            foreshadows = await self.repo.list_foreshadows(book_id)
            pending = [f for f in foreshadows if f.get("status") != "closed"]
            if pending:
                lines = "\n".join(
                    f"- {f.get('text')}（状态：{f.get('status')}，期望回收场：{f.get('expected_close_scene') or '未定'}）"
                    for f in pending
                )
                parts.append("伏笔现状（未回收伏笔，写作注意呼应/埋设）：\n" + lines)
        except Exception:  # noqa: BLE001
            pass

        try:
            # 世界状态现状（S1：作者手改 = 最高权威设定，全部注入并标注，不得过滤——B27b）
            # 例外：写手侧显式关闭（include_world_states=False），避免与写手自己的账本块重复
            world_states = await self.repo.list_world_states(book_id) if include_world_states else []
            if world_states:
                lines = "\n".join(
                    f"- [{w.get('kind')}] {w.get('name')}：{w.get('value')}"
                    + ("（作者手改，以此为准）" if w.get("edited") else "")
                    for w in world_states[:30]
                )
                parts.append("世界状态现状（战力/道具/时间/术语/关键数字，写作保持与账本一致）：\n" + lines)
        except Exception:  # noqa: BLE001
            pass

        # v1.50：全局脉络一页纸（0-token 派生）——主笔"掌管全局"靠这个，不靠把全文塞进来。
        # 只在主笔/对话侧注入（include_world_states=True）；写手侧已有按章召回的素材，别重复。
        if include_world_states:
            try:
                from app.services.engine.book_brief import render_brief

                brief = await self.get_book_brief(book_id)
                text = render_brief(brief, budget=1200)
                if text:
                    parts.append(text)
            except Exception as e:  # noqa: BLE001  脉络失败不能挡住主笔对话
                logger.warning("[brief] 装配失败 book=%s：%s", book_id, e)

        return "\n\n".join(parts)

    # ---------------------------------------------------------------- 全局脉络（v1.50 · 0-token）
    async def get_book_brief(self, book_id: str) -> dict:
        """全书脉络一页纸 + 骨架体检（0-token，零 LLM；主笔与页面共用同一份）。"""
        from app.services.engine.book_brief import build_brief, render_brief

        book = await self.repo.get_book(book_id)
        if book is None:
            raise BookNotFoundError(f"未找到书籍: {book_id}")
        chapters = await self.repo.list_chapters_by_book(book_id)
        scenes_by_chapter: dict[str, list[dict]] = {}
        for ch in chapters:
            cid = str(ch.get("id") or "")
            scenes_by_chapter[cid] = await self.repo.list_scenes_by_chapter(cid) if cid else []
        brief = build_brief(
            book=book, chapters=chapters, scenes_by_chapter=scenes_by_chapter,
            characters=await self.repo.list_characters_by_book(book_id),
            foreshadows=await self.repo.list_foreshadows(book_id),
            world_states=await self.repo.list_world_states(book_id),
            beliefs=await self.repo.list_beliefs(book_id),
        )
        brief["text"] = render_brief(brief)
        brief["book_id"] = book_id
        return brief

    async def chapter_production_plan(self, chapter_id: str) -> dict:
        """本章该怎么生产（draft 直写 / emerge 先推演 / hybrid 关键场先演）+ 还差什么。

        v1.52：把「去 /write 还是去 /director」从作者记性变成数据裁决——
        写作页照 plan.next_action 引导，不再靠人记流程。
        """
        from app.services.engine.chapter_production import plan_production

        chapter = await self.repo.get_chapter(chapter_id)
        if chapter is None:
            raise ChapterNotFoundError(f"未找到章节: {chapter_id}")
        book_id = str(chapter.get("book_id") or "")
        scenes = await self.repo.list_scenes_by_chapter(chapter_id)
        arcs = await self._archives_by_scene(book_id) if book_id else {}
        turns_by_scene = {sid: len(v or []) for sid, v in (arcs or {}).items()}
        plan = plan_production(chapter=chapter, scenes=scenes,
                               turns_by_scene=turns_by_scene)
        plan["chapter_id"] = chapter_id
        plan["book_id"] = book_id
        plan["title"] = str(chapter.get("title") or "")
        return plan

    async def set_chapter_mode(self, chapter_id: str = "", mode: str = "",
                               production: dict | None = None, book_id: str = "",
                               chapter_no: int = 0) -> dict:
        """写章级制作单（主笔裁决 / 作者覆盖）：mode + must_rehearse/beats/hook。

        边界：主笔**只写结构**（这一章怎么生产），不碰正文——正文仍由执行层写。
        """
        from app.services.engine.book_brief import MODES

        # 工具调用侧只有「第几章」（模型不该被要求背 chapter_id），这里按书+章号解析
        if not chapter_id and book_id and chapter_no:
            for ch in await self.repo.list_chapters_by_book(book_id):
                if int(ch.get("order_no") or 0) == int(chapter_no):
                    chapter_id = str(ch.get("id") or "")
                    break
        if not chapter_id:
            raise ChapterNotFoundError("缺少 chapter_id（或 book_id + chapter_no）")
        chapter = await self.repo.get_chapter(chapter_id)
        if chapter is None:
            raise ChapterNotFoundError(f"未找到章节: {chapter_id}")
        cur_mode = str(chapter.get("mode") or "draft")
        new_mode = str(mode or cur_mode).strip() or "draft"
        if new_mode not in MODES:
            raise InvalidActionError(f"非法 mode: {new_mode!r}（可选 {list(MODES)}）")
        prod = _parse_json_obj(chapter.get("production_json"))
        if production:
            if not isinstance(production, dict):
                raise InvalidActionError("production 必须是对象")
            for k, v in production.items():
                # None = **删除这个键**：否则"清空制作单"永远做不到（merge 会把旧值留着，
                # 实测把 mode 改回 draft 后 must_rehearse/hook 还挂在那儿）
                if v is None:
                    prod.pop(k, None)
                else:
                    prod[k] = v
        await self.repo.save_chapter({**chapter, "mode": new_mode,
                                      "production_json": json.dumps(prod, ensure_ascii=False)})
        return {"chapter_id": chapter_id, "mode": new_mode, "production": prod}

    # ---------------------------------------------------------------- 正文协作工作区（阶段④）
    async def _scene_and_book(self, scene_id: str):
        """场景 + 其所属书 id（反查，供账本装配）。"""
        scene = await self.repo.get_scene(scene_id)
        if scene is None:
            raise SceneNotFoundError(f"未找到场景: {scene_id}")
        book_id = ""
        chapter = await self.repo.get_chapter(scene.get("chapter_id") or "")
        if chapter:
            book_id = chapter.get("book_id") or ""
        return scene, book_id

    # ------------------------------------------------------------------
    # 任务总线（L3 · A+ 批）：请求 / 查看 / 流转
    # ------------------------------------------------------------------
    async def agent_task_create(
        self, scene_id: str, goal: str = "", kind: str = "rehearsal",
        to_agent: str = "stage_manager", from_agent: str = "author",
    ) -> dict:
        """发起一条任务（如"请求彩排"）。发起者越界 → TaskDenied（路由转 409）。"""
        from app.services.agents.tasks import create_task, task_dict

        book_id = ""
        scene = await self.repo.get_scene(scene_id) or {}
        if scene.get("chapter_id"):
            ch = await self.repo.get_chapter(scene["chapter_id"])
            book_id = (ch or {}).get("book_id", "")
        task = await create_task(
            self.repo, from_agent=from_agent, to_agent=to_agent, kind=kind,
            goal=goal or f"让角色先演一遍《{scene.get('title', '本场')}》",
            scene_id=scene_id, book_id=book_id,
            input_ref={"scene_id": scene_id, "goal": scene.get("goal", "")},
        )
        return task_dict(task)

    async def agent_task_list(self, scene_id: str = "", book_id: str = "", status: str = "") -> list[dict]:
        from app.services.agents.tasks import task_dict

        rows = await self.repo.list_tasks(scene_id=scene_id, book_id=book_id, status=status)
        return [task_dict(r) for r in rows]

    async def agent_task_transition(self, task_id: str, status: str, note: str = "") -> dict:
        """推进任务状态（状态机校验在 tasks.transition）。"""
        from app.services.agents.tasks import task_dict, transition

        out = await transition(self.repo, task_id, status, note=note)
        return task_dict(out)

    async def agent_task_messages(self, task_id: str) -> list[dict]:
        return await self.repo.list_messages(task_id)

    async def character_voices(self, scene_id: str, text: str = "") -> dict:
        """E 批 · **角色在场感**：请角色就当前正文说一句（只读意见卡，不改正文/账本）。"""
        from app.services.agents.character_voice import build_voices

        return await build_voices(self, scene_id, text)

    async def prose_quality_score(self, scene_id: str, text: str = "") -> dict:
        """**评审**（D 批给身份）：A2 的 0-token 合分 + LLM 五维自评（只读，不改正文）。"""
        from app.services.engine.quality import score_text

        scene, _ = await self._scene_and_book(scene_id)
        chars = await self.repo.list_characters_for_scene(scene_id) if scene_id else []
        body = (text or "").strip() or str(scene.get("final_prose") or "")
        if not body:
            return {"total": None, "scores": {}, "weak_points": [], "note": "正文为空，无评分"}
        return await score_text(llm_client, body, characters=chars)

    async def prose_review_meeting(self, scene_id: str, text: str = "") -> dict:
        """D 批 · **审稿会**：体检 → 润色（仅当有问题）→ 质检 → 评审，一次跑完并给裁决。"""
        from app.services.agents.review_meeting import run_meeting

        return await run_meeting(self, scene_id, text)

    async def _draft_context(self, scene_id: str) -> dict:
        """装配写手上下文（v1.44 抽出）：prose_draft 与 prose_draft_stream 共用同一份。

        三处修正（Wiki B48/B49/B50）：
          - B48：世界状态改用 scene_context.select_world_states —— item/term 改为「本场设定或
            正文里出现过就注入」，此前拿物品名去比角色名集合，导致这两类**永远进不来**；
          - B49：chief_perceive(include_world_states=False)，账本只由本函数注入一次（口径唯一）；
          - B50：代词脏行（name=「他」）被闸门拦住，且**不静默**——被裁项写进日志。
        """
        from app.services.agents.constraints import build_constraints
        from app.services.agents.constraints import render as render_constraints
        from app.services.engine.scene_context import drop_note, select_world_states

        scene, book_id = await self._scene_and_book(scene_id)
        memory = (await self.chief_perceive(book_id, limit=8, include_world_states=False)
                  if book_id else "")
        constraints_text = (render_constraints(await build_constraints(self.repo, book_id))
                            if book_id else "")
        characters = await self.repo.list_characters_for_scene(scene_id) if scene_id else []

        all_ws = (await self.repo.list_world_states(book_id)) if book_id else []
        existing = str(scene.get("final_prose") or "")
        scene_text = " ".join(str(scene.get(k) or "") for k in
                             ("title", "stage_desc", "goal", "content_desc"))
        cast_names = {c.get("name") or "" for c in characters if c.get("name")}
        world_states, dropped = select_world_states(
            all_ws, cast_names, scene_text=scene_text, prose_text=existing)
        if dropped:
            logger.info("[draft] 世界状态注入 %d/%d；裁掉：%s",
                        len(world_states), len(all_ws), drop_note(dropped))

        # 前一场景正文摘要（同章节内 cursor_pos 更小的最后一个已定稿场景）
        prev_prose = ""
        chapter_id = scene.get("chapter_id") or ""
        if chapter_id:
            try:
                all_scenes = await self.repo.list_scenes_by_chapter(chapter_id)
                cur_pos = scene.get("cursor_pos") or 0
                prev_scenes = [s for s in all_scenes
                               if (s.get("cursor_pos") or 0) < cur_pos
                               and s.get("final_prose", "").strip()]
                if prev_scenes:
                    prev_scenes.sort(key=lambda s: s.get("cursor_pos") or 0, reverse=True)
                    prev_text = prev_scenes[0].get("final_prose", "")
                    prev_prose = prev_text[:200] + ("……" if len(prev_text) > 200 else "")
            except Exception:  # noqa: BLE001
                pass

        # S4 桥接：已采纳的涌现高光（灵感池 source=emergence 且 adopted=True）+ 角色动机依据
        emergence = await self._emergence_materials(book_id) if book_id else []
        motives = await self._scene_motives(scene_id, book_id) if book_id else []
        return {"scene": scene, "book_id": book_id, "memory": memory,
                "constraints": constraints_text, "characters": characters,
                "world_states": world_states, "dropped": dropped, "existing": existing,
                "prev_prose": prev_prose, "emergence": emergence, "motives": motives}

    async def _save_writer_note(self, scene_id: str, before: str, after: str,
                                note: str = "") -> None:
        """落一条 writer 追溯记录（流式与非流式共用）。"""
        await self.repo.save_prose_note({
            "id": f"note-{uuid.uuid4().hex[:10]}",
            "scene_id": scene_id, "kind": "writer", "status": "approved",
            "suggestion": (note or "写手生成正文初稿") if after else "（模型未接入，初审稿为空由前端提示）",
            "before": before, "after": after, "created_by": "writer",
            "ts": int(time.time() * 1000),
        })

    async def prose_draft(self, scene_id: str) -> dict:
        """写手：生成正文初稿（注入角色卡 + 前文摘要 + 场景相关账本），并留 writer 追溯记录。"""
        from app.services.engine.prose import draft_prose

        ctx = await self._draft_context(scene_id)
        text = await draft_prose(llm_client, ctx["scene"], ctx["memory"], ctx["existing"],
                                 characters=ctx["characters"], prev_prose=ctx["prev_prose"],
                                 emergence=ctx["emergence"], motives=ctx["motives"],
                                 world_states=ctx["world_states"], constraints=ctx["constraints"])
        await self._save_writer_note(scene_id, ctx["existing"], text)
        return {"text": text}

    async def prose_draft_stream(self, scene_id: str):
        """写手**流式**版（v1.44）：逐 token yield，中间正文区像打字机一样长出来。

        收尾必落 writer 审计：正常结束记「写手生成正文初稿（流式）」，**流式中断**也把已生成
        部分存下来并如实标注——LLM 已经花掉的钱不能白花，部分成果也不能静默丢。
        """
        from app.services.engine.prose import draft_prose_stream

        ctx = await self._draft_context(scene_id)
        acc: list[str] = []
        finished = False
        try:
            async for tok in draft_prose_stream(
                llm_client, ctx["scene"], ctx["memory"], ctx["existing"],
                characters=ctx["characters"], prev_prose=ctx["prev_prose"],
                emergence=ctx["emergence"], motives=ctx["motives"],
                world_states=ctx["world_states"], constraints=ctx["constraints"],
            ):
                acc.append(tok)
                yield tok
            finished = True
        finally:
            text = "".join(acc).strip()
            if text:
                try:
                    await self._save_writer_note(
                        scene_id, ctx["existing"], text,
                        "" if finished else "（流式中断：仅保存已生成部分）")
                except Exception:  # noqa: BLE001  收尾失败不能盖掉主流程
                    logger.warning("[draft] 流式收尾落审计失败", exc_info=True)

    # ---------------------------------------------------------------- 章级直写（v1.46）
    async def _chapter_context(self, chapter_id: str) -> dict:
        """装配**章级**写作上下文（直写链路）：章节 + 全书角色卡 + 相关账本 + 约束 + 记忆
        + 上一章结尾 + 本章场景大纲（有场时作为分段依据，没有就靠章摘要推进）。

        与场景级的区别：场景级只喂「本场角色 + 本场相关账本」；章级要喂全书角色与章级目标，
        因为**章是最小可写单位，场只是可选细分**。
        """
        from app.services.agents.constraints import build_constraints
        from app.services.agents.constraints import render as render_constraints
        from app.services.engine.prose import DEFAULT_WORD_TARGET
        from app.services.engine.scene_context import select_world_states

        chapter = await self.repo.get_chapter(chapter_id)
        if chapter is None:
            raise ChapterNotFoundError(f"未找到章节: {chapter_id}")
        book_id = str(chapter.get("book_id") or "")
        characters = (await self.repo.list_characters_by_book(book_id)) if book_id else []
        memory = (await self.chief_perceive(book_id, limit=8, include_world_states=False)
                  if book_id else "")
        constraints = (render_constraints(await build_constraints(self.repo, book_id))
                       if book_id else "")

        all_ws = (await self.repo.list_world_states(book_id)) if book_id else []
        existing = str(chapter.get("prose_md") or "")
        scenes = await self.repo.list_scenes_by_chapter(chapter_id)
        scene_lines = "\n".join(
            ("- " + str(s.get("title") or "（未命名场）")
             + ("：" + str(s.get("goal")) if str(s.get("goal") or "").strip() else ""))
            for s in scenes
        )
        cast = {c.get("name") or "" for c in characters if c.get("name")}
        scope_text = " ".join([str(chapter.get("summary") or ""), scene_lines])
        world_states, _dropped = select_world_states(
            all_ws, cast, scene_text=scope_text, prose_text=existing)

        # 上一章结尾（承接语感）：同书 order_no 更小的最近一章
        prev_tail = ""
        try:
            chapters = await self.repo.list_chapters_by_book(book_id)
            cur_no = int(chapter.get("order_no") or 0)
            prevs = [c for c in chapters if int(c.get("order_no") or 0) < cur_no]
            prevs.sort(key=lambda c: int(c.get("order_no") or 0), reverse=True)
            for p in prevs:
                body = str(p.get("prose_md") or "").strip()
                if body:
                    prev_tail = body[-600:]
                    break
        except Exception:  # noqa: BLE001  上一章读不到不影响本章
            prev_tail = ""

        target = int(chapter.get("word_target") or 0) or DEFAULT_WORD_TARGET
        scenes_text = ""
        if prev_tail:
            scenes_text += "【上一章结尾（承接语感，不要重复）】\n" + prev_tail + "\n\n"
        scenes_text += ("【本章场景大纲】\n" + scene_lines) if scene_lines else "（本章未拆场）"

        # v1.49 · 记忆检索：按本章目标召回「前情 / 相关角色 / 世界观 / 未回收伏笔」。
        # 与 prev_tail 的分工：prev_tail 保语感（上一章最后 600 字原样承接），
        # recall 保事实（前 9 章摘要、角色腔调、世界状态、伏笔状态）。
        recall_text = ""
        recall_bundle: dict = {}
        try:
            from app.services.rag import recall as rag_recall

            recall_bundle = await rag_recall.chapter_recall(self.repo, book_id, chapter_id)
            recall_text = rag_recall.render(recall_bundle)
        except Exception as e:  # noqa: BLE001  检索失败不能挡住写作
            logger.warning("[recall] 装配失败 chapter=%s：%s", chapter_id, e)

        # v1.58 · 前情压缩：全书 / 本卷 / 最近两章（有结构、有预算，替代"只有上一章结尾"）。
        prior_text = ""
        prior_bundle: dict = {}
        try:
            prior_bundle = await self.chapter_prior(chapter_id)
            prior_text = str(prior_bundle.get("text") or "")
        except Exception as e:  # noqa: BLE001  前情读不到不能挡住写作
            logger.warning("[digest] 前情装配失败 chapter=%s：%s", chapter_id, e)

        # v1.53 · 角色分层：次要/背景角色不进回合，但写手要知道"他们在场"（一笔带过，不抢戏）。
        background_text = ""
        try:
            from app.services.engine.cast_roles import background_block

            background_text = background_block(characters)
        except Exception as e:  # noqa: BLE001
            logger.warning("[cast] 背景角色装配失败 chapter=%s：%s", chapter_id, e)

        # v1.52 · 生产方式：emerge/hybrid 章要把「已推演的场」成果喂给写手（据成果渲染，不重演）。
        rehearsal_text = ""
        try:
            from app.services.engine.chapter_production import rehearsal_block

            arcs = await self._archives_by_scene(book_id) if book_id else {}
            turns_by_scene = {sid: len(v or []) for sid, v in (arcs or {}).items()}
            rehearsal_text = rehearsal_block(scenes=scenes, turns_by_scene=turns_by_scene)
        except Exception as e:  # noqa: BLE001  推演成果读不到不影响直写
            logger.warning("[production] 推演成果装配失败 chapter=%s：%s", chapter_id, e)

        return {"chapter": chapter, "book_id": book_id, "characters": characters,
                # v1.52：已推演的场（emerge/hybrid 拿来渲染的依据）
                "rehearsed_text": rehearsal_text,
                # v1.53：在场背景角色（不进回合，写手可一笔带过）
                "background_text": background_text,
                # v1.58：分层前情概要（prompt 用 prior_text，前端用 prior）
                "prior_text": prior_text, "prior": prior_bundle,
                "memory": memory, "constraints": constraints, "world_states": world_states,
                "existing": existing, "scenes_text": scenes_text, "word_target": target,
                # v1.47：把场清单原样带出，供分段编排按"场功能"推进
                "scenes": scenes,
                # v1.49：检索出来的记忆素材（prompt 用 recall_text，前端用 recall）
                "recall_text": recall_text, "recall": recall_bundle}

    async def save_chapter_prose(self, chapter_id: str, text: str) -> dict:
        """保存章正文（唯一真源：chapters.prose_md）→ **自动触发章级记账**（后台任务）。

        P0 闭环：保存 → 记账（对照伏笔/信念/世界状态账本）+ 复检（verify）→ 写账本，
        与场景级 save_scene_prose 对齐。同内容重复保存 unchanged 不触发（天然幂等）。
        """
        chapter = await self.repo.get_chapter(chapter_id)
        if chapter is None:
            raise ChapterNotFoundError(f"未找到章节: {chapter_id}")
        book_id = str(chapter.get("book_id") or "")
        body = (text or "").strip()
        unchanged = str(chapter.get("prose_md") or "").strip() == body
        # v1.61：覆盖前先把**旧正文**存成版本（append-only）。
        # 这是"保存即覆盖"唯一的后悔药：没有它，一次坏写（B58 那类）就真丢了。
        # 首次保存（旧文为空）不存空版本 —— 历史里只留"真有内容的稿"。
        prev_prose = str(chapter.get("prose_md") or "")
        if not unchanged and prev_prose.strip():
            try:
                await self.repo.save_chapter_revision(chapter_id, prev_prose, source="author")
            except Exception as e:  # noqa: BLE001  版本失败不能挡保存
                logger.warning("[revisions] 旧正文存版本失败 chapter=%s：%s", chapter_id, e)
        if not await self.repo.save_chapter_prose(chapter_id, body):
            raise ChapterNotFoundError(f"未找到章节: {chapter_id}")

        bookkeeping = "skip"
        if body and not unchanged:
            try:
                from app.services.tasks_worker import enqueue as job_enqueue

                await job_enqueue("chapter_bookkeeping", {"chapter_id": chapter_id, "text": body})
                bookkeeping = "queued"
            except Exception as e:  # noqa: BLE001  队列不可用退回进程内任务（T9：不静默）
                logger.warning("[bookkeeping] 章级入队失败，退回 asyncio 任务：%s", e)
                asyncio.create_task(self.chapter_bookkeeping(chapter_id, body))
                bookkeeping = "fallback"
        # v1.49 · 记忆索引保鲜：正文变了，索引里这一章的「摘要 + 正文尾部」就旧了，
        # 写下一章时的「前情提要」会缺最新进展。顺手入队重建（幂等；无向量时单本 ~40ms）。
        reindex = "skip"
        if body and not unchanged:
            try:
                from app.services.tasks_worker import enqueue as job_enqueue

                await job_enqueue("book_reindex", {"book_id": str(book_id)})
                reindex = "queued"
            except Exception as e:  # noqa: BLE001  索引旧了不影响正文，但要说出来
                logger.warning("[rag] 记忆索引入队失败 %s：%s", chapter_id, e)
                reindex = "failed"
        # v1.58：正文变了 → 前情概要要重算（后台；卷末会顺手重算卷级概要）
        digest = "skip"
        if body and not unchanged:
            try:
                from app.services.tasks_worker import enqueue as job_enqueue

                await job_enqueue("chapter_digest", {"chapter_id": chapter_id})
                digest = "queued"
            except Exception as e:  # noqa: BLE001
                logger.warning("[digest] 前情压缩入队失败 %s：%s", chapter_id, e)
                digest = "failed"
        return {"chapter_id": chapter_id, "saved": True, "words": prose_chars(body),
                "unchanged": unchanged, "bookkeeping": bookkeeping, "reindex": reindex,
                "digest": digest}

    async def chapter_bookkeeping(self, chapter_id: str, text: str) -> dict:
        """**章级记账 + 复检**（v1.46 · P0 闭环）：章正文对照账本 → 写伏笔/信念/世界状态。

        与场景级 _bookkeep_after_save 的差别：
          - 角色取「全书角色」（章级没有「本场角色」概念）
          - scene_no 用章的 order_no（账本要能定位到第几章发生的）
          - **暂不落 prose_notes**：该表 scene_id 是必填外键，章级审计需要加列（P1 待办）
        失败只告警——记账不能回滚已保存的正文。
        """
        from app.services.engine.prose import verify_prose

        chapter = await self.repo.get_chapter(chapter_id)
        if chapter is None:
            raise ChapterNotFoundError(f"未找到章节: {chapter_id}")
        book_id = str(chapter.get("book_id") or "")
        body = (text or "").strip()
        empty = {"written": 0, "foreshadow": 0, "beliefs": 0, "states": 0, "risks": 0}
        if not book_id or not body:
            return {**empty, "skipped": "空章节或空正文"}
        if not llm_client.available:
            return {**empty, "skipped": "LLM 未接入（跳过记账，不静默：/health.llm 有记录）"}
        try:
            foreshadows = await self.repo.list_foreshadows(book_id)
            beliefs = await self.repo.list_beliefs(book_id)
            world_states = await self.repo.list_world_states(book_id)
            characters = await self.repo.list_characters_by_book(book_id)
            opinion = await verify_prose(llm_client, body, foreshadows, beliefs, world_states,
                                         characters=characters)
            written = await self._apply_verify_bookkeeping(
                book_id, opinion, source_event_id="CHAPTER_SAVE",
                scene_no=int(chapter.get("order_no") or 0))
            result = {**empty, "written": written,
                      "foreshadow": len(opinion.get("foreshadow_updates") or []),
                      "beliefs": len(opinion.get("belief_deltas") or []),
                      "states": len(opinion.get("state_deltas") or []),
                      "risks": len(opinion.get("risks") or [])}
            logger.info("[chapter-bookkeeping] %s 写入 %d 条：%s", chapter_id, written, result)
            return result
        except Exception as e:  # noqa: BLE001  记账失败不影响已保存正文
            logger.warning("[chapter-bookkeeping] 失败 %s：%s", chapter_id, e)
            return {**empty, "error": f"{type(e).__name__}: {e}"}

    async def chapter_draft_stream(self, chapter_id: str):
        """**章级直写**流式：按字数目标分段写满（一次写完本章，逐 token yield）。

        与场景级 prose_draft_stream 的区别：不是「生成一段候选」，而是「把一章写完」。
        段数由 word_target 决定（上限 MAX_SEGS 防跑飞），段与段之间自动承接；
        落库仍由作者点保存（前端 PUT）——保持「候选 → 采纳 → 落库」的一致性。
        """
        from app.services.engine.prose import SEG_CHARS, chapter_prompt, chapter_segment_goals

        ctx = await self._chapter_context(chapter_id)
        # v1.47 · P1 章级编排：有场时按场功能分段（每场一段，段目标带场标题+本场目标）
        goals = chapter_segment_goals(ctx["word_target"], scenes=ctx.get("scenes"))
        seg_target = max(300, int(ctx["word_target"]) // max(1, len(goals)))
        written = ctx["existing"]
        for i, goal in enumerate(goals, start=1):
            prompt = chapter_prompt(
                chapter=ctx["chapter"], characters=ctx["characters"],
                world_states=ctx["world_states"], constraints=ctx["constraints"],
                memory=ctx["memory"], scenes=ctx["scenes_text"], written=written,
                prior=ctx.get("prior_text") or "",
                recall=ctx.get("recall_text") or "",
                rehearsed=ctx.get("rehearsed_text") or "",
                background=ctx.get("background_text") or "",
                seg_no=i, seg_total=len(goals), seg_goal=goal, seg_target=seg_target)
            seg: list[str] = []
            if i > 1:
                yield "\n\n"          # 段间空行：前端直接拼接即成段落
            async for tok in llm_client.chat_stream(prompt, temperature=0.85):
                seg.append(tok)
                yield tok
            piece = "".join(seg).strip()
            if piece:
                written = (written + "\n\n" + piece).strip() if written.strip() else piece

    # ---------------------------------------------------------------- 记忆检索（RAG · v1.49）
    async def chapter_recall(self, chapter_id: str) -> dict:
        """本章会注入写手 prompt 的记忆素材（前端「记忆」面板与后端同一份口径）。"""
        from app.services.rag import recall as rag_recall

        chapter = await self.repo.get_chapter(chapter_id)
        if chapter is None:
            raise ChapterNotFoundError(f"未找到章节: {chapter_id}")
        book_id = str(chapter.get("book_id") or "")
        bundle = await rag_recall.chapter_recall(self.repo, book_id, chapter_id)
        bundle["text"] = rag_recall.render(bundle)
        bundle["book_id"] = book_id
        return bundle

    async def memory_search(self, book_id: str, query: str,
                            kinds: object = None, top_k: object = None) -> dict:
        """工具 memory.search 的实现：对话里查「前情 / 角色 / 世界观 / 伏笔」。"""
        from app.config import settings as _settings
        from app.services.rag import recall as rag_recall

        parsed: list[str] | None = None
        if isinstance(kinds, str):
            parsed = [k.strip() for k in kinds.replace("，", ",").split(",") if k.strip()]
        elif isinstance(kinds, (list, tuple)):
            parsed = [str(k).strip() for k in kinds if str(k).strip()]
        try:
            k = int(top_k) if top_k is not None else int(_settings.rag_top_k or 8)
        except (TypeError, ValueError):
            k = int(_settings.rag_top_k or 8)
        return await rag_recall.search_for_tool(self.repo, book_id, query, parsed, k)

    async def book_memory_stats(self, book_id: str) -> dict:
        """记忆索引统计（前端「记忆」面板 + /health.rag）。"""
        from app.services.rag import embedder, store

        data = await store.stats(book_id)
        data["book_id"] = book_id
        data["embedding"] = embedder.status()
        return data

    async def reindex_book_memory(self, book_id: str) -> dict:
        """全量重建记忆索引（arq 任务 book_reindex 的实际执行体）。"""
        from app.services.rag import indexer

        return await indexer.reindex_book(self.repo, book_id)


    # ---------------------------------------------------------------- P1：设定补齐 + 章级巡检（v1.48）
    async def ensure_chapter_settings(self, chapter_id: str, force: bool = False) -> dict:
        """补齐章摘要 + 各场设定（舞台/本场目标/内容描述）。

        规则：**只补空字段，绝不覆盖作者手写**（force=True 才覆盖）。
        动机：真实书场景设定填充率 0/3 → 写手自由发挥 → 体检必打回（「本场设定栏全空」）。
        返回 {filled, skipped?/error?, chapter_summary, scenes}，调用方据此如实汇报。
        """
        from app.services.agents.constraints import build_constraints
        from app.services.agents.constraints import render as render_constraints
        from app.services.engine.prose import generate_chapter_settings

        chapter = await self.repo.get_chapter(chapter_id)
        if chapter is None:
            raise ChapterNotFoundError(f"未找到章节: {chapter_id}")
        book_id = str(chapter.get("book_id") or "")
        scenes = await self.repo.list_scenes_by_chapter(chapter_id)
        fields = ("stage_desc", "goal", "content_desc")

        def gaps(s: dict) -> list[str]:
            return [k for k in fields if not str((s or {}).get(k) or "").strip()]

        need_chapter = force or not str(chapter.get("summary") or "").strip()
        need_scenes = [s for s in scenes if gaps(s)]
        if not need_chapter and not need_scenes:
            if not scenes:
                return {"filled": 0, "skipped": "本章未拆场，且章摘要已填（无可补项）"}
            return {"filled": 0, "skipped": "章摘要与各场设定都已完整"}
        if not scenes:
            return {"filled": 0, "skipped": "本章未拆场：补设定需要至少一个场（可先去主笔拆场）"}
        if not llm_client.available:
            return {"filled": 0, "skipped": "LLM 未接入（未补）"}

        characters = (await self.repo.list_characters_by_book(book_id)) if book_id else []
        constraints = (render_constraints(await build_constraints(self.repo, book_id))
                       if book_id else "")
        out = await generate_chapter_settings(llm_client, chapter, scenes, characters, constraints)
        if not out:
            return {"filled": 0, "error": "模型未返回可用设定（已重试，仍失败）"}

        filled = 0
        if need_chapter:
            cs = str(out.get("chapter_summary") or "").strip()
            if cs:
                await self.repo.save_chapter({**chapter, "summary": cs})
                filled += 1
        gen_list = out.get("scenes") if isinstance(out.get("scenes"), list) else []
        by_title = {str((g or {}).get("title") or "").strip(): g for g in gen_list}
        for idx, s in enumerate(scenes):
            gen = by_title.get(str(s.get("title") or "").strip())
            if not isinstance(gen, dict) and idx < len(gen_list) and isinstance(gen_list[idx], dict):
                gen = gen_list[idx]
            if not isinstance(gen, dict):
                continue
            patch: dict = {}
            for k in fields:
                cur = str(s.get(k) or "").strip()
                new = str(gen.get(k) or "").strip()
                if new and (force or not cur):
                    patch[k] = new
            if patch:
                await self.update_scene(s["id"], patch)
                filled += len(patch)
        # v1.49：补出来的摘要/场设定要进记忆索引（否则下一章检索不到这些设定）
        if filled or (need_chapter and out.get("chapter_summary")):
            try:
                from app.services.tasks_worker import enqueue as job_enqueue

                await job_enqueue("book_reindex", {"book_id": str(book_id)})
            except Exception as e:  # noqa: BLE001
                logger.warning("[rag] 补设定后记忆索引入队失败 %s：%s", chapter_id, e)
        return {"filled": filled, "chapter_summary": need_chapter,
                "scenes": len(scenes), "scenes_generated": len(gen_list)}

    # ---------------------------------------------------------------- 前情压缩（v1.58）
    async def _book_volume_bounds(self, book_id: str,
                                  chapters: list[dict] | None = None) -> list[dict]:
        """卷边界（v1.59）：**book_volumes 表是唯一真源**；表空时按 volume_size 派生（只读回退，不写库）。

        返回 [{no, from, to, id?, title?, derived?}]，`derived=True` 表示是回退出来的。
        """
        from app.services.engine.digest import volume_bounds

        rows = await self.repo.list_book_volumes(book_id)
        if rows:
            return [{"no": int(r.get("no") or 0), "from": int(r.get("range_from") or 0),
                     "to": int(r.get("range_to") or 0), "id": str(r.get("id") or ""),
                     "title": str(r.get("title") or ""), "derived": False} for r in rows]
        book = await self.repo.get_book(book_id) or {}
        chs = chapters if chapters is not None else await self.repo.list_chapters_by_book(book_id)
        custom = _parse_json_list(book.get("volumes_json"))
        return [{**b, "id": "", "title": "", "derived": True} for b in
                volume_bounds(len(chs or []), int(book.get("volume_size") or 8), custom)]

    async def sync_volume_assignments(self, book_id: str) -> dict:
        """把 chapters.volume_id 对齐到卷表 range（v1.59 · 幂等 + 自愈）。

        稳定性要点：
          - **只由这里写** volume_id（其他路径一律不写），避免两套真相；
          - 章序变了（增删/重排）调用它即可收敛，重复调用无副作用；
          - 落在任何卷之外的章 → volume_id=None（**不猜归属**），并在返回里列出来。
        """
        volumes = await self.repo.list_book_volumes(book_id)
        chapters = await self.repo.list_chapters_by_book(book_id)
        assigned = 0
        unassigned: list[int] = []
        for ch in chapters:
            no = int(ch.get("order_no") or 0)
            target = ""
            for v in volumes:
                if int(v.get("range_from") or 0) <= no <= int(v.get("range_to") or 0):
                    target = str(v.get("id") or "")
                    break
            if target:
                assigned += 1
            elif no > 0:
                unassigned.append(no)
            if str(ch.get("volume_id") or "") != target:
                await self.repo.save_chapter({**ch, "volume_id": target or None})
        return {"book_id": book_id, "volumes": len(volumes), "chapters": len(chapters),
                "assigned": assigned, "unassigned": unassigned}

    async def generate_chapter_digest(self, chapter_id: str) -> dict:
        """章级前情概要（后台任务）：正文 → 结构化 digest + 冲突检测。

        为什么只喂正文**尾部**：压缩看的是"这一章改变了什么"，尾部足以覆盖结果；
        带全文既贵又会诱发模型复述过程。
        """
        from app.services.engine.digest import (
            CHAPTER_DIGEST_SCHEMA, chapter_digest_prompt, detect_conflicts,
            normalize_chapter_digest, render_volume_digest, volume_of,
        )
        from app.services.engine.schema_retry import validate_and_retry

        chapter = await self.repo.get_chapter(chapter_id)
        if chapter is None:
            raise ChapterNotFoundError(f"未找到章节: {chapter_id}")
        book_id = str(chapter.get("book_id") or "")
        no = int(chapter.get("order_no") or 0)
        scenes = await self.repo.list_scenes_by_chapter(chapter_id)
        body = chapter_body(chapter, scenes)
        if not str(body or "").strip():
            return {"chapter_id": chapter_id, "skipped": "章正文为空（先写正文再压缩前情）"}
        if not llm_client.available:
            return {"chapter_id": chapter_id, "skipped": "LLM 未接入（未生成前情概要）"}

        bounds = await self._book_volume_bounds(book_id)
        vol = volume_of(no, bounds)
        vol_digest: dict = {}
        if vol:
            row = await self.repo.get_book_digest(book_id, "volume", int(vol["from"]))
            if row:
                vol_digest = _parse_json_obj(row.get("digest_json"))

        raw = await validate_and_retry(
            llm_client, chapter_digest_prompt(chapter=chapter, body=body,
                                              prior=render_volume_digest(vol_digest)),
            CHAPTER_DIGEST_SCHEMA, _validate_chapter_digest)
        if not isinstance(raw, dict):
            return {"chapter_id": chapter_id, "error": "模型未返回可用前情概要（已重试）"}
        digest = normalize_chapter_digest(raw, chapter_no=no)
        if not (digest.get("one_line") or digest.get("events")):
            return {"chapter_id": chapter_id, "error": "模型输出缺关键字段（one_line/events 皆空）"}
        conflicts = list(digest.get("conflicts") or [])
        conflicts += detect_conflicts(digest, vol_digest)
        await self.repo.save_chapter({**chapter, "digest_json": json.dumps(digest, ensure_ascii=False)})
        # v1.62：digest 是**记忆索引的上游**（章事实/知晓/钩子三块都从这里来），
        # 生成完不重建索引 = 检索侧永远读不到这一章的结构化前情。
        await self._reindex_quietly(book_id, reason="chapter_digest")
        return {"chapter_id": chapter_id, "chapter_no": no, "digest": digest,
                "conflicts": conflicts, "volume": vol}

    async def generate_volume_digest(self, book_id: str, volume_no: int = 0) -> dict:
        """卷级前情概要：本卷各章 digest → 一条卷级 digest（不读正文）。"""
        from app.services.engine.digest import (
            VOLUME_DIGEST_SCHEMA, normalize_volume_digest, render_volume_digest,
            volume_digest_prompt, volume_of,
        )
        from app.services.engine.schema_retry import validate_and_retry

        chapters = await self.repo.list_chapters_by_book(book_id)
        bounds = await self._book_volume_bounds(book_id, chapters)
        if not bounds:
            return {"book_id": book_id, "skipped": "这本书还没有章节"}
        targets = ([b for b in bounds if int(b["no"]) == int(volume_no)] if volume_no
                   else bounds)
        if not targets:
            return {"book_id": book_id, "skipped": f"没有第 {volume_no} 卷"}
        if not llm_client.available:
            return {"book_id": book_id, "skipped": "LLM 未接入"}

        done: list[dict] = []
        for vol in targets:
            in_range = [c for c in chapters
                        if vol["from"] <= int(c.get("order_no") or 0) <= vol["to"]]
            digests = []
            for c in in_range:
                d = _parse_json_obj(c.get("digest_json"))
                if d.get("one_line") or d.get("events"):
                    d.setdefault("chapter_no", int(c.get("order_no") or 0))
                    digests.append(d)
            if not digests:
                done.append({"volume_no": vol["no"], "skipped": "本卷还没有章级概要"})
                continue
            prev = await self.repo.get_book_digest(
                book_id, "volume", int(bounds[bounds.index(vol) - 1]["from"])) \
                if bounds.index(vol) > 0 else None
            raw = await validate_and_retry(
                llm_client,
                volume_digest_prompt(volume_no=vol["no"], range_from=vol["from"],
                                     range_to=vol["to"], chapters=digests,
                                     prior=render_volume_digest(_parse_json_obj((prev or {}).get("digest_json")))),
                VOLUME_DIGEST_SCHEMA, _validate_volume_digest)
            if not isinstance(raw, dict):
                done.append({"volume_no": vol["no"], "error": "模型未返回可用卷级概要"})
                continue
            vd = normalize_volume_digest(raw, volume_no=vol["no"],
                                         range_from=vol["from"], range_to=vol["to"])
            # v1.59：卷级概要挂到卷 id 上（range 同时保留，便于边界变更后核对），并清 stale。
            # 注意 id 用 vd- 前缀：内存态把所有实体放在同一个 dict 里，不能与卷行 id(vol-) 撞车。
            await self.repo.save_book_digest({
                "id": f"vd-{book_id}-{vol['no']}",
                "book_id": book_id, "level": "volume",
                "volume_id": str(vol.get("id") or ""),
                "range_from": vol["from"], "range_to": vol["to"],
                "digest_json": json.dumps(vd, ensure_ascii=False),
                "summary_md": str(vd.get("summary_md") or ""),
                "stale": False,
            })
            done.append({"volume_no": vol["no"], "range": [vol["from"], vol["to"]]})
        if any("range" in d for d in done):
            await self._reindex_quietly(book_id, reason="volume_digest")
        return {"book_id": book_id, "volumes": done}

    async def generate_book_digest(self, book_id: str) -> dict:
        """全书级前情概要：各卷 digest → 一条全书 digest（卷数少时等价于合并）。"""
        from app.services.engine.digest import (
            VOLUME_DIGEST_SCHEMA, normalize_volume_digest, volume_digest_prompt,
        )
        from app.services.engine.schema_retry import validate_and_retry

        rows = await self.repo.list_book_digests(book_id, "volume")
        vols = [_parse_json_obj(r.get("digest_json")) for r in rows]
        vols = [v for v in vols if v.get("arc") or v.get("characters")]
        if not vols:
            return {"book_id": book_id, "skipped": "还没有卷级概要（先生成卷级）"}
        if not llm_client.available:
            return {"book_id": book_id, "skipped": "LLM 未接入"}
        chapters = await self.repo.list_chapters_by_book(book_id)
        raw = await validate_and_retry(
            llm_client,
            volume_digest_prompt(volume_no=0, range_from=1,
                                 range_to=len(chapters), chapters=vols),
            VOLUME_DIGEST_SCHEMA, _validate_volume_digest)
        if not isinstance(raw, dict):
            return {"book_id": book_id, "error": "模型未返回可用全书概要"}
        bd = normalize_volume_digest(raw, volume_no=0, range_from=1, range_to=len(chapters))
        await self.repo.save_book_digest({
            "id": f"book-{book_id}", "book_id": book_id, "level": "book",
            "range_from": 1, "range_to": len(chapters),
            "digest_json": json.dumps(bd, ensure_ascii=False),
            "summary_md": str(bd.get("summary_md") or ""),
        })
        await self._reindex_quietly(book_id, reason="book_digest")
        return {"book_id": book_id, "volumes": len(vols), "digest": bd}

    async def _reindex_quietly(self, book_id: str, *, reason: str = "") -> str:
        """顺手重建记忆索引（不抛、不挡主流程）；返回 queued/fallback/failed/skip。

        v1.62：digest（章/卷/全书）是索引的上游，改了不重建 → 检索侧读到的是旧素材。
        队列可用就入队；不可用就进程内跑一次（一本书 ~50ms，比"悄悄过期"划算）。
        """
        if not book_id:
            return "skip"
        try:
            from app.services.tasks_worker import enqueue as job_enqueue

            await job_enqueue("book_reindex", {"book_id": book_id})
            return "queued"
        except Exception as e:  # noqa: BLE001  队列不可用退回进程内，别让索引悄悄过期
            logger.warning("[rag] %s 后索引入队失败，进程内重建 book=%s：%s", reason, book_id, e)
            try:
                await self.reindex_book_memory(book_id)
                return "fallback"
            except Exception as e2:  # noqa: BLE001
                logger.warning("[rag] %s 后索引重建失败 book=%s：%s", reason, book_id, e2)
                return "failed"

    async def set_volume_config(self, book_id: str, *, volume_size: int | None = None,
                                volumes: list[dict] | None = None) -> dict:
        """微调卷边界（v1.58）：主笔排骨架时可改，作者也能改。

        volume_size：默认每几章一卷；volumes：显式边界 [{from,to}]（给了就以它为准）。
        """
        from app.services.engine.digest import volume_bounds

        book = await self.repo.get_book(book_id)
        if book is None:
            raise BookNotFoundError(f"未找到书籍: {book_id}")
        chapters = await self.repo.list_chapters_by_book(book_id)
        total = len(chapters)

        # 目标卷结构：给了 volumes 就用它；否则按 volume_size 均分
        if volumes is not None:
            rows = [{"no": i + 1, "from": int(v.get("from") or 0), "to": int(v.get("to") or 0),
                     "title": str(v.get("title") or ""),
                     "arc_goal": str(v.get("arc_goal") or ""),
                     "climax": str(v.get("climax") or "")}
                    for i, v in enumerate(volumes) if isinstance(v, dict)]
            prev_to = 0
            for r in rows:
                if r["from"] <= prev_to or r["to"] < r["from"]:
                    raise InvalidActionError("卷边界必须递增且不重叠（from > 上一卷 to，to ≥ from）")
                prev_to = r["to"]
        else:
            size = int(volume_size if volume_size is not None
                       else (book.get("volume_size") or 8))
            if size < 1 or size > 50:
                raise InvalidActionError("volume_size 需在 1~50 之间")
            await self.repo.save_book({**book, "id": book_id, "volume_size": size})
            rows = [{**b, "title": "", "arc_goal": "", "climax": ""}
                    for b in volume_bounds(total, size)]

        # 整本替换卷结构（幂等：先清后建 + 同步归属 + 旧概要标 stale）
        await self.repo.clear_book_volumes(book_id)
        for r in rows:
            await self.repo.save_book_volume({
                "id": f"vol-{book_id}-{r['no']}", "book_id": book_id, "no": int(r["no"]),
                "title": str(r.get("title") or ""),
                "range_from": int(r["from"]), "range_to": int(r["to"]),
                "arc_goal": str(r.get("arc_goal") or ""),
                "climax": str(r.get("climax") or ""),
            })
        synced = await self.sync_volume_assignments(book_id)
        stale = await self._mark_volume_digests_stale(book_id)
        vols = await self.repo.list_book_volumes(book_id)
        return {"book_id": book_id, "volume_size": int(book.get("volume_size") or 8),
                "volumes": [{"no": v["no"], "title": v["title"], "from": v["range_from"],
                             "to": v["range_to"]} for v in vols],
                "bounds": await self._book_volume_bounds(book_id, chapters),
                "sync": synced, "digests_marked_stale": stale}

    async def list_book_volumes(self, book_id: str) -> list[dict]:
        """分卷列表（v1.59）：表为空时给出**派生**回退（derived=true，不写库）。"""
        rows = await self.repo.list_book_volumes(book_id)
        chapters = await self.repo.list_chapters_by_book(book_id)
        if rows:
            counts: dict[str, int] = {}
            for ch in chapters:
                vid = str(ch.get("volume_id") or "")
                if vid:
                    counts[vid] = counts.get(vid, 0) + 1
            return [{**r, "chapters": counts.get(str(r["id"]), 0), "derived": False,
                     "range": [r["range_from"], r["range_to"]]} for r in rows]
        bounds = await self._book_volume_bounds(book_id, chapters)
        return [{**b, "chapters": max(0, b["to"] - b["from"] + 1)} for b in bounds]

    async def _mark_volume_digests_stale(self, book_id: str) -> int:
        """卷边界变了 → 旧卷级/全书级概要标 stale（**不删内容**，提示待重算）。"""
        n = 0
        for d in await self.repo.list_book_digests(book_id):
            if d.get("stale"):
                continue
            await self.repo.save_book_digest({**d, "id": d["id"], "stale": True})
            n += 1
        return n

    async def list_chapter_revisions(self, chapter_id: str) -> list[dict]:
        """稿本历史（version 倒序，不含正文全文）。"""
        return await self.repo.list_chapter_revisions(chapter_id)

    async def get_chapter_revision(self, chapter_id: str, version: int) -> dict:
        """取某一版的完整正文。"""
        rev = await self.repo.get_chapter_revision(chapter_id, version)
        if rev is None:
            raise ChapterNotFoundError(f"未找到版本: 第 {version} 版")
        return rev

    async def restore_chapter_revision(self, chapter_id: str, version: int) -> dict:
        """恢复到某一版：把历史正文写回 prose_md，并**追加一条 restore 版本**。

        为什么不指针回拨：恢复本身也是一次修改，必须留历史 —— 否则"恢复到第 3 版"
        之后，第 4 版的内容就凭空消失了。
        """
        rev = await self.get_chapter_revision(chapter_id, version)
        body = str(rev.get("prose_md") or "")
        chapter = await self.repo.get_chapter(chapter_id)
        if chapter is None:
            raise ChapterNotFoundError(f"未找到章节: {chapter_id}")
        await self.repo.save_chapter_prose(chapter_id, body)
        new_version = await self.repo.save_chapter_revision(
            chapter_id, body, source=f"restore:{int(version)}")
        return {"chapter_id": chapter_id, "restored_from": int(version),
                "chars": len(body), "new_version": new_version}

    async def chapter_prior(self, chapter_id: str) -> dict:
        """本章的前情素材（全书 / 本卷 / 最近两章 + 冲突），给注入与 UI 共用。"""
        from app.services.engine.digest import render_prior_bundle, volume_of

        chapter = await self.repo.get_chapter(chapter_id)
        if chapter is None:
            raise ChapterNotFoundError(f"未找到章节: {chapter_id}")
        book_id = str(chapter.get("book_id") or "")
        no = int(chapter.get("order_no") or 0)
        chapters = await self.repo.list_chapters_by_book(book_id)
        bounds = await self._book_volume_bounds(book_id, chapters)
        vol = volume_of(no, bounds)
        book_row = await self.repo.get_book_digest(book_id, "book")
        vol_row = (await self.repo.get_book_digest(book_id, "volume", int(vol["from"]))
                   if vol else None)
        previous = [c for c in chapters if 0 < int(c.get("order_no") or 0) < no][-2:]
        recent = [_parse_json_obj(c.get("digest_json")) for c in reversed(previous)]
        # 冲突两类：①**本章自己的概要**发现的（作者该修，立即报警）；
        # ②最近章概要里记下的（可能还没修，延续提醒）。
        conflicts: list[dict] = []
        own = _parse_json_obj(chapter.get("digest_json"))
        for c in (own.get("conflicts") or []):
            conflicts.append({"chapter_no": no, "own": True, **c})
        for d in recent:
            for c in (d.get("conflicts") or []):
                conflicts.append({"chapter_no": int(d.get("chapter_no") or 0), **c})
        bundle = {
            "book": _parse_json_obj((book_row or {}).get("digest_json")),
            "volume": _parse_json_obj((vol_row or {}).get("digest_json")),
            "recent": recent,
            "conflicts": conflicts[:6],
            "volume_no": int(vol["no"]) if vol else 0,
            "volume_title": str((vol or {}).get("title") or ""),
            "volume_range": [int(vol["from"]), int(vol["to"])] if vol else [],
            "volumes": bounds,
            # v1.59：卷概要若因卷边界/章序变化而陈旧 → UI 提示"待重算"（不静默用旧数据）
            "volume_digest_stale": bool((vol_row or {}).get("stale")),
            "has_any": bool(book_row or vol_row or any(recent)),
        }
        bundle["text"] = render_prior_bundle(bundle)
        bundle["chapter_id"] = chapter_id
        bundle["chapter_no"] = no
        return bundle

    @staticmethod
    def _chapter_beats_summary(scenes: list[dict]) -> dict:
        """章内各场 beats 结论汇总（v1.57）：只报**已经推演过**的场，未推演的场不算未达成。"""
        unmet: list[dict] = []
        checked = 0
        for sc in scenes or []:
            cfg = _parse_json_obj(str(sc.get("plan_cfg_json") or "{}"))
            st = cfg.get("beats_status") or {}
            if not isinstance(st, dict) or not st:
                continue
            checked += 1
            for t in st.get("missed_texts") or []:
                if str(t).strip():
                    unmet.append({"scene": str(sc.get("title") or ""), "text": str(t)})
        return {"scenes_checked": checked, "unmet": unmet, "ok": not unmet}

    async def chapter_inspect(self, chapter_id: str) -> dict:
        """**章级巡检**（v1.48 · P1）：体检 ∥ 质检 → 评审 → 0-token 裁决 → 自动记账。

        与场景级审稿会的差别：以**章正文**为单位（场是可选细分），角色取全书角色。
        已知限制：prose_notes 的 scene_id 是必填外键 → 章级巡检暂不落审计记录（P1 待办）。
        """
        from app.services.agents.review_meeting import verdict_of
        from app.services.engine.prose import review_prose, verify_prose
        from app.services.engine.quality import score_text

        ctx = await self._chapter_context(chapter_id)
        body = str(ctx["existing"]).strip()
        if not body:
            return {"chapter_id": chapter_id, "skipped": "章正文为空（先点『写这一章』）"}
        if not llm_client.available:
            return {"chapter_id": chapter_id, "skipped": "LLM 未接入"}
        book_id = ctx["book_id"]
        characters = ctx["characters"]
        prior = style_voice_prior(body, characters)
        foreshadows = (await self.repo.list_foreshadows(book_id)) if book_id else []
        beliefs = (await self.repo.list_beliefs(book_id)) if book_id else []
        world_states = (await self.repo.list_world_states(book_id)) if book_id else []

        review, verify = await asyncio.gather(
            review_prose(llm_client, ctx["chapter"], body,
                         characters=characters, voice_prior=prior),
            verify_prose(llm_client, body, foreshadows, beliefs, world_states,
                         characters=characters, skip_voice=True),
        )
        issues = (review or {}).get("issues") or []
        voice = (review or {}).get("voice_findings") or []
        high = len([i for i in issues if str((i or {}).get("severity")) == "high"])
        opinion = verify or {}
        risks = list(opinion.get("risks") or [])
        for v in (voice or []):
            line = f"口吻：{v.get('char')}「{str(v.get('evidence') or '')[:24]}」{v.get('issue')}"
            if line not in risks:
                risks.append(line)
        score = await score_text(llm_client, body, characters=characters)
        total = score.get("total")
        dims = score.get("scores") or {}
        verdict = verdict_of(total, dims, high, len(risks))
        written = await self._apply_verify_bookkeeping(
            book_id, opinion, source_event_id="CHAPTER_INSPECT",
            scene_no=int(ctx["chapter"].get("order_no") or 0)) if book_id else 0
        return {
            "chapter_id": chapter_id,
            "words": prose_chars(body),
            "review": {"issues": len(issues), "high": high, "voice": len(voice),
                       "overall": str((review or {}).get("overall") or "")[:200],
                       "top": issues[:5]},
            "verify": {"risks": len(risks),
                       "foreshadow": len(opinion.get("foreshadow_updates") or []),
                       "beliefs": len(opinion.get("belief_deltas") or []),
                       "top_risks": risks[:5]},
            "score": {"total": total, "dims": dims},
            "verdict": verdict,
            "bookkeeping_written": written,
            # v1.57：本场必然事件达成情况（来自推演时落库的 beats_status；读不到就当没查）
            "beats": self._chapter_beats_summary(ctx.get("scenes") or []),
        }


    async def _chapters_cached(self, book_id: str) -> list[dict]:
        """章清单走 L1 进程内缓存（TTL 45s / 容量 256）——只放"丢了无所谓"的映射，不放 LLM 结果。"""
        from app.services import cache as cache_mod

        cached = cache_mod.l1_get("chapters", book_id)
        if cached is not None:
            return cached
        rows = await self.repo.list_chapters_by_book(book_id)
        cache_mod.l1_set("chapters", book_id, rows)
        return rows

    async def _resolve_chapter_ref(self, *, chapter_id: str = "", chapter_no: object = 0,
                                   book_id: str = "") -> tuple[dict | None, str]:
        """把「第 N 章」或内部 id 统一解析成章 dict（解析不了就返回原因，不抛）。

        为什么需要：主笔对话里作者只会说「第 3 章」，模型不可能知道内部 id
        （形如 chapter-c08f2b6b）。所以工具层必须能吃章号。
        """
        cid = str(chapter_id or "").strip()
        if cid:
            chapter = await self.repo.get_chapter(cid)
            return (chapter, "") if chapter else (None, f"未找到章节 {cid}")
        try:
            no = int(str(chapter_no).strip() or 0)
        except (TypeError, ValueError):
            no = 0
        if no <= 0:
            return None, "缺 chapter_id 或 chapter_no（章号，如 3）"
        if not book_id:
            return None, "缺 book_id（按章号审计需要知道是哪本书）"
        chapters = await self._chapters_cached(book_id)
        for ch in chapters:
            if int(ch.get("order_no") or 0) == no:
                return ch, ""
        total = len(chapters)
        return None, f"这本书没有第 {no} 章（当前共 {total} 章）"

    async def chapter_prose_audit(self, chapter_id: str = "", text: str = "",
                                  chapter_no: object = 0, book_id: str = "") -> dict:
        """**骨架遵循度审计**（v1.67 · 只读）：0-token 判定 + LLM 语义判定，不改一个字。

        作者诉求：主笔要能"看正文"，但不能改正文。所以这条链路只产出报告：
        偏离项 + 改法建议 + 该保持的东西；写库只写一条章级审计 note（kind=chief）。
        """
        from app.services.engine.chapter_audit import (
            AUDIT_SCHEMA, audit_prompt, checks_zero_token, normalize_audit,
            render_audit, validate_audit,
        )
        from app.services.engine.schema_retry import validate_and_retry

        chapter, why = await self._resolve_chapter_ref(
            chapter_id=chapter_id, chapter_no=chapter_no, book_id=book_id)
        if chapter is None:
            return {"chapter_id": str(chapter_id or ""), "error": why, "checks": [],
                    "deviation": None, "goal": {}, "rendered": "", "skipped": why}
        chapter_id = str(chapter.get("id") or "")
        book_id = str(chapter.get("book_id") or "")
        scenes = await self.repo.list_scenes_by_chapter(chapter_id)
        no = int(chapter.get("order_no") or 0)
        from app.services.text_metrics import chapter_body as _chapter_body

        body = (text or "").strip() or _chapter_body(chapter, scenes)
        volumes = (await self.repo.list_book_volumes(book_id)) if book_id else []
        volume = next((v for v in volumes
                       if int(v.get("range_from") or 0) <= no <= int(v.get("range_to") or 0)), None)
        characters = (await self.repo.list_characters_by_book(book_id)) if book_id else []
        protags = [str(c.get("name") or "") for c in characters
                   if str(c.get("role_tier") or "") in ("protagonist", "主角")]
        checks = checks_zero_token(chapter=chapter, body=body, scenes=scenes,
                                   volume=volume, protagonist_names=protags)
        deviation: dict | None = None
        skipped = ""
        # v1.69：审校结果走两级缓存（L2 Redis）+ single-flight —— 同一「正文 + 评测计划」
        # 在 24h 内不重复打 LLM；并发点同一个审计只跑一次。
        cache_info = {"key": "", "hit": False, "ttl": 0}
        payload: dict = {}
        if not body:
            skipped = "章正文为空（先写正文再审计）"
        elif not llm_client.available:
            skipped = "LLM 未接入（只跑 0-token 判定）"
        else:
            from app.services import cache as cache_mod

            body_hash = cache_mod.sha256_text(body)
            p_hash = cache_mod.plan_hash({
                "v": 1,
                "summary": str(chapter.get("summary") or ""),
                "tension_curve": str(chapter.get("tension_curve") or ""),
                "word_target": int(chapter.get("word_target") or 0),
                "volume": [str((volume or {}).get("arc_goal") or ""),
                           str((volume or {}).get("climax") or "")],
                "checks": [c.get("code") for c in checks],
            })
            key = cache_mod.audit_key(chapter_id, body_hash, p_hash)
            cache_info["key"] = key
            cache_info["ttl"] = cache_mod.AUDIT_TTL

            async def _compute_audit() -> dict:
                """只在**未命中缓存**时跑：跑完顺手落一条章级审计 note，并把 note_id 一起缓存。

                v1.69 的坑：note 在缓存之外创建 → 每次点审计都新增一条（表被重复噪声灌满）。
                现在 note 的创建属于"计算结果"的一部分：命中缓存就复用同一条 note。
                """
                try:
                    raw = await validate_and_retry(
                        llm_client,
                        audit_prompt(chapter=chapter, body=body, volume=volume, checks=checks),
                        AUDIT_SCHEMA, validate_audit,
                    )
                    if not isinstance(raw, dict):
                        return {"deviation": None, "skipped": "模型未返回可用审计", "note_id": ""}
                    dev = normalize_audit(raw)
                    rendered = render_audit({"checks": checks, "deviation": dev})
                    note = await self._save_chapter_note(
                        chapter_id, kind="chief", created_by="chief",
                        suggestion=(rendered or "骨架遵循度审计")[:600], before="",
                        payload={"audit": {"checks": checks, "deviation": dev}},
                    )
                    return {"deviation": dev, "skipped": "", "note_id": note}
                except Exception as e:  # noqa: BLE001  审计失败不 500：0-token 结论照样有用
                    logger.warning("[audit] LLM 判定失败 chapter=%s：%s", chapter_id, e)
                    return {"deviation": None,
                            "skipped": f"LLM 判定失败（{type(e).__name__}）：已给出 0-token 结论",
                            "note_id": ""}

            payload, computed = await cache_mod.single_flight(key, _compute_audit)
            payload = payload or {}
            deviation = payload.get("deviation")
            skipped = str(payload.get("skipped") or "")
            cache_info["hit"] = not computed
        report = {"chapter_id": chapter_id, "chapter_no": no,
                  "goal": {"chapter": str(chapter.get("summary") or ""),
                           "tension_curve": str(chapter.get("tension_curve") or ""),
                           "volume": str((volume or {}).get("arc_goal") or ""),
                           "climax": str((volume or {}).get("climax") or "")},
                  "checks": checks, "deviation": deviation, "skipped": skipped,
                  "cache": cache_info}
        report["rendered"] = render_audit(report)
        # note 只在"真正跑了模型"那条路径里创建（并且随结果一起缓存）：
        # 命中缓存 → 复用同一条 note；LLM 未接入 → 不落 note（避免每次点都灌一条噪声）。
        report["note_id"] = str(payload.get("note_id") or "")
        return report

    async def chapter_rewrite(self, chapter_id: str = "", instruction: str = "", scope: str = "chapter",
                              para_index: object = 0, scene_id: str = "", text: str = "",
                              chapter_no: object = 0, book_id: str = "") -> dict:
        """**按建议改稿**（v1.69 · 方案② 的候选环节）：只产候选，不落库。

        v1.70 修的三个缺陷（都有回归用例）：
          - 疑点1：scope=para 时此前把**整章**塞进 prompt（写着「只改这一段」），
            模型回整章 → splice 把整章塞回一段 → 正文膨胀。现在 prompt 只喂**这一段**；
          - 疑点3：章正文超 8000 字时整章改写只喂尾部 → 采纳等于砍掉前文。
            现在**直接拒绝**（提示改用 para/scene），并给「候选骤短」护栏；
          - 疑点6：命中缓存还重复落 note → note 创建挪进 single-flight 的计算体内，
            命中缓存 = 复用同一条 note。
        """
        from app.services import cache as cache_mod
        from app.services.engine.chapter_rewrite import (
            REWRITE_SCHEMA, MAX_PROMPT_BODY, normalize_rewrite, rewrite_prompt, splice_scope,
            validate_rewrite,
        )
        from app.services.engine.schema_retry import validate_and_retry
        from app.services.text_metrics import chapter_body

        chapter, why = await self._resolve_chapter_ref(
            chapter_id=chapter_id, chapter_no=chapter_no, book_id=book_id)
        if chapter is None:
            return {"error": why, "chapter_id": str(chapter_id or "")}
        cid = str(chapter.get("id") or "")
        bid = str(chapter.get("book_id") or "")
        instr = " ".join(str(instruction or "").split())
        if not instr:
            return {"error": "缺 instruction（要执行哪条建议）", "chapter_id": cid}
        if not llm_client.available:
            return {"error": "LLM 未接入（改稿需要模型）", "chapter_id": cid}

        scenes = await self.repo.list_scenes_by_chapter(cid)
        body = (text or "").strip() or chapter_body(chapter, scenes)
        if not body:
            return {"error": "章正文为空（先写正文再改）", "chapter_id": cid}

        scope = str(scope or "chapter").lower()
        try:
            pi = int(para_index or 0)
        except (TypeError, ValueError):
            pi = 0
        paras = [p for p in body.split("\n\n")]
        scene_prose = ""

        # ---- 定位改写片段（prompt 只看到这一段；其余段落不受影响）----
        if scope == "para":
            if pi <= 0 or pi > len(paras):
                return {"error": f"para_index 越界：{pi}（本文共 {len(paras)} 段）", "chapter_id": cid}
            fragment = paras[pi - 1]
            scope_label = f"第 {pi} 段（共 {len(paras)} 段）"
        elif scope == "scene":
            if not scene_id:
                return {"error": "scope=scene 需要 scene_id", "chapter_id": cid}
            sc = await self.repo.get_scene(scene_id)
            scene_prose = str((sc or {}).get("final_prose") or "").strip()
            if not scene_prose or scene_prose not in body:
                return {"error": "本场已推演正文不在章正文里（先「并入本章」再按场改）", "chapter_id": cid}
            fragment = scene_prose
            scope_label = "本场（已并入正文的那一段）"
        else:
            scope = "chapter"
            # 疑点3：整章 > 8000 字时 prompt 只装得下尾部 → 采纳等于砍掉前文。直接拒绝。
            if len(body) > MAX_PROMPT_BODY:
                return {"error": (
                    f"整章 {len(body)} 字 > {MAX_PROMPT_BODY}：整章改写只能看到尾部、会截断前文。"
                    "请改用 scope=para（按段）或 scope=scene（按场）分段改，或先把章正文控制在 8000 字内。"), "chapter_id": cid}
            fragment = body
            scope_label = "整章"

        volume = None
        for v in await self.repo.list_book_volumes(bid) if bid else []:
            if int(v.get("range_from") or 0) <= int(chapter.get("order_no") or 0) <= int(v.get("range_to") or 0):
                volume = v
                break
        context = "；".join(x for x in (
            f"本卷目标：{str((volume or {}).get('arc_goal') or '').strip()}" if (volume or {}).get("arc_goal") else "",
            f"卷末高潮：{str((volume or {}).get('climax') or '').strip()}" if (volume or {}).get("climax") else "",
        ) if x)

        body_hash = cache_mod.sha256_text(body)
        key = cache_mod.rewrite_key(cid, body_hash, instr, scope)

        async def _compute_candidate() -> dict:
            """最多两次：第一次若返回「与原文一致」的空候选，第二次带纠错提示再来。

            note 的创建属于「计算结果」的一部分（随结果一起缓存）——
            v1.69 的坑：命中缓存还每次新建 note，审计表被重复噪声灌满。
            """
            nudge = ""
            cand: dict = {"after": "", "summary": "", "kept": []}
            for _attempt in range(2):
                raw = await validate_and_retry(
                    llm_client,
                    rewrite_prompt(chapter=chapter, body=fragment, instruction=instr,
                                   scope=scope, scope_label=scope_label,
                                   context=context, nudge=nudge),
                    REWRITE_SCHEMA, validate_rewrite,
                )
                cand = normalize_rewrite(raw) if isinstance(raw, dict) else cand
                check_after = str(cand.get("after") or "").strip()
                if check_after:
                    try:
                        trial, _diff = splice_scope(body, check_after, scope=scope, para_index=pi,
                                                    scene_prose=scene_prose)
                    except ValueError as e:
                        nudge = f"上一次的输出无法拼回正文：{e}。请按 scope 要求只给对应的正文片段。"
                        continue
                    if trial.strip() != body.strip():
                        new_body, diff = splice_scope(body, check_after, scope=scope,
                                                      para_index=pi, scene_prose=scene_prose)
                        note = await self._save_chapter_note(
                            cid, kind="polisher", created_by="editor",
                            suggestion=(str(cand.get("summary") or "按建议改稿"))[:200],
                            before=body, after=new_body,
                            payload={"instruction": instr, "scope": scope, "diff": diff,
                                     "based_on": body_hash, "kept": cand.get("kept") or [],
                                     "kind": "rewrite"},
                        )
                        return {"after": new_body, "summary": cand.get("summary") or "",
                                "kept": cand.get("kept") or [], "diff": diff, "note_id": note}
                nudge = ("上一次改写与原文完全一致（等于没改）。请**务必做出实质修改**：至少改动一句指向该建议的话；"
                         "如果这条建议其实已经满足，就做最小的必要改进，并在 summary 里说明为什么这样改。")
            return cand

        try:
            payload, computed = await cache_mod.single_flight(key, _compute_candidate)
        except Exception as e:  # noqa: BLE001  模型失败要如实回报（不 500）
            logger.warning("[rewrite] 候选生成失败 chapter=%s：%s", cid, e)
            return {"error": f"候选生成失败：{type(e).__name__}: {e}", "chapter_id": cid}
        payload = payload or {}
        new_body = str(payload.get("after") or "")
        note_id = str(payload.get("note_id") or "")
        if not new_body or not note_id:
            return {"error": "模型没产出可用候选（可重试）", "chapter_id": cid}
        diff = payload.get("diff") or {}
        # 通用护栏：整章候选骤短（<60%）= 疑似截断/砍稿，拒绝
        if scope == "chapter" and len(body) >= 200 and len(new_body) < int(len(body) * 0.6):
            return {"error": (
                f"候选 {len(new_body)} 字比原文 {len(body)} 字短了 40% 以上（疑似截断/砍稿），已拒绝。"
                "可改用 scope=para/scene 分段改。"), "chapter_id": cid}

        return {"chapter_id": cid, "note_id": note_id,
                "summary": str(payload.get("summary") or ""),
                "kept": payload.get("kept") or [], "after": new_body, "diff": diff,
                "based_on": body_hash,
                "cache": {"key": key, "hit": not computed, "ttl": cache_mod.REWRITE_TTL}}

    async def apply_prose_note(self, note_id: str, confirm: bool = False,
                               based_on: str = "") -> dict:
        """**采纳候选**（v1.69 · 方案② 的落地环节）：把 note.after 原子写进正文并存版本。

        安全三件套：①必须 confirm；②候选生成时的正文 hash 必须与当前一致（否则拒绝，
        避免覆盖作者后来的修改）；③写库走既有 save_chapter_prose / save_scene_prose
        （覆盖前自动存版本、触发记账/索引/前情，可一键回退）。

        v1.70：**每条 note 一把进程内互斥锁** —— 修复「两次并发 apply 都能过 hash 校验、
        各写一次」的竞态（回归用例 test_concurrent_apply_writes_once）。
        """
        if not confirm:
            raise InvalidActionError("采纳会改写正文，需要 confirm=true")
        from app.services import cache as cache_mod
        lock = _apply_note_lock(note_id)
        async with lock:
            # 拿到锁之后**重新读一遍** note：并发场景下别人可能已经应用过了
            note = await self.repo.get_prose_note(note_id)
            if note is None:
                raise InvalidActionError(f"审计记录不存在: {note_id}")
            if str(note.get("status") or "") == "rejected":
                raise InvalidActionError("这条记录已被驳回，不能再采纳")
            after = str(note.get("after") or "").strip()
            if not after:
                raise InvalidActionError("这条记录没有候选稿（after 为空），无需应用")
            try:
                payload = json.loads(note.get("payload_json") or "{}")
            except Exception:  # noqa: BLE001
                payload = {}
            if not isinstance(payload, dict):
                payload = {}
            if payload.get("applied"):
                return {"note_id": note_id, "already_applied": True, "applied": payload.get("applied")}

            expected = str(based_on or payload.get("based_on") or "")
            chapter_id = str(note.get("chapter_id") or "")
            scene_id = str(note.get("scene_id") or "")
            revision = 0
            if chapter_id:
                chapter = await self.repo.get_chapter(chapter_id)
                if chapter is None:
                    raise InvalidActionError(f"章节不存在: {chapter_id}")
                cur = chapter_body(chapter, await self.repo.list_scenes_by_chapter(chapter_id))
                if expected and expected != cache_mod.sha256_text(cur):
                    raise InvalidActionError(
                        "正文在生成候选后已变更（hash 不一致）——请重新生成候选，避免覆盖新内容")
                had_prose = bool(str(chapter.get("prose_md") or "").strip())
                saved = await self.save_chapter_prose(chapter_id, after)
                revs = await self.repo.list_chapter_revisions(chapter_id)
                # 语义：save_chapter_prose 把**覆盖前的旧正文**存成最新版本；
                # 首次写入没有旧文 → prev_revision = 0（前端不显示「可回退」）。
                revision = int((revs[0] or {}).get("version") or 0) if (revs and had_prose) else 0
                target = "chapter"
            elif scene_id:
                scene = await self.repo.get_scene(scene_id)
                if scene is None:
                    raise InvalidActionError(f"场景不存在: {scene_id}")
                cur = str(scene.get("final_prose") or "")
                if expected and expected != cache_mod.sha256_text(cur):
                    raise InvalidActionError(
                        "本场正文在生成候选后已变更（hash 不一致）——请重新生成候选")
                saved = await self.save_scene_prose(scene_id, after)
                target = "scene"
            else:
                raise InvalidActionError("这条记录既没有 chapter_id 也没有 scene_id，无法采纳")

            applied = {"at": int(time.time() * 1000), "prev_revision": revision,
                       "chars": len(after), "target": target}
            await self.repo.save_prose_note({
                "id": note_id, "status": "approved", "reviewed_at": datetime.now(),
                "payload_json": json.dumps({**payload, "applied": applied}, ensure_ascii=False),
            })
            return {"note_id": note_id, "target": target, "prev_revision": revision,
                    "chars": len(after), "saved": saved, "already_applied": False}

    # ---------------------------------------------------------------- 章级审校工具（v1.65）
    # 为什么在 service 而不是 engine：与场级 prose.* 一一对应（同一台引擎），差别只在
    # "单位是章 + 落章级审计（prose_notes.chapter_id）"。合并进章级工作台后，责编在章级
    # 说话要能真的动手，而不是只能"建议你去场级页面点"。
    async def _chapter_and_body(self, chapter_id: str, text: str = "") -> tuple[dict, str, str]:
        """章 + 章正文（唯一真源，空则回落场聚合）+ book_id。text 非空时以它为准（未保存稿）。"""
        from app.services.text_metrics import chapter_body

        chapter = await self.repo.get_chapter(chapter_id)
        if chapter is None:
            raise ChapterNotFoundError(f"未找到章节: {chapter_id}")
        scenes = await self.repo.list_scenes_by_chapter(chapter_id)
        body = (text or "").strip() or chapter_body(chapter, scenes)
        return chapter, body, str(chapter.get("book_id") or "")

    async def _save_chapter_note(self, chapter_id: str, *, kind: str, suggestion: str,
                                 before: str = "", after: str = "", created_by: str = "editor",
                                 payload: dict | None = None) -> str:
        """章级审校记录（pending）——与场级 note 同一张表，靠 chapter_id 区分。"""
        note_id = f"note-{uuid.uuid4().hex[:10]}"
        try:
            await self.repo.save_prose_note({
                "id": note_id, "scene_id": None, "chapter_id": chapter_id,
                "kind": kind, "status": "pending",
                "suggestion": (suggestion or "")[:600],
                "before": before, "after": after, "created_by": created_by,
                "payload_json": json.dumps(payload or {}, ensure_ascii=False),
                "ts": int(time.time() * 1000),
            })
            return note_id
        except Exception as e:  # noqa: BLE001  审计失败不该让业务失败（T9 会记降级）
            logger.warning("[chapter-note] 落审计失败 chapter=%s：%s", chapter_id, e)
            return ""

    async def chapter_prose_review(self, chapter_id: str, text: str = "") -> dict:
        """体检整章（章级版 prose.review）。"""
        from app.services.engine.prose import review_prose

        chapter, body, book_id = await self._chapter_and_body(chapter_id, text)
        if not body:
            return {"report": {}, "note_status": "skipped", "note": "章正文为空：先写正文再体检"}
        characters = (await self.repo.list_characters_by_book(book_id)) if book_id else []
        prior = style_voice_prior(body, characters)
        report = await review_prose(llm_client, chapter, body, characters=characters,
                                    voice_prior=prior)
        issues = report.get("issues") or []
        voice = report.get("voice_findings") or []
        summary = "；".join(f"[{i.get('severity')}] {i.get('text')}" for i in issues[:3]) or "（无问题）"
        if voice:
            summary += "；口吻：" + "；".join(
                f"{v.get('char')}（{str(v.get('issue') or '')[:40]}）" for v in voice[:2])
        note_id = await self._save_chapter_note(
            chapter_id, kind="editor", created_by="editor",
            suggestion=f"{report.get('overall') or ''}｜{summary}",
            before=body, payload={"voice_findings": voice, "voice_prior": prior},
        )
        return {"report": report, "voice_prior": prior, "note_id": note_id,
                "note_status": "pending" if note_id else "failed"}

    async def chapter_prose_polish(self, chapter_id: str, text: str = "") -> dict:
        """润色整章（候选，不自动落库；作者在章页点采纳才写 prose_md）。"""
        from app.services.engine.prose import polish_prose

        _chapter, body, _book_id = await self._chapter_and_body(chapter_id, text)
        if not body:
            return {"after": "", "summary": "", "note": "章正文为空"}
        out = await polish_prose(llm_client, body)
        after = str(out.get("after") or body)
        note_id = await self._save_chapter_note(
            chapter_id, kind="polisher", created_by="polisher",
            suggestion=str(out.get("summary") or "")[:200],
            before=body, after=after,
        )
        return {"after": after, "summary": out.get("summary", ""),
                "note_id": note_id, "note_status": "pending" if note_id else "failed"}

    async def chapter_prose_verify(self, chapter_id: str, text: str = "") -> dict:
        """质检整章（伏笔/信念/因果/状态/风险）。"""
        from app.services.engine.prose import verify_prose

        _chapter, body, book_id = await self._chapter_and_body(chapter_id, text)
        if not body:
            return {"opinion": {}, "note": "章正文为空"}
        foreshadows = (await self.repo.list_foreshadows(book_id)) if book_id else []
        beliefs = (await self.repo.list_beliefs(book_id)) if book_id else []
        world_states = (await self.repo.list_world_states(book_id)) if book_id else []
        characters = (await self.repo.list_characters_by_book(book_id)) if book_id else []
        opinion = await verify_prose(llm_client, body, foreshadows, beliefs, world_states,
                                     characters=characters, skip_voice=True)
        sug = ("伏笔推进 %d 条；信念变化 %d 条；因果 %d 条；状态变化 %d 条；风险 %d 条"
               % (len(opinion.get("foreshadow_updates") or []), len(opinion.get("belief_deltas") or []),
                  len(opinion.get("causal") or []), len(opinion.get("state_deltas") or []),
                  len(opinion.get("risks") or [])))
        note_id = await self._save_chapter_note(
            chapter_id, kind="verifier", created_by="verifier",
            suggestion=sug, before=body, payload=opinion)
        return {"opinion": opinion, "note_id": note_id,
                "note_status": "pending" if note_id else "failed"}

    async def chapter_prose_scan_tone(self, chapter_id: str, text: str = "") -> dict:
        """AI 味扫描整章（0-token，不花模型）。"""
        from app.services.engine.style_checks import ai_tone_report

        _chapter, body, book_id = await self._chapter_and_body(chapter_id, text)
        if not body:
            return {"report": {"rules": []}, "note": "章正文为空"}
        characters = (await self.repo.list_characters_by_book(book_id)) if book_id else []
        names = {str(c.get("name") or "") for c in characters if c.get("name")}
        states = (await self.repo.list_world_states(book_id)) if book_id else []
        terms = {str(s.get("name") or "") for s in states
                 if str(s.get("kind") or "") in {"term", "numeric"}}
        report = ai_tone_report(body, exclude=names | terms)
        rules = report.get("rules") or []
        hit = "、".join(sorted({str(r.get("rule")) for r in rules})) or "无"
        note_id = await self._save_chapter_note(
            chapter_id, kind="editor", created_by="ai_tone",
            suggestion=("AI 味扫描：命中 %d 条（%s）" % (len(rules), hit))[:400],
            before=body, payload={"source": "ai_tone", "ai_tone": report})
        return {"report": report, "note_id": note_id,
                "note_status": "pending" if note_id else "failed"}

    async def chapter_prose_quality(self, chapter_id: str, text: str = "") -> dict:
        """评审整章：五维自评 + 0-token 硬信号（只读）。"""
        from app.services.engine.quality import score_text

        _chapter, body, book_id = await self._chapter_and_body(chapter_id, text)
        if not body:
            return {"total": None, "scores": {}, "weak_points": [], "note": "章正文为空，无评分"}
        characters = (await self.repo.list_characters_by_book(book_id)) if book_id else []
        return await score_text(llm_client, body, characters=characters)

    async def chapter_prose_context(self, chapter_id: str) -> dict:
        """章级副驾的"我看到了什么"（前情 + 记忆五段 + 场清单），给前端面板与后端同一份口径。"""
        from app.services.agents.chapter_perceive import build_chapter_packet, summary

        packet = await build_chapter_packet(self.repo, chapter_id)
        return {"summary": summary(packet), "packet": packet}

    async def merge_scene_into_chapter(self, chapter_id: str, scene_id: str = "") -> dict:
        """把**已推演的场正文**并入章正文（场正文只是可选项，章正文是唯一真源）。

        规则（可按需再调）：
          - 只并入有 final_prose 的场；已在章正文里出现过的跳过（幂等，重复点不会重复贴）；
          - 按场序（cursor_pos）追加，段间空一行；
          - 走 save_chapter_prose：覆盖前存版本 → 触发记账/索引/前情，与手写保存完全同一条路。
        """
        from app.services.text_metrics import chapter_body

        chapter = await self.repo.get_chapter(chapter_id)
        if chapter is None:
            raise ChapterNotFoundError(f"未找到章节: {chapter_id}")
        scenes = await self.repo.list_scenes_by_chapter(chapter_id)
        targets = [s for s in scenes if not scene_id or str(s.get("id")) == scene_id]
        # 查重必须只看**真源里已有的**（chapter.prose_md），不能拿 chapter_body 的回落结果：
        # 章正文为空时 chapter_body 会回落到场正文，那样"一次都没并过"也会被判定成"已并入"。
        stored = str(chapter.get("prose_md") or "").strip()
        body = stored
        added: list[dict] = []
        for s in targets:
            prose = str(s.get("final_prose") or "").strip()
            if not prose:
                continue
            if prose in stored:
                continue
            body = (body + "\n\n" + prose).strip() if body else prose
            added.append({"scene_id": str(s.get("id")), "title": s.get("title") or "",
                          "chars": len(prose)})
        if not added:
            return {"chapter_id": chapter_id, "merged": 0, "chars": len(body),
                    "skipped": "没有可并入的场正文（未推演 / 已并入过）"}
        saved = await self.save_chapter_prose(chapter_id, body)
        return {"chapter_id": chapter_id, "merged": len(added), "chars": len(body),
                "scenes": added, "save": saved}

    async def prose_review(self, scene_id: str, text: str) -> dict:
        """体检员：结构化体检报告 → editor note（pending）。"""
        from app.services.engine.prose import review_prose

        scene, _ = await self._scene_and_book(scene_id)
        characters = await self.repo.list_characters_for_scene(scene_id) if scene_id else []
        # S2 step5：0-token 口吻先验（成本 0，跑在体检之前；与 A3 共用底座）
        prior = style_voice_prior(text, characters)
        report = await review_prose(llm_client, scene, text, characters=characters,
                                    voice_prior=prior)
        issues = report.get("issues") or []
        voice = report.get("voice_findings") or []
        summary = "；".join(f"[{i.get('severity')}] {i.get('text')}"
                            for i in issues[:3]) or "（无问题）"
        if voice:
            summary += "；口吻：" + "；".join(
                f"{v.get('char')}（{str(v.get('issue') or '')[:40]}）" for v in voice[:2]
            )
        prior_flags = prior.get("flags") or []
        if prior_flags:
            summary += "；先验信号：" + "；".join(
                str(f.get("detail") or "")[:60] for f in prior_flags[:2]
            )
        await self.repo.save_prose_note({
            "id": f"note-{uuid.uuid4().hex[:10]}",
            "scene_id": scene_id, "kind": "editor", "status": "pending",
            "suggestion": f"{report.get('overall') or ''}\n{summary}"[:600],
            "before": text, "after": "", "created_by": "editor",
            "payload_json": json.dumps(
                {"voice_findings": voice, "voice_prior": prior}, ensure_ascii=False),
            "ts": int(time.time() * 1000),
        })
        return {"report": report, "voice_prior": prior, "note_status": "pending"}

    async def prose_polish(self, scene_id: str, text: str) -> dict:
        """润色师：去 AI 味润色（只改写法）→ polisher note（pending，after 供 [应用]）。"""
        from app.services.engine.prose import polish_prose

        await self._scene_and_book(scene_id)
        out = await polish_prose(llm_client, text)
        await self.repo.save_prose_note({
            "id": f"note-{uuid.uuid4().hex[:10]}",
            "scene_id": scene_id, "kind": "polisher", "status": "pending",
            "suggestion": str(out.get("summary") or "")[:200],
            "before": text, "after": str(out.get("after") or text),
            "created_by": "polisher", "ts": int(time.time() * 1000),
        })
        return {"after": out.get("after", text), "summary": out.get("summary", "")}

    async def prose_verify(self, scene_id: str, text: str, skip_voice: bool = False) -> dict:
        """质检员：伏笔/信念/因果对照 → verifier note（pending，明细存 payload_json，
        作者 approve 时据此落账本）。

        skip_voice=True（v1.42）：口吻交给体检员，本工具不重复分析（审稿会并发路径）。
        """
        from app.services.engine.prose import verify_prose

        scene, book_id = await self._scene_and_book(scene_id)
        foreshadows = (await self.repo.list_foreshadows(book_id)) if book_id else []
        beliefs = (await self.repo.list_beliefs(book_id)) if book_id else []
        world_states = (await self.repo.list_world_states(book_id)) if book_id else []
        characters = await self.repo.list_characters_for_scene(scene_id) if scene_id else []
        opinion = await verify_prose(llm_client, text, foreshadows, beliefs, world_states,
                                     characters=characters, skip_voice=skip_voice)
        sug = (
            f"伏笔推进 {len(opinion.get('foreshadow_updates') or [])} 条；"
            f"信念变化 {len(opinion.get('belief_deltas') or [])} 条；"
            f"因果 {len(opinion.get('causal') or [])} 条；"
            f"状态变化 {len(opinion.get('state_deltas') or [])} 条"
        )
        voice_risks = opinion.get("voice_risks") or []
        if voice_risks:
            sug += "；口吻风险：" + "；".join(
                f"{v.get('char')}（{str(v.get('risk') or '')[:40]}）" for v in voice_risks[:2]
            )
        if opinion.get("risks"):
            sug += "；风险：" + "；".join(str(r) for r in opinion["risks"][:2])[:300]
        await self.repo.save_prose_note({
            "id": f"note-{uuid.uuid4().hex[:10]}",
            "scene_id": scene_id, "kind": "verifier", "status": "pending",
            "suggestion": sug[:600], "before": text, "after": "", "created_by": "verifier",
            "payload_json": json.dumps(opinion, ensure_ascii=False),
            "ts": int(time.time() * 1000),
        })
        return {"opinion": opinion, "note_status": "pending"}

    async def _emergence_materials(self, book_id: str) -> list[dict]:
        """S4 桥接素材：灵感池里 source=emergence 且**已采纳**（作者勾选）的高光卡。"""
        cards = await self.repo.list_inspirations(book_id)
        return [c for c in cards
                if str(c.get("source") or "") == "emergence" and c.get("adopted") is True]

    async def _scene_motives(self, scene_id: str, book_id: str) -> list[dict]:
        """S4 桥接动机：该场景**最近一个有 reasoning 的回合**里各角色的动机依据。"""
        archives = (await self._archives_by_scene(book_id)).get(scene_id, [])
        for a in sorted(archives, key=lambda x: x.get("turn") or 0, reverse=True):
            rows = [m for m in (a.get("thoughts") or [])
                    if str((m or {}).get("reasoning") or "").strip()]
            if rows:
                return rows
        return []

    async def prose_quality_loop(self, scene_id: str, text: str) -> dict:
        """A2 质量回环：自评 → 门控 → （必要时）重写一次 → 三闸复检。

        采纳时落 **polisher note**（before/after）→ 复用前端"应用"管线；
        评分/复检明细落 note 的 payload_json.quality。
        """
        from app.services.engine.quality import quality_loop

        scene, book_id = await self._scene_and_book(scene_id)
        characters = await self.repo.list_characters_for_scene(scene_id) if scene_id else []
        states = (await self.repo.list_world_states(book_id)) if book_id else []
        out = await quality_loop(llm_client, text, characters=characters, world_states=states)
        if out.get("accepted"):
            before = out.get("before") or {}
            after = out.get("after_score") or {}
            sug = "质量回环：%s→%s（路由 %s，改 %d 处）" % (
                before.get("total"), after.get("total"), out.get("route"),
                len([c for c in (out.get("changes") or []) if c.get("applied")]))
            await self.repo.save_prose_note({
                "id": f"note-{uuid.uuid4().hex[:10]}",
                "scene_id": scene_id, "kind": "polisher", "status": "pending",
                "suggestion": sug[:300], "before": text,
                "after": str(out.get("after") or text), "created_by": "a2_quality",
                "payload_json": json.dumps({
                    "source": "a2_quality",
                    "quality": {"before": before, "after": after,
                                "route": out.get("route"), "changes": out.get("changes") or []},
                }, ensure_ascii=False),
                "ts": int(time.time() * 1000),
            })
            out["note_status"] = "pending"
        return out

    async def prose_ai_tone(self, scene_id: str, text: str) -> dict:
        """A3 反 AI 味扫描（0-token）→ editor note（pending，明细存 payload_json.ai_tone）。

        exclude 同时传角色名与世界状态术语：R-A3-6 不排除专名会把主角名判成"复读"。
        """
        from app.services.engine.style_checks import ai_tone_report

        scene, book_id = await self._scene_and_book(scene_id)
        characters = await self.repo.list_characters_for_scene(scene_id) if scene_id else []
        names = {str(c.get("name") or "") for c in characters if c.get("name")}
        states = (await self.repo.list_world_states(book_id)) if book_id else []
        terms = {str(s.get("name") or "") for s in states
                 if str(s.get("kind") or "") in {"term", "numeric"}}
        report = ai_tone_report(text, exclude=names | terms)
        rules = report.get("rules") or []
        hit_rules = "、".join(sorted({str(r.get("rule")) for r in rules})) or "无"
        sug = "AI 味扫描：命中 %d 条（%s）" % (len(rules), hit_rules)
        if rules:
            sug += "；" + "；".join(str(r.get("detail") or "")[:48] for r in rules[:2])
        await self.repo.save_prose_note({
            "id": f"note-{uuid.uuid4().hex[:10]}",
            "scene_id": scene_id, "kind": "editor", "status": "pending",
            "suggestion": sug[:400], "before": text, "after": "",
            "created_by": "ai_tone",
            "payload_json": json.dumps({"source": "ai_tone", "ai_tone": report},
                                       ensure_ascii=False),
            "ts": int(time.time() * 1000),
        })
        return {"report": report, "note_status": "pending"}

    async def prose_spot_fix(self, scene_id: str, text: str) -> dict:
        """A3 定点修复：只改白名单规则命中句；通过双闸才采纳。

        采纳时落 **polisher note**（before/after）→ 前端复用现有"应用润色稿"按钮落地，
        不新造 diff UI；未采纳/异常不落 note（避免噪音），原因在响应里返回。
        """
        from app.services.engine.ai_tone import spot_fix

        scene, book_id = await self._scene_and_book(scene_id)
        characters = await self.repo.list_characters_for_scene(scene_id) if scene_id else []
        states = (await self.repo.list_world_states(book_id)) if book_id else []
        out = await spot_fix(llm_client, text, characters=characters, world_states=states)
        applied = [c for c in (out.get("changes") or []) if c.get("applied")]
        if out.get("accepted") and applied:
            sug = "定点修复：改 %d 句（命中 %s→%s）；%s" % (
                len(applied), out.get("hits_before"), out.get("hits_after"),
                "；".join(str(c.get("reason") or "")[:28] for c in applied[:2]))
            await self.repo.save_prose_note({
                "id": f"note-{uuid.uuid4().hex[:10]}",
                "scene_id": scene_id, "kind": "polisher", "status": "pending",
                "suggestion": sug[:300], "before": text,
                "after": str(out.get("after") or text), "created_by": "ai_tone",
                "payload_json": json.dumps({
                    "source": "ai_tone_spotfix",
                    "changes": out.get("changes") or [],
                    "hits_before": out.get("hits_before"),
                    "hits_after": out.get("hits_after"),
                    "ai_tone_after": out.get("report_after"),
                }, ensure_ascii=False),
                "ts": int(time.time() * 1000),
            })
            out["note_status"] = "pending"
        return out

    async def list_prose_notes(self, scene_id: str) -> list[dict]:
        await self._scene_and_book(scene_id)
        return await self.repo.list_prose_notes(scene_id)

    async def review_prose_note(self, note_id: str, approve: bool) -> dict:
        """作者审阅：approve verifier note → 读 payload_json 明细先记账（伏笔推进/信念/因果）
        再置 approved；reject → 仅置 rejected 不写库。已审阅的 note 幂等拒绝。"""
        note = await self.repo.get_prose_note(note_id)
        if note is None:
            raise InvalidActionError(f"审计记录不存在: {note_id}")
        if note["status"] != "pending":
            raise InvalidActionError(f"记录已审阅（{note['status']}），不允许重复操作")

        bookkeeping = False
        if approve and note["kind"] == "verifier":
            try:
                opinion = json.loads(note.get("payload_json") or "{}") or {}
                # v1.69：章级质检记录（chapter_id 有值、scene_id 为空）也必须能记账 ——
                # 否则章级 note 点「批准」会因 _scene_and_book("") 报错（合并后的高频路径）。
                book_id = ""
                scene_no = 0
                if note.get("chapter_id"):
                    ch = await self.repo.get_chapter(str(note["chapter_id"]))
                    if ch:
                        book_id = str(ch.get("book_id") or "")
                        scene_no = int(ch.get("order_no") or 0)
                else:
                    _scene, book_id = await self._scene_and_book(note["scene_id"])
                if book_id and (opinion.get("foreshadow_updates") or opinion.get("belief_deltas")
                                or opinion.get("state_deltas")):
                    bookkeeping = await self._apply_verify_bookkeeping(
                        book_id, opinion, source_event_id="PROSE_VERIFY", scene_no=scene_no)
            except Exception as e:  # noqa: BLE001
                logger.warning("[prose] verifier 记账失败，note 保持 pending：%s", e)
                raise InvalidActionError(f"质检记账失败，未批准：{e}") from e

        status = "approved" if approve else "rejected"
        await self.repo.save_prose_note({
            "id": note_id, "status": status,
            "reviewed_at": datetime.now(),
        })
        return {"id": note_id, "status": status, "bookkeeping": bookkeeping}

    async def _apply_verify_bookkeeping(self, book_id: str, opinion: dict,
                                        source_event_id: str = "PROSE_VERIFY",
                                        scene_no: int = 0) -> int:
        """把质检/记账明细落账本（阶段⑤ 记账 Agent 核心写入）：
        1) 伏笔推进（text 匹配书级伏笔 → status/notes 追加）；2) 信念新增（角色名→id）；
        3) 世界状态（S1：战力/道具/时间/术语/数字，按 key upsert，幂等）；
        4) 因果：无事件流跳过。返回实际写入条数（幂等由调用方 hash/状态闸门保证）。"""
        written = 0
        protected = 0  # 作者手改被质检跳过的条数（B27a）
        # 1) 伏笔推进
        foreshadows = {f["id"]: f for f in await self.repo.list_foreshadows(book_id)}
        valid_status = {"in_progress", "closed"}
        for u in opinion.get("foreshadow_updates") or []:
            text = str(u.get("text") or "").strip()
            if not text:
                continue
            target = next(
                (f for f in foreshadows.values()
                 if f.get("text") and (text in f["text"] or f["text"] in text)),
                None,
            )
            if target is None:
                continue  # 账本里没有对应伏笔 → 不凭空新增（避免污染）
            status = str(u.get("status") or "in_progress")
            if status not in valid_status:
                status = "in_progress"
            reason = str(u.get("reason") or "")
            prev_notes = str(target.get("notes") or "")
            extra = f"[{source_event_id}] {reason}".strip() if reason else f"[{source_event_id}] 正文推进"
            await self.repo.save_foreshadow({
                "id": target["id"], "status": status,
                "notes": f"{prev_notes}\n{extra}".strip() if prev_notes else extra,
            })
            written += 1

        # 2) 信念新增（角色名 → 书级角色 id）
        chars = {c.get("name"): c.get("id") for c in await self.repo.list_characters_by_book(book_id)}
        valid_channel = {"perceived", "told", "inferred"}
        for b in opinion.get("belief_deltas") or []:
            cid = chars.get(str(b.get("char") or ""))
            txt = str(b.get("text") or "").strip()
            if not cid or not txt:
                continue
            channel = str(b.get("channel") or "perceived")
            if channel not in valid_channel:
                channel = "perceived"
            await self.repo.save_belief({
                "id": f"bel-{uuid.uuid4().hex[:10]}",
                "book_id": book_id, "char_id": cid,
                "fact_id": "", "source_event_id": source_event_id,
                "channel": channel, "text": txt, "confidence": 0.7,
                "edited": False, "ts": int(time.time() * 1000),
            })
            written += 1

        # 3) 世界状态 upsert（S1：战力/道具/时间/术语/数字，按 key 幂等）
        valid_kind = {"character", "item", "time", "term", "numeric"}
        now_ts = int(time.time() * 1000)
        # 真 upsert：按 (kind, key) 匹配既有行，复用其 id（历史/手建 id 不一定是 ws-<hash>，
        # 用 key 定位才能正确更新而非插重复行）
        existing = {}
        try:
            exist_rows = await self.repo.list_world_states(book_id)
        except Exception:  # noqa: BLE001
            exist_rows = []
        for e in exist_rows:
            existing[(str(e.get("kind") or ""), str(e.get("key") or ""))] = e

        for s in opinion.get("state_deltas") or []:
            kind = str(s.get("kind") or "").strip()
            key = str(s.get("key") or "").strip()
            value = str(s.get("value") or "").strip()
            if kind not in valid_kind or not key or not value:
                continue
            prev_row = existing.get((kind, key))
            if prev_row is not None:
                # 作者手改 = 最高权威设定，质检（LLM 提取）不得覆盖（B27a）
                if prev_row.get("edited"):
                    protected += 1
                    continue
                rid = str(prev_row.get("id") or "")
                prev_value = str(prev_row.get("value") or "")
                name = str(s.get("name") or prev_row.get("name") or "").strip()
            else:
                # 无既有行 → 稳定 hash id 新建
                nid = hashlib.sha256(f"{book_id}|{kind}|{key}".encode()).hexdigest()[:14]
                rid = f"ws-{nid}"
                prev_value = str(s.get("previous_value") or "")
                name = str(s.get("name") or "").strip()
            await self.repo.save_world_state({
                "id": rid, "book_id": book_id,
                "kind": kind,
                "name": name,
                "key": key,
                "value": value,
                "scene_no": scene_no,
                "source_event_id": source_event_id,
                "previous_value": prev_value,
                "edited": False, "ts": now_ts,
            })
            existing[(kind, key)] = {"id": rid, "name": name}
            written += 1
        if protected:
            logger.info("[bookkeeping] 作者手改状态 %d 条，质检未覆盖（B27a）", protected)
        return written

    async def save_scene_prose(self, scene_id: str, text: str) -> dict:
        """作者保存正文 → final_prose 幂等落库，保存后**自动触发记账 Agent**（后台任务，
        同内容重复保存 unchanged 不触发，天然幂等）。"""
        scene, _ = await self._scene_and_book(scene_id)
        text = (text or "").strip()
        if str(scene.get("final_prose") or "") == text:
            return {"scene_id": scene_id, "unchanged": True, "word_count": prose_chars(text)}
        await self.update_scene(scene_id, {"final_prose": text})
        # 后台记账（v1.41 挪进 arq 队列）：不阻塞保存返回；失败入重试，不再"后端重启就丢"
        try:
            from app.services.tasks_worker import enqueue as job_enqueue

            await job_enqueue("bookkeeping", {"scene_id": scene_id, "text": text})
        except Exception as e:  # noqa: BLE001  队列不可用时退回进程内任务（T9：不静默）
            logger.warning("[bookkeeping] arq 入队失败，退回 asyncio 任务：%s", e)
            asyncio.create_task(self._bookkeep_after_save(scene_id, text))
        return {"scene_id": scene_id, "unchanged": False, "word_count": prose_chars(text)}

    async def _bookkeep_after_save(self, scene_id: str, text: str) -> None:
        """保存正文后的自动记账（阶段⑤ 记账 Agent）：LLM 分析 final_prose ↔ 伏笔/信念账本
        → 落账本（复用 _apply_verify_bookkeeping）+ 审计记录（kind=verifier·approved·created_by=bookkeeping）。
        失败仅告警，不滚动保存结果。"""
        try:
            from app.services.engine.prose import verify_prose

            scene, book_id = await self._scene_and_book(scene_id)
            if not book_id or not text.strip() or not llm_client.available:
                return
            foreshadows = await self.repo.list_foreshadows(book_id)
            beliefs = await self.repo.list_beliefs(book_id)
            world_states = await self.repo.list_world_states(book_id)
            scene_no = scene.get("cursor_pos") or 0
            characters = await self.repo.list_characters_for_scene(scene_id)
            opinion = await verify_prose(llm_client, text, foreshadows, beliefs, world_states,
                                         characters=characters)
            written = await self._apply_verify_bookkeeping(
                book_id, opinion, source_event_id="PROSE_SAVE", scene_no=scene_no)
            sug = (
                f"自动记账：伏笔 {len(opinion.get('foreshadow_updates') or [])} 条 / "
                f"信念 {len(opinion.get('belief_deltas') or [])} 条 / "
                f"状态 {len(opinion.get('state_deltas') or [])} 条，写入 {written} 条"
            )
            await self.repo.save_prose_note({
                "id": f"note-{uuid.uuid4().hex[:10]}",
                "scene_id": scene_id, "kind": "verifier", "status": "approved",
                "suggestion": sug[:300], "before": "", "after": text[:500],
                "created_by": "bookkeeping", "reviewed_at": datetime.now(),
                "payload_json": json.dumps(opinion, ensure_ascii=False),
                "ts": int(time.time() * 1000),
            })
        except Exception as e:  # noqa: BLE001
            logger.warning("[prose] 保存后自动记账失败（不影响保存）：%s", e)

    # ---------------------------------------------------------------- 主笔共创对话（P0-2）
    async def list_chat_histories(self, book_id: str) -> list[dict]:
        """获取某书的主笔共创对话历史（ts 正序）。"""
        return await self.repo.list_chat_histories(book_id)

    async def chief_chat_stream(self, book_id: str, messages: list[dict]):
        """主笔共创对话：注入感知上下文（书树+记忆+灵感+伏笔），逐 token 流式返回。
        对话完成后自动保存历史到 chat_histories 表（按书隔离）。"""
        perceived = await self.chief_perceive(book_id)

        last_user = ""
        for m in messages:
            if m.get("role") == "user" and m.get("content"):
                last_user = str(m["content"])
        last_user = last_user[-800:]

        prompt = (
            f"你是小说【主笔】（总编剧）。这是当前这本书的感知上下文：\n"
            f"{perceived or '（暂无上下文）'}\n"
            f"请基于以上上下文，回答作者问题：{last_user}\n"
            f"回答用中文，简洁有干货，可给建议或反问，不要空话。"
        )

        full_reply = ""
        if llm_client.available:
            async for token in llm_client.chat_stream(prompt):
                full_reply += token
                yield token
        else:
            # 回退文案按两段增量下发：与真实 LLM 的逐 token 流保持同一种前端渲染节奏（B15）
            for _seg in (
                f"主笔已收到：{last_user}。当前模型未接入，我先按这本书的骨架给你一点参考——",
                "可以先从「一句话方向」或选中骨架里的某一章让我展开；需要真实创作建议时接入模型即可。",
            ):
                full_reply += _seg
                yield _seg

        # 对话完成后保存历史（user 消息 + assistant 回复）
        import time as _time
        now_ts = int(_time.time() * 1000)
        history_entries = []
        # 保存本轮 user 消息
        history_entries.append({
            "id": f"ch_{now_ts}_u",
            "role": "user",
            "content": last_user,
            "ts": now_ts,
        })
        # 保存 assistant 回复
        history_entries.append({
            "id": f"ch_{now_ts}_a",
            "role": "assistant",
            "content": full_reply,
            "ts": now_ts + 1,
        })
        try:
            await self.repo.append_chat_histories(book_id, history_entries)
        except Exception as e:  # noqa: BLE001
            logger.warning("[service] 保存对话历史失败（不影响对话）：%s", e)

    # ---------------------------------------------------------------- 场景收束 + 换场（S3）
    async def analyze_scene_close(self, sim_id: str) -> dict:
        """场景收束长程汇报：导演 LLM 汇报 → 写伏笔账本 + 场景摘要 + 事件因果补全。"""
        from app.services.engine.director import DirectorEngine

        sim = await self._load_sim(sim_id)
        foreshadows = await self.repo.list_foreshadows(sim.book_id) if sim.book_id else []
        remaining = await self._remaining_scene_titles(sim.chapter_id, sim.scene_id)
        director = DirectorEngine(sim)
        report = await director.analyze_scene_close(
            foreshadow_items=[{k: f.get(k) for k in ("id", "text", "status", "expected_close_scene")} for f in foreshadows],
            remaining_scenes=remaining,
        )

        # 1) 伏笔三态更新（写表）
        by_id = {f["id"]: f for f in foreshadows}
        for u in report.get("foreshadow_updates") or []:
            fid = u.get("id")
            if not fid or fid not in by_id:
                continue
            before = by_id[fid]
            await self.repo.save_foreshadow({**before, "status": u.get("status", before["status"])})

        # 2) 因果补全（Event.caused_by/causal_pressure，存回 sim）
        causality = {c.get("event_id"): c for c in report.get("causality") or [] if c.get("event_id")}
        for e in sim.events:
            c = causality.get(e.id)
            if c and c.get("caused_by"):
                e.caused_by = c["caused_by"]
            if c and c.get("causal_pressure"):
                e.causal_pressure = float(c["causal_pressure"])
        await self.repo.save(sim_id, sim)

        # 3) 场景摘要写 scene 表
        if sim.scene_id and report.get("scene_summary"):
            sc = await self.repo.get_scene(sim.scene_id)
            if sc:
                await self.repo.save_scene({**sc, "scene_summary": report["scene_summary"]})
        return report

    async def _remaining_scene_titles(self, chapter_id: str, scene_id: str) -> list[str]:
        if not chapter_id:
            return []
        scenes = await self.repo.list_scenes_by_chapter(chapter_id)
        after = [s["title"] for s in scenes if s["id"] != scene_id]
        return after

    # ---------------------------------------------------------------- 正文定稿/落库（P0 · 正文落库）
    async def finalize_scene_prose(self, sim_id: str) -> dict:
        """作者手动定稿：聚合该 sim 的 turn_archives.prose（按 turn 排序）→ 写 scenes.final_prose。

        幂等覆盖（rewind 重演后再定稿自然覆盖）；不强制 converged（作者存稿自由）。
        """
        sim = await self._load_sim(sim_id)
        if not sim.scene_id:
            raise InvalidActionError("该 sim 无场景上下文，无法定稿")
        prose = "\n\n".join(
            a.prose for a in sorted(sim.turn_archives, key=lambda x: x.turn) if a.prose
        )
        if not prose:
            raise InvalidActionError("无可定稿正文（尚无成文回合）")

        sc = await self.repo.get_scene(sim.scene_id)
        if sc is None:
            raise SceneNotFoundError(f"未找到场景: {sim.scene_id}")
        await self.repo.save_scene({**sc, "final_prose": prose})
        return {
            "scene_id": sim.scene_id,
            "chapter_id": sim.chapter_id,
            "book_id": sim.book_id,
            "title": sc.get("title", ""),
            "word_count": prose_chars(prose),
            "prose": prose,
        }

    async def get_scene_prose(self, scene_id: str) -> dict | None:
        """单场景定稿正文。"""
        sc = await self.repo.get_scene(scene_id)
        if sc is None:
            return None
        prose = sc.get("final_prose") or ""
        return {
            "scene_id": scene_id,
            "chapter_id": sc.get("chapter_id", ""),
            "title": sc.get("title", ""),
            "prose": prose,
            "word_count": prose_chars(prose),
            "finalized": bool(prose),
        }

    async def get_chapter_prose(self, chapter_id: str) -> dict | None:
        """章节正文（v1.46 直写链路后有两种来源，**章正文优先**）。

        - 直写模式：正文在 chapters.prose_md（章是最小可写单位）
        - 场景模式：正文 = 各场 final_prose 聚合（场是可选细分）
        两者都非空时以章正文为准（避免出现第二个正文真源/字数口径打架——B39 的教训）。
        新增 prose_md / words / word_target / tone / summary 字段；老字段（prose/scenes/
        word_count）保持原语义不变，零破坏。
        """
        ch = await self.repo.get_chapter(chapter_id)
        if ch is None:
            return None
        scenes = await self.repo.list_scenes_by_chapter(chapter_id)
        parts = [s.get("final_prose") or "" for s in scenes]
        scenes_prose = "\n\n".join(p for p in parts if p)
        chapter_body = str(ch.get("prose_md") or "")
        prose = chapter_body if chapter_body.strip() else scenes_prose
        return {
            "chapter_id": chapter_id,
            "book_id": ch.get("book_id", ""),
            "title": ch.get("title", ""),
            "order_no": ch.get("order_no", 0),
            "word_count": prose_chars(prose),
            # ---- v1.46 章级直写字段（新增，不动老字段） ----
            "prose_md": chapter_body,
            "words": prose_chars(prose),
            "word_target": int(ch.get("word_target") or 0),
            "tone": ch.get("tone") or "",
            "summary": ch.get("summary") or "",
            # v1.50：章级制作单（这一章走直写还是涌现，页头徽标+执行层据此选路）
            "mode": ch.get("mode") or "draft",
            "production": _parse_json_obj(ch.get("production_json")),
            "scenes": [
                {
                    "scene_id": s["id"], "title": s.get("title", ""),
                    "prose": s.get("final_prose") or "",
                    "finalized": bool(s.get("final_prose")),
                }
                for s in scenes
            ],
            "prose": prose,
        }

    async def export_book_md(self, book_id: str) -> str:
        """全书导出 markdown：书名 / 简介 / 总字数 / 各章（order_no）/ 已定稿场景正文。

        一章多场景时场景间用 `* * *` 分隔（否则正文直接相连，读者无法分辨场景边界）。
        """
        book = await self.repo.get_book(book_id)
        if book is None:
            raise SceneNotFoundError(f"未找到书: {book_id}")
        chapters = await self.repo.list_chapters_by_book(book_id)

        # 先收集（目录与总字数都要，故两遍）
        chapters_out: list[dict] = []
        total_chars = 0
        for ch in sorted(chapters, key=lambda c: c.get("order_no") or 0):
            scenes = await self.repo.list_scenes_by_chapter(ch["id"])
            # v1.46 直写链路：有章正文就出一块；否则按场景逐场（场景间用 * * * 分隔）
            has_body = bool(str(ch.get("prose_md") or "").strip())
            if has_body:
                bodies = [str(ch.get("prose_md")).strip()]
            else:
                bodies = [str(s["final_prose"]).strip() for s in scenes
                          if (s.get("final_prose") or "").strip()]
            if not bodies:
                continue  # 未定稿章节不入书稿
            chars = prose_chars("\n\n".join(bodies))
            total_chars += chars
            chapters_out.append({
                "order_no": int(ch.get("order_no") or 0),
                "title": str(ch.get("title") or ""),
                "bodies": bodies, "chars": chars,
                "scenes": len(bodies) if not has_body else 0,
                "has_body": has_body,
            })
        if not chapters_out:
            raise InvalidActionError("本书尚无已定稿内容，先在导演台定稿再导出")

        lines: list[str] = [f"# {book.get('title', '无题之书')}", ""]
        if book.get("synopsis"):
            lines += [f"> {book['synopsis']}", ""]
        lines += [f"全书约 {total_chars:,} 字 · 共 {len(chapters_out)} 章", ""]

        # 目录（只列已入书章节；字数与概览 KPI / 章时间线同口径）
        lines += ["## 目录", ""]
        for i, c in enumerate(chapters_out, start=1):
            title = c["title"].strip() or "（无题）"
            # 章标题本身常已含「第 N 章」（主笔排的、手建的都这样）→ 别重复前缀
            tail = (f" / {c['scenes']} 场" if c["scenes"] else "（直写）")
            if re.match(r"^第\s*[0-9一二三四五六七八九十百零]+\s*章", title):
                lines.append(f"{i}. {title} — {c['chars']:,} 字{tail}")
            else:
                label = f"第 {c['order_no']} 章" if c["order_no"] else f"第 {i} 章"
                lines.append(f"{i}. {label} {title} — {c['chars']:,} 字{tail}")
        lines.append("")

        for c in chapters_out:
            lines += [f"## {c['title'] or '（无题）'}", ""]
            lines.append("\n\n* * *\n\n".join(c["bodies"]))
            lines.append("")
        return "\n".join(lines).strip() + "\n"

    async def next_scene_seed(self, sim_id: str) -> Optional[dict]:
        """收束后查下一场景（同 chapter，cursor_pos+1）。仅 converged=True 生效。"""
        sim = await self._load_sim(sim_id)
        if not sim.director.converged or not sim.chapter_id:
            return None
        scenes = await self.repo.list_scenes_by_chapter(sim.chapter_id)
        idx = next((i for i, s in enumerate(scenes) if s["id"] == sim.scene_id), -1)
        if idx < 0 or idx + 1 >= len(scenes):
            return None
        nxt = scenes[idx + 1]
        return {"next_scene_id": nxt["id"], "next_scene_title": nxt["title"],
                "chapter_id": sim.chapter_id, "book_id": sim.book_id}

    async def start_scene_after(self, sim_id: str, next_scene_id: str) -> dict:
        """建下一场景的 sim：搬世界（facts/beliefs/characters/last_main_actor），重置现场。"""
        src = await self._load_sim(sim_id)
        scene = await self.repo.get_scene(next_scene_id)
        if scene is None:
            raise SceneNotFoundError(f"未找到场景: {next_scene_id}")

        spec = scenarios.load_scenario(scene.get("scenario_def") or "betrayal_night")
        # 合并 src facts（继承）+ 新场景 initial_facts（按 id 去重）
        inherited = [f.model_dump() for f in src.world.facts]
        new_facts = json.loads(scene.get("initial_facts_json") or "[]")
        seen = {f["id"] for f in inherited if f.get("id")}
        for f in new_facts:
            if f.get("id") not in seen:
                inherited.append(f)
                seen.add(f.get("id"))

        static_cfg = spec["plan_cfg"] if isinstance(spec["plan_cfg"], PlanCfg) else PlanCfg()
        plan_cfg = _scene_plan_cfg(scene.get("plan_cfg_json") or "", static_cfg)
        sim = models.SimulationState(
            scenario=scene.get("scenario_def") or "betrayal_night",
            book_id=src.book_id, chapter_id=src.chapter_id or scene["chapter_id"],
            scene_id=next_scene_id,
            last_main_actor=src.last_main_actor,           # 搬世界：衔接谁先动
            world=models.WorldState(
                scene_id=next_scene_id,
                title=scene.get("title", ""),
                settings=src.world.settings,
                env_conds=[scene.get("stage_desc") or ""] if scene.get("stage_desc") else [],
                facts=[models.Fact(**f) for f in inherited],
            ),
            characters=src.characters,                     # 搬世界：角色卡+记忆
            beliefs=src.beliefs,                           # 搬世界：信念账本
        )
        if src.scratch.get("world_rules"):
            sim.scratch["world_rules"] = src.scratch["world_rules"]  # 搬世界：规则硬约束
        sim_id = f"sim-{uuid.uuid4().hex[:12]}"
        await self.repo.save(sim_id, sim)
        return {"sim_id": sim_id, "resumed": False, "scene_id": next_scene_id}

    # ---------------------------------------------------------------- 启动叙事（四层装配）
    async def start(self, book_id: str = "", chapter_id: str = "",
                    scene_id: str = "", resume: bool = True) -> dict:
        """按 scene 启动/恢复叙事。

        - resume=True 且该 scene 有未结束 sim → 返回 {sim_id, resumed: True}
        - 否则从 DB 装配场景（初始事实/PlanCfg/角色卡）→ 建新 sim → {sim_id, resumed: False}
        """
        scene = await self.repo.get_scene(scene_id)
        if scene is None:
            raise SceneNotFoundError(f"未找到场景: {scene_id}")

        if resume:
            sid = await self.repo.resume_latest(scene_id)
            if sid:
                return {"sim_id": sid, "resumed": True}

        scenario_def = scene["scenario_def"] or "betrayal_night"
        spec = scenarios.load_scenario(scenario_def)  # 无 Key 回退函数来源（D2）

        # 上传归一：chapter_id / book_id 缺省时由 scene 反查
        if not chapter_id:
            chapter_id = scene["chapter_id"]
        if not book_id:
            ch = await self.repo.get_chapter(chapter_id)
            book_id = ch["book_id"] if ch else ""

        # 从 DB 装配：初始事实 + 导演 PlanCfg（DB 优先）+ 角色卡（④层字段）
        initial_facts = [
            models.Fact(**f) for f in json.loads(scene.get("initial_facts_json") or "[]")
        ]
        static_cfg = spec["plan_cfg"] if isinstance(spec["plan_cfg"], PlanCfg) else PlanCfg()
        plan_cfg = _scene_plan_cfg(scene.get("plan_cfg_json") or "", static_cfg)

        char_rows = await self.repo.list_characters_for_scene(scene_id)
        # ---- v1.53 默认选角分层：涌现只演"能改变局面的人"（主角/关键配角/反派，上限 5） ----
        # 背景/次要角色**不进回合**（他们只作为"在场背景"进写手素材）：
        # 这样既不会群像开会、摊薄张力，也不会把成本花在龙套上。作者仍可手工改 cast。
        from app.services.engine.cast_roles import default_cast

        related_text = " ".join(str(x) for x in (
            scene.get("title"), scene.get("goal"), scene.get("content_desc"),
            scene.get("stage_desc")) if str(x or "").strip())
        cards_by_id: dict[str, models.CharacterCard] = {}
        roster: list[dict] = []
        for row in char_rows:
            try:
                card = models.CharacterCard.model_validate_json(row["spec_json"])
            except Exception:  # noqa: BLE001
                continue
            if not card.id or card.id in cards_by_id:  # 防御：spec 未回填 id 的坏卡不装配
                continue
            cards_by_id[card.id] = card
            roster.append({"id": card.id, "name": card.name,
                           "role_tier": row.get("role_tier") or "minor",
                           "summary": _parse_json_obj(row.get("spec_json")).get("summary") or ""})
        cast_info = default_cast(roster, related_text=related_text)
        keep = set(cast_info["cast"])
        characters: dict[str, models.CharacterCard] = {
            cid: card for cid, card in cards_by_id.items() if not keep or cid in keep}
        # 空黑板：书级/特设均无角色卡 → 保持空（不再注入静态剧情角色，见导演台空态引导）

        sim = models.SimulationState(
            scenario=scenario_def,
            book_id=book_id,
            chapter_id=chapter_id,
            scene_id=scene_id,
            world=models.WorldState(scene_id=scene_id, title=scene["title"]),
        )
        sim.characters = characters
        # 空黑板：仅写场景初始事实；无初始事实则 facts 空（不回退模板剧情事实），
        # 舞台布置（stage_desc）作为环境条件承载，供导演/旁白感知开场。
        sim.world.facts = list(initial_facts)
        stage_desc = str(scene.get("stage_desc") or "").strip()
        if stage_desc:
            sim.world.env_conds = [stage_desc]
        sim.director.ending_options = list(plan_cfg.ending_options)
        sim.action_order = [c for c in sim.characters]
        sim.scratch["cast_all"] = list(sim.action_order)   # v1.54：节流的完整名单（每回合从它挑）
        # v1.54：节流要用到档位与主角名单（CharacterCard 里没有这两个信息，放 scratch）
        sim.scratch["cast_tiers"] = {r["id"]: r.get("role_tier") or "minor" for r in roster}
        sim.scratch["protagonists"] = [r["id"] for r in roster
                                       if (r.get("role_tier") == "protagonist")]
        # 空世界标记（characters 与 facts 皆空）：前端据此显示"配置上场角色"引导
        if not sim.characters and not sim.world.facts:
            sim.scratch["empty_world"] = True
        await self._inject_world_rules(sim, book_id)

        sid = f"sim-{uuid.uuid4().hex[:8]}"
        await self.repo.save(sid, sim)
        return {"sim_id": sid, "resumed": False,
                # v1.53：默认选角的分层结果（谁进回合 / 谁只作背景 / 为什么）
                "cast": cast_info}

    async def _load_sim(self, sim_id: str) -> models.SimulationState:
        """统一取 sim，缺失抛 SimNotFoundError（全局 handler 转 404）。"""
        sim = await self.repo.load(sim_id)
        if sim is None:
            raise SimNotFoundError(f"未找到模拟: {sim_id}")
        return sim

    async def _inject_world_rules(self, sim: models.SimulationState, book_id: str) -> None:
        """把书的 world_rules_json 注入 sim（护栏第 4 步用，0 token 检测）。"""
        if not book_id:
            return
        try:
            book = await self.repo.get_book(book_id)
            rules_raw = (book or {}).get("world_rules_json") or "[]"
            sim.scratch["world_rules"] = json.loads(rules_raw) if rules_raw.strip() else []
        except Exception as e:  # noqa: BLE001
            logger.warning("[service] 注入世界规则失败（忽略）：%s", e)

    async def _sim_plan_cfg(self, sim: models.SimulationState, static_cfg: PlanCfg) -> PlanCfg:
        """step/stream_step 用的导演 PlanCfg（B59）。

        为什么必须从场景读：`plan_cfg_json` 里放着主笔写的 **beats（必然事件）** 与
        作者/主笔配的 goal_pressure、expose_target。此前 step 直接 `_scene_plan_cfg("{}")`
        → **推演阶段这些配置全部失效**（beats 收束闸门永远看不到硬约束 = 形同虚设）。
        """
        raw = "{}"
        try:
            scene = await self.repo.get_scene(sim.scene_id) if sim.scene_id else None
            raw = str((scene or {}).get("plan_cfg_json") or "{}")
        except Exception as e:  # noqa: BLE001  读不到就退回静态配置，不挡推演
            logger.warning("[service] 读场景 plan_cfg 失败（用静态默认）：%s", e)
        cfg = _scene_plan_cfg(static_cfg=static_cfg, plan_cfg_json=raw)
        cfg.ending_options = list(sim.director.ending_options)   # 快照里的是权威
        return cfg

    # ---------------------------------------------------------------- step 回合
    async def step(self, sim_id: str, n: int = 1) -> models.SimulationState:
        sim = await self._load_sim(sim_id)
        spec = scenarios.load_scenario(sim.scenario)  # 按 sim 的场景，而非全局默认
        static_cfg = spec["plan_cfg"] if isinstance(spec["plan_cfg"], PlanCfg) else PlanCfg()
        # 导演 PlanCfg：**从 sim 所属场景的 plan_cfg_json 读**（B59：beats/goal_pressure 必须生效）
        plan_cfg = await self._sim_plan_cfg(sim, static_cfg)
        graph = self._graph(plan_cfg, spec["decide_fn"], spec["converge_fn"])
        for _ in range(n):
            # 终止：真正完结（收束/超上限）才停；举手(pending)是暂停，accept 后可继续
            if sim.ended or sim.director.converged:
                break
            _ensure_cast(sim)     # 空 cast 硬拦：不许"没角色也在演绎"（空转回合无任何产出）
            prev_events = len(sim.events)
            state = await graph.ainvoke({"sim": sim, "last_main_actor": sim.last_main_actor or None})
            # 主戏角色写回 sim（跨回合轮换记忆持久化）
            if state.get("last_main_actor"):
                sim.last_main_actor = state["last_main_actor"]
            # 回合归档：回退/查看任意回合
            self._archive_turn(sim, prev_events)
            # v1.72：顺手清掉历史遗留空壳归档（老数据/LLM 挂掉期间产生的），
            # 作者推进任意一回合即自愈，不必等手动清理
            self.prune_empty_archives(sim)
            await self.repo.save(sim_id, sim)
            await self._sync_beliefs(sim)  # 信念账本草稿 → 书级 Belief 表（幂等）
            # 单回合内举手 → 暂停等作者拍板（外层 SSE 循环会因 pending 停止）
            if sim.director.raise_request.pending:
                break
        if sim.ended:
            await self._persist_beats_status(sim)   # v1.57：beats 结论跟着场景走
        return sim

    async def stream_step(self, sim_id: str) -> AsyncIterator[dict]:
        """事件级流式 step：用 graph.astream(stream_mode='custom') 驱动单回合图，
        节点内每完成一步即 writer 推事件，本生成器逐事件 yield（即到即出），
        回合结束落库。供 SSE 消费；客户端断连以 CancelledError 终止。
        """
        sim = await self._load_sim(sim_id)
        spec = scenarios.load_scenario(sim.scenario)
        static_cfg = spec["plan_cfg"] if isinstance(spec["plan_cfg"], PlanCfg) else PlanCfg()
        plan_cfg = await self._sim_plan_cfg(sim, static_cfg)
        graph = self._graph(plan_cfg, spec["decide_fn"], spec["converge_fn"])

        if sim.ended or sim.director.converged:
            return
        _ensure_cast(sim)     # 空 cast 硬拦（SSE 同样不许空转）
        prev_events = len(sim.events)
        async for event in graph.astream(
            {"sim": sim, "last_main_actor": sim.last_main_actor or None},
            stream_mode="custom",
        ):
            if isinstance(event, dict) and "kind" in event:
                yield event
        # 回合结束：主戏角色写回 + 归档 + 落库
        self._archive_turn(sim, prev_events)
        self.prune_empty_archives(sim)   # v1.72：SSE 同样自愈
        await self.repo.save(sim_id, sim)
        await self._sync_beliefs(sim)  # 信念账本草稿 → 书级 Belief 表（幂等）
        if sim.ended:
            await self._persist_beats_status(sim)   # v1.57：beats 结论跟着场景走

    async def _sync_beliefs(self, sim: models.SimulationState) -> None:
        """把 sim 运行时信念幂等 upsert 到书级 Belief 表（自动沉淀：edited=False，只增不删）。"""
        if not sim.book_id:
            return
        for char_id, items in sim.beliefs.items():
            for b in items:
                key = f"{sim.book_id}:{char_id}:{b.fact_id}"
                bid = f"bel-{hashlib.md5(key.encode('utf-8')).hexdigest()[:12]}"
                await self.repo.save_belief({
                    "id": bid, "book_id": sim.book_id, "char_id": char_id,
                    "fact_id": b.fact_id, "source_event_id": b.source_event_id,
                    "channel": b.channel.value if hasattr(b.channel, "value") else str(b.channel),
                    "text": b.text, "confidence": b.confidence, "edited": False, "ts": b.ts,
                })

    @staticmethod
    def prune_empty_archives(sim: models.SimulationState) -> int:
        """清掉时间线里的**空壳回合**（无角色行动、无成文），返回删除条数。

        v1.72：v1.70 只做到"新回合不再归档空壳 + history 展示层过滤"，DB 里老的空壳
        一直留着：作者看到 /history 的 total_archived=20 而真实只有 1 个回合，
        归档里也留着 19 条假数据（导出剧本/张力曲线也算进去）。这里做**物理清理**，
        口径与 _archive_is_empty 一致；幂等，随时可调。
        """
        before = len(sim.turn_archives or [])
        sim.turn_archives = [a for a in (sim.turn_archives or []) if not _archive_is_empty(a)]
        return before - len(sim.turn_archives)

    @staticmethod
    def _archive_turn(sim: models.SimulationState, prev_events: int) -> None:
        """把刚结束的回合归档进 sim.turn_archives（供时间线回退查看）。

        v1.70 修：本回合**既无角色行动也无成文**时不归档（导演提示-only 的空回合）——
        否则会出现"演绎了 19 回合、点开全是空的"假时间线（真实数据实测：LLM 挂掉期间
        每回合只有 director_hint 事件，character_phase 无产出，19 回合全部为空壳）。
        空回合不进时间线，作者点「⟳ 新开」重置或补选角色后重新推演即可。
        """
        turn = sim.world.turn
        new_events = sim.events[prev_events:]
        if not new_events:
            return
        # 本回合角色行动（作为事件展示）
        acted = []
        for e in new_events:
            if e.source.value == "character" and e.payload.get("text"):
                acted.append({
                    "id": e.id, "actor": e.actor,
                    "kind": e.payload.get("kind", "action"),
                    "text": e.payload.get("text", ""),
                })
        prose = ""
        for e in new_events:
            if e.type.value == "text" and e.payload.get("text"):
                prose = e.payload["text"]
        # 空回合拦截：没有角色行动、也没有成文 → 时间线不收（避免"空壳回合"假象）
        if not acted and not prose:
            return
        # 回合类型：从本回合事件推断（冲突优先）
        cls = "type-info"
        for e in new_events:
            k = e.payload.get("kind")
            if k == "conflict":
                cls = "type-conflict"
                break
            if k == "dialogue" and cls == "type-info":
                cls = "type-dialogue"
            elif k == "action" and cls == "type-info":
                cls = "type-action"
        # 回合摘要：优先导演产物（短、有方向感；如"舞台提示：让 李文 先接话…"），
        # 没有则用本回合正文/末条行动截断 —— 不必 LLM 重新生成长摘要
        briefing = ""
        if sim.director.hint and sim.director.hint.stage_prompt:
            briefing = sim.director.hint.stage_prompt
        elif sim.director.injected_events:
            briefing = sim.director.injected_events[0]
        if briefing.startswith("舞台提示："):
            briefing = briefing[len("舞台提示："):]
        summary = (briefing or prose or (acted[-1]["text"] if acted else "")).strip()
        if not summary:
            summary = f"回合 T-{turn:02d}"
        # S4：把本回合各角色的"思考"归入归档，并**清空 scratch**。
        # scratch["thoughts"] 按 char_id 覆盖，若不归集清空，第二回合就把上一回合的
        # 思考冲掉了（时间线/回放/导出查不到当时的思考）。
        thoughts: list[dict] = []
        for cid, data in (sim.scratch.get("thoughts") or {}).items():
            d = data if isinstance(data, dict) else {"thought": str(data)}
            card = sim.characters.get(cid)
            thoughts.append({
                "char": getattr(card, "name", "") or cid,
                "char_id": cid,
                "monologue": str(d.get("thought") or d.get("monologue") or "").strip(),
                "emotion": str(d.get("emotion") or "").strip(),
                "reasoning": str(d.get("reasoning") or "").strip(),
                "raw": d,
            })
        sim.scratch["thoughts"] = {}
        sim.turn_archives = [a for a in sim.turn_archives if a.turn != turn]
        sim.turn_archives.append(models.TurnArchive(
            turn=turn,
            tension=sim.director.tension,
            tension_trend=sim.director.tension_trend,
            cls=cls,
            summary=summary[:18] or f"回合 T-{turn:02d}",
            prose=prose,
            events=acted,
            thoughts=thoughts,
        ))

    # ---------------------------------------------------------------- S4 涌现产物（剧本）
    async def _archives_by_scene(self, book_id: str) -> dict[str, list[dict]]:
        """每场景的回合归档：{scene_id: [archive dict]}（S3/S4 共用）。

        **取"最新且确实有归档"的那个 sim**，而不是盲目取最新一行：新建/废弃的 sim
        （0 归档）会盖掉有真实推演数据的旧 sim → 剧本与张力曲线显示为空（真实数据实测踩过）。
        """
        by_scene: dict[str, list[str]] = {}
        for s in await self.repo.list_sims_by_book(book_id):   # 已按 updated_at DESC
            sid = str(s.get("scene_id") or "")
            if sid:
                by_scene.setdefault(sid, []).append(str(s.get("id")))
        out: dict[str, list[dict]] = {}
        for scene_id, sim_ids in by_scene.items():
            newest: list[dict] = []
            for sim_id in sim_ids:
                sim = await self.repo.load(sim_id)
                if sim is None:
                    continue
                arcs = [a.model_dump() for a in (sim.turn_archives or [])]
                if arcs:
                    out[scene_id] = arcs
                    break
                if not newest:
                    newest = arcs
            else:
                out[scene_id] = newest
        return out

    async def scene_script(self, scene_id: str, with_thoughts: bool = True,
                           with_tension: bool = False) -> dict:
        """S4：单场景剧本产物（Markdown 剧本体 + JSON 结构化）。"""
        from app.services.engine.emergence import (
            extract_hits, render_script_json, render_script_md,
        )

        scene, book_id = await self._scene_and_book(scene_id)
        archives = (await self._archives_by_scene(book_id)).get(scene_id, []) if book_id else []
        return {
            "scene_id": scene_id, "turns": len(archives),
            "markdown": render_script_md(scene, archives, with_thoughts=with_thoughts,
                                         with_tension=with_tension),
            "json": render_script_json(scene, archives),
            "hits": extract_hits(scene, archives),   # S4 step2：确定性高光（供采纳为素材）
        }

    async def adopt_emergence_hits(self, scene_id: str,
                                   turns: list[int] | None = None) -> dict:
        """把确定性高光**采纳为灵感卡**（复用灵感池：零新表 + 采纳流 + 前端面板现成）。"""
        from app.services.engine.emergence import HIT_LABEL, extract_hits

        scene, book_id = await self._scene_and_book(scene_id)
        if not book_id:
            raise InvalidActionError(f"场景未关联书籍: {scene_id}")
        archives = (await self._archives_by_scene(book_id)).get(scene_id, [])
        hits = extract_hits(scene, archives)
        wanted = set(turns or [])
        picked = [h for h in hits if not wanted or h["turn"] in wanted]
        cards = await self.repo.list_inspirations(book_id)
        order = max([c.get("sort_order") or 0 for c in cards], default=0)
        created: list[dict] = []
        for h in picked:
            order += 1
            label = "、".join(HIT_LABEL.get(k, k) for k in h["kinds"])
            desc = "【涌现高光·%s】%s" % (label, h["summary"] or "")
            if h.get("quote"):
                desc += "｜台词：%s" % h["quote"]
            card = {
                "id": f"insp-{uuid.uuid4().hex[:10]}", "book_id": book_id,
                "icon": "✨", "title": ("剧本高光 T-%02d" % h["turn"])[:12],
                "desc": desc[:400], "type": "plot", "source": "emergence",
                "adopted": False, "sort_order": order,
            }
            await self.repo.save_inspiration(card)
            created.append(card)
        return {"scene_id": scene_id, "hits": hits, "picked": len(picked),
                "adopted": len(created), "cards": created}

    async def book_script(self, book_id: str, with_thoughts: bool = True,
                          with_tension: bool = False) -> dict:
        """S4：全书剧本产物（按章节/场景顺序拼接，仅含有推演回合的场景）。"""
        from app.services.engine.emergence import render_script_md

        tree = await self.repo.get_book_tree(book_id)
        chapters = sorted((tree or {}).get("chapters") or [], key=lambda c: c.get("order_no") or 0)
        arch = await self._archives_by_scene(book_id)
        parts: list[str] = []
        scenes_n = turns_n = 0
        for ch in chapters:
            for sc in await self.repo.list_scenes_by_chapter(ch["id"]):
                a = arch.get(str(sc.get("id")), [])
                if not a:
                    continue
                scenes_n += 1
                turns_n += len(a)
                parts.append(render_script_md(sc, a, with_thoughts=with_thoughts,
                                              with_tension=with_tension))
        return {"book_id": book_id, "scenes": scenes_n, "turns": turns_n,
                "markdown": "\n\n---\n\n".join(parts) if parts else "（本书尚无推演回合）"}

    # ---------------------------------------------------------------- 查看状态
    async def get_state(self, sim_id: str) -> models.SimulationState:
        return await self._load_sim(sim_id)

    # ---------------------------------------------------------------- 导演共创对话（逐 token 流式）
    async def director_chat(self, sim_id: str, message: str) -> AsyncIterator[str]:
        """作者↔导演 agent 共创对话：逐 token 流式返回导演的创作指导。

        LLM 不可用/产出为空/流式中断 → 抛 DirectorChatUnavailableError
        （路由层在流内转 SSE error 事件，前端展示"导演离线"兜底）。
        MVP 先只做实时往返，不落库；未来可沉淀为"共创记录"进章节。
        """
        sim = await self._load_sim(sim_id)
        from app.services.engine.director import DirectorEngine

        director = DirectorEngine(sim)
        async for tok in director.stream_chat(message):
            yield tok

    # ---------------------------------------------------------------- 作者介入（举手）
    async def intervene(self, sim_id: str, action: str, payload: dict) -> models.SimulationState:
        sim = await self._load_sim(sim_id)

        a = action.strip().lower()
        if a in {"accept", "agree", "同意"}:
            sim.director.raise_request.pending = False
            sim.ended = False  # 举手暂停恢复：cleared by author approval
            text = payload.get("text", "")
            if text:
                await self._append_director_hint(sim, text)
        elif a in {"reject", "驳回"}:
            sim.director.raise_request.pending = False
        elif a == "inject_event":
            text = payload.get("text", "")
            if text:
                await self._append_director_hint(sim, text)
        elif a == "expose":
            # 信息曝光：把某事实记为目标角色的 belief（来源 DIR，§6.1 信息差/筹码）
            from app.services.engine.world import WorldEngine

            fid = payload.get("fact_id", "")
            target = payload.get("target_char_id", "")
            fact = next((f for f in sim.world.facts if f.id == fid), None)
            if fact is None:
                raise InvalidActionError(f"无可曝光事实: {fid}")
            if target not in sim.characters:
                raise InvalidActionError(f"未知目标角色: {target}")
            try:
                channel = models.BeliefChannel(str(payload.get("channel") or "perceived"))
            except ValueError:
                raise InvalidActionError(f"非法 channel: {payload.get('channel')!r}")
            WorldEngine(sim).record_belief(
                char_id=target, fact_id=fact.id,
                source_event_id="DIR", channel=channel, text=fact.text, confidence=0.8,
            )
        elif a == "adjust_weight":
            # 目标权重调整：必须伴随剧情内因（§6.1 因果律）——内因以 hint 事件写回可感知
            from app.services.engine.world import WorldEngine

            char_id = payload.get("char_id", "")
            goal_id = payload.get("goal_id", "")
            delta = float(payload.get("delta") or 0)
            reason = str(payload.get("reason") or "").strip()
            if not reason:
                raise InvalidActionError("目标权重调整必须附带剧情内因（§6.1 因果律）")
            WorldEngine(sim).apply_goal_adjust(models.GoalAdjust(
                char_id=char_id, goal_id=goal_id, delta=delta, reason=reason,
            ))
            await self._append_director_hint(sim, f"目标权重：{char_id}·{goal_id} {delta:+g}（因：{reason}）")
        else:
            raise InvalidActionError(f"未知介入动作: {action}")
        await self.repo.save(sim_id, sim)
        await self._sync_beliefs(sim)  # expose 等介入会产生信念 → 同步书级账本
        return sim

    async def get_inject_palette(self, sim_id: str) -> dict:
        """介入工具的下拉数据源：当前运行时 facts（可曝光）+ 角色动态目标（可调权）。"""
        sim = await self._load_sim(sim_id)
        facts = [
            {"id": f.id, "text": f.text, "kind": f.kind, "visible_to": f.visible_to}
            for f in sim.world.facts if f.active
        ]
        characters = [
            {
                "id": cid, "name": c.name,
                "dynamic_goals": [
                    {"id": g.id, "text": g.text, "weight": g.weight}
                    for g in c.dynamic_goals
                ],
            }
            for cid, c in sim.characters.items()
        ]
        return {"turn": sim.world.turn, "facts": facts, "characters": characters}

    # ---------------------------------------------------------------- 选角（Cast）
    async def set_sim_cast(self, sim_id: str, character_ids: list[str]) -> dict:
        """配置上场角色：以书级角色库重建 sim.characters / action_order。

        cast 不重启局面：facts/events/turn 保留；beliefs 裁剪到仍上场的角色。
        """
        sim = await self._load_sim(sim_id)
        if not sim.book_id:
            raise InvalidActionError("该 sim 无书级上下文，无法选角")

        book_chars = await self.repo.list_characters_by_book(sim.book_id)
        by_id: dict[str, dict] = {}
        for c in book_chars:
            try:
                card = models.CharacterCard.model_validate_json(c["spec_json"])
            except Exception:  # noqa: BLE001 —— 坏卡跳过
                continue
            if not card.id:  # spec 未回填 id：跳过（无法作为上场角色）
                continue
            # 双 id 兼容：sim 侧以 spec.id 为 key（与 start 装配一致）；row id 兜底（旧数据/未回填）
            by_id[card.id] = c
            by_id.setdefault(c.get("id"), c)
        unknown = [c for c in character_ids if c not in by_id]
        if unknown:
            raise InvalidActionError(f"以下角色不在该书角色库: {unknown}")

        characters: dict[str, models.CharacterCard] = {}
        for cid in character_ids:
            row = by_id[cid]
            try:
                card = models.CharacterCard.model_validate_json(row["spec_json"])
            except Exception:  # noqa: BLE001 —— spec 损坏跳过，不让坏卡上车
                continue
            characters[card.id] = card

        sim.characters = characters
        sim.action_order = [c for c in characters]
        sim.beliefs = {k: v for k, v in sim.beliefs.items() if k in characters}
        # 选角后不再视为"空世界"（即便无 facts，也有上场角色等待开场）
        sim.scratch.pop("empty_world", None)
        await self.repo.save(sim_id, sim)
        return {"sim_id": sim_id, "characters": list(sim.characters.keys())}

    @staticmethod
    async def _append_director_hint(sim: models.SimulationState, text: str) -> None:
        from app.services.engine.world import WorldEngine

        world = WorldEngine(sim)
        world.append_event(
            type_=models.EventType.director_hint,
            source=models.EventSource.director,
            actor="导演",
            payload={"hints": [text]},
        )

    # ---------------------------------------------------------------- 回退（时间线）
    async def rewind(self, sim_id: str, turn: int) -> models.SimulationState:
        """回退到指定回合：截断事件/归档/张力，可从此重演。

        目标回合的 events 被保留到该回合最后一个角色事件为止；后续归档删除；
        导演状态（张力/趋势/举手/收敛）重置为该回合归档值；world.facts 保留（增量演化
        不回滚，MVP 以事件回退 + 重演覆盖为准）。
        """
        sim = await self._load_sim(sim_id)

        target = next((a for a in sim.turn_archives if a.turn == turn), None)
        if target is None:
            raise InvalidActionError(f"无回合 {turn} 的归档（当前至 T-{sim.world.turn}）")

        # 截断事件：保留到该回合最后一个角色事件
        cut_turn = target.turn
        last_char_ev_idx = -1
        for i, e in enumerate(sim.events):
            if e.source.value == "character":
                last_char_ev_idx = i
            elif e.turn > cut_turn:
                break
        sim.events = sim.events[: last_char_ev_idx + 1]
        sim.world.turn = cut_turn
        sim.world.timeline = [e.id for e in sim.events]
        sim.director.tension = target.tension
        sim.director.tension_trend = target.tension_trend
        sim.director.raise_request.pending = False
        sim.director.converged = False
        sim.director.ending_selected = ""
        sim.ended = False
        sim.paused = False
        sim.turn_archives = [a for a in sim.turn_archives if a.turn <= cut_turn]
        # v1.72：回退后连带清掉空壳（否则回退完时间线里还留着一串假回合，
        # "回退到 T-02"之后仍显示 T-19 —— 实测出现过）
        self.prune_empty_archives(sim)

        await self.repo.save(sim_id, sim)
        return sim

    async def prune_archives(self, sim_id: str) -> dict:
        """维护入口：物理清理某 sim 的空壳回合归档（v1.72，给存量数据与前端按钮用）。"""
        sim = await self._load_sim(sim_id)
        before = len(sim.turn_archives or [])
        removed = self.prune_empty_archives(sim)
        if removed:
            await self.repo.save(sim_id, sim)
        return {
            "sim_id": sim_id,
            "turn": sim.world.turn,
            "total_archived": before,     # 清理前
            "removed": removed,           # 删掉的空壳条数
            "kept": len(sim.turn_archives or []),  # 清理后
        }

    # ---------------------------------------------------------------- 事件暂停（回合边界）
    async def pause(self, sim_id: str) -> models.SimulationState:
        """暂停推演：标记 paused。正在跑的回合不中断（回合边界暂停），
        SSE 循环每回合结束后检查 paused → 停住；resume 后从下一回合继续。"""
        sim = await self._load_sim(sim_id)
        sim.paused = True
        await self.repo.save(sim_id, sim)
        return sim

    async def resume(self, sim_id: str) -> models.SimulationState:
        """恢复推演：清除 paused（ended 保持原样）。"""
        sim = await self._load_sim(sim_id)
        sim.paused = False
        await self.repo.save(sim_id, sim)
        return sim