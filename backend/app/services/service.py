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
import time
import uuid
from datetime import datetime
from typing import AsyncIterator

from langgraph.graph.state import CompiledStateGraph

from app import scenarios
from app.config import settings
from app.db.repo import Repo
from app.errors import InvalidActionError, SceneNotFoundError, SimNotFoundError
from app.schemas import models
from app.services.engine.director import PlanCfg
from app.services.engine.style_checks import voice_prior as style_voice_prior
from app.services.llm.client import client as llm_client  # 模块级引用：便于测试统一替换（conftest）

logger = logging.getLogger(__name__)


def _scene_plan_cfg(plan_cfg_json: str, static_cfg: PlanCfg) -> PlanCfg:
    """融合 DB 场景配置（优先）与静态回退：缺哪个补哪个。"""
    db = json.loads(plan_cfg_json or "{}")
    return PlanCfg(
        secret_fact_ids=list(db.get("secret_fact_ids") or static_cfg.secret_fact_ids),
        expose_target=dict(db.get("expose_target") or static_cfg.expose_target),
        goal_pressure=[dict(p) for p in (db.get("goal_pressure") or static_cfg.goal_pressure)],
        env_pressure_lines=list(db.get("env_pressure_lines") or static_cfg.env_pressure_lines),
        raise_after_turn=db.get("raise_after_turn") or static_cfg.raise_after_turn,
        ending_options=list(db.get("ending_options") or static_cfg.ending_options),
    )


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
        return await self.repo.list_scenes_by_chapter(chapter_id)

    async def get_chapter(self, chapter_id: str) -> dict | None:
        """章节详情（含 book_id，供导演台 scene→chapter→book 反查书树）。"""
        return await self.repo.get_chapter(chapter_id)

    async def get_scene(self, scene_id: str) -> dict | None:
        return await self.repo.get_scene(scene_id)

    async def get_scene_characters(self, scene_id: str) -> list[dict]:
        """场景可用角色 = 本书书级角色 + 场景特设角色。"""
        return await self.repo.list_characters_for_scene(scene_id)

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

    @staticmethod
    def _clamp_conf(value) -> float:
        try:
            return max(0.0, min(1.0, float(value)))
        except (TypeError, ValueError):
            return 0.5

    # ---------------------------------------------------------------- Dashboard 聚合
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

        # 每场景取最新一个 sim（重跑场景时旧 sim 作废，避免重复计数）
        latest_by_scene: dict[str, str] = {}
        for s in await self.repo.list_sims_by_book(book_id):
            sid = str(s.get("scene_id") or "")
            if sid and sid not in latest_by_scene:
                latest_by_scene[sid] = str(s.get("id"))

        archives_by_scene: dict[str, list[dict]] = {}
        for scene_id, sim_id in latest_by_scene.items():
            sim = await self.repo.load(sim_id)
            if sim is None:
                continue
            archives_by_scene[scene_id] = [
                {"tension": t.tension, "tension_trend": t.tension_trend}
                for t in (sim.turn_archives or [])
            ]

        foreshadows = await self.repo.list_foreshadows(book_id)
        view = build_global_view(chapters, scenes_all, archives_by_scene, foreshadows)
        view["book_id"] = book_id
        view["title"] = book.get("title") or ""
        view["sims"] = len(latest_by_scene)
        return view

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

        word_count = sum(len(s.get("final_prose") or "") for s in scenes_all)

        # ---- 时间线：章状态 + 章字数 ----
        timeline: list[dict] = []
        chapters_done = 0
        for ch in chapters:
            ch_scenes = scenes_by_ch.get(ch["id"], [])
            fully_done = bool(ch_scenes) and all(
                (s.get("final_prose") or "").strip() for s in ch_scenes
            )
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
                "word_count": sum(len(s.get("final_prose") or "") for s in ch_scenes),
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

        # ---- 金句（从最终正文抽对话行）----
        quotes: list[dict] = []
        for s in scenes_all:
            prose = s.get("final_prose") or ""
            for line in prose.splitlines():
                line = line.strip()
                if line and (("「" in line and "」" in line) or (line.startswith('"') and line.endswith('"'))):
                    quotes.append({"text": line[:60], "author": s.get("title") or "—— 正文"})
                    if len(quotes) >= 3:
                        break
            if len(quotes) >= 3:
                break

        # ---- 近 4 周热力（按定稿时间散布字数，数据不足则全 0）----
        heat = [0.0] * 28
        now = datetime.now()
        for s in scenes_all:
            if not (s.get("final_prose") or "").strip():
                continue
            ts = s.get("updated_at") or s.get("created_at")
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
                heat[27 - days] += len(s.get("final_prose") or "")

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
        return await self.repo.get_book_tree(book_id)

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
        await self.repo.save_chapter(data)
        return {"id": cid}

    async def update_chapter(self, chapter_id: str, data: dict) -> dict:
        await self.repo.save_chapter({**data, "id": chapter_id})
        return {"id": chapter_id}

    async def delete_chapter(self, chapter_id: str) -> None:
        await self.repo.delete_chapter(chapter_id)

    async def create_scene(self, chapter_id: str, data: dict) -> dict:
        sid = data.get("id") or f"scene-{uuid.uuid4().hex[:8]}"
        data = {**data, "id": sid, "chapter_id": chapter_id}
        await self.repo.save_scene(data)
        return {"id": sid}

    async def update_scene(self, scene_id: str, data: dict) -> dict:
        await self.repo.save_scene({**data, "id": scene_id})
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
        await self.repo.save_character({**data, "id": char_id})
        return {"id": char_id}

    async def delete_character(self, char_id: str) -> None:
        await self.repo.delete_character(char_id)

    # ---------------------------------------------------------------- 主笔规划（S2）
    async def plan_book(self, book_id: str, direction: str = "", inspiration_ids: list[str] | None = None) -> dict:
        """主笔产出骨架预览（不落库）：POST /books/{id}/plan。

        inspiration_ids 非空时，把已采纳灵感卡作为"已在酝酿的设定"注入主笔（P1-1 灵感落地）。
        上下文统一走 chief_perceive（书树+记忆+灵感+伏笔），杜绝从零重排。
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
        plan = await plan_skelly(direction, context or book.get("synopsis", ""),
                                 inspirations=inspirations or None)
        return {"book_id": book_id, "plan": plan}

    async def commit_book_plan(self, book_id: str, plan: dict) -> dict:
        """确认骨架 → 落库：worldview/world_rules + chapters/scenes + foreshadows。

        全量替换语义：先清理该书已有的章节/场景，再按 plan 重新创建，
        避免多次提交 plan 时因标题差异导致重复章节/场景。
        """
        book = await self.repo.get_book(book_id)
        if book is None:
            raise SceneNotFoundError(f"未找到书: {book_id}")

        worldview = plan.get("worldview") or {}
        # 1) 世界观 + 规则解析（LLM 转结构化，检测阶段免费）
        from app.services.engine.chief_planner import parse_world_rules

        rules_text = str(worldview.get("rules_text") or "")
        world_rules = await parse_world_rules(rules_text) if rules_text.strip() else []
        await self.repo.save_book({
            "id": book_id,
            "synopsis": worldview.get("premise") or book.get("synopsis") or "",
            "worldview_json": json.dumps(worldview, ensure_ascii=False),
            "world_rules_json": json.dumps(world_rules, ensure_ascii=False),
        })

        # 2) 清理已有章节/场景（全量替换），再按 plan 创建
        existing_chapters = await self.list_chapters(book_id)
        for ch in existing_chapters:
            await self.repo.delete_chapter(ch["id"])

        chapters = plan.get("chapters") or []
        scene_ids: list[str] = []
        for i, ch in enumerate(chapters):
            ch_title = ch.get("title", f"第{i + 1}章")
            cid = (await self.create_chapter(book_id, {
                "title": ch_title,
                "summary": ch.get("summary", ""),
                "order_no": i + 1,
                "tone": ch.get("tone", ""),
                "tension_curve": ch.get("tension_curve", ""),
                "word_target": int(ch.get("word_target") or 0),
            }))["id"]

            scenes = ch.get("scenes") or []
            for j, sc in enumerate(scenes):
                sc_title = sc.get("title", f"场景{j + 1}")
                r = await self.create_scene(cid, {
                    "title": sc_title,
                    "stage_desc": sc.get("stage_desc", ""),
                    "goal": sc.get("goal", ""),
                    "content_desc": sc.get("content_desc", ""),
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

        return {
            "book_id": book_id,
            "chapters": len(chapters),
            "scenes": len(scene_ids),
            "foreshadows": f_count,
            "rules": len(world_rules),
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

    async def chief_perceive(self, book_id: str, limit: int = 20) -> str:
        """主笔感知装配：书树摘要 + 书级记忆 + 已采纳灵感卡 + 伏笔现状 → 上下文文本。

        所有主笔入口（对话/规划/正文生成）统一走此函数，杜绝「从零重排、多书串味」。
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
            # 世界状态现状（S1：战力/道具/时间/术语/数字骨架，过滤作者手改项）
            world_states = await self.repo.list_world_states(book_id)
            canon = [w for w in world_states if not w.get("edited")]
            if canon:
                lines = "\n".join(
                    f"- [{w.get('kind')}] {w.get('name')}：{w.get('value')}"
                    for w in canon[:30]
                )
                parts.append("世界状态现状（战力/道具/时间/术语/关键数字，写作保持与账本一致）：\n" + lines)
        except Exception:  # noqa: BLE001
            pass

        return "\n\n".join(parts)

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

    async def prose_draft(self, scene_id: str) -> dict:
        """写手：生成正文初稿（注入角色卡 + 前文摘要），并留 writer 追溯记录。"""
        from app.services.engine.prose import draft_prose

        scene, book_id = await self._scene_and_book(scene_id)
        memory = await self.chief_perceive(book_id, limit=8) if book_id else ""
        existing = str(scene.get("final_prose") or "")

        # 获取出场角色卡
        characters = await self.repo.list_characters_for_scene(scene_id) if scene_id else []

        # 感知注入：相关世界状态（只喂"与本场相关的状态"，不灌全书）
        #   - character/item/term：仅当 name 匹配出场角色 → 精确相关
        #   - time/numeric：全书级，直接注入（时间锚/关键数字是全局骨架）
        world_states = []
        if book_id:
            try:
                all_ws = await self.repo.list_world_states(book_id)
                cast_names = {c.get("name") or "" for c in characters if c.get("name")}
                world_states = [
                    s for s in all_ws
                    if (s.get("kind") in {"character", "item", "term"}
                        and (s.get("name") or "") in cast_names)
                    or s.get("kind") in {"time", "numeric"}
                ]
            except Exception:
                world_states = []

        # 获取前一场景正文摘要（同章节内 cursor_pos 更小的最后一个场景）
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
            except Exception:
                pass

        # S4 桥接：已采纳的涌现高光（灵感池 source=emergence 且 adopted=True）+ 角色动机依据
        emergence = await self._emergence_materials(book_id) if book_id else []
        motives = await self._scene_motives(scene_id, book_id) if book_id else []

        text = await draft_prose(llm_client, scene, memory, existing,
                                 characters=characters, prev_prose=prev_prose,
                                 emergence=emergence, motives=motives,
                                 world_states=world_states)
        await self.repo.save_prose_note({
            "id": f"note-{uuid.uuid4().hex[:10]}",
            "scene_id": scene_id, "kind": "writer", "status": "approved",
            "suggestion": "写手生成正文初稿" if text else "（模型未接入，初审稿为空由前端提示）",
            "before": existing, "after": text, "created_by": "writer",
            "ts": int(time.time() * 1000),
        })
        return {"text": text}

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

    async def prose_verify(self, scene_id: str, text: str) -> dict:
        """质检员：伏笔/信念/因果对照 → verifier note（pending，明细存 payload_json，
        作者 approve 时据此落账本）。"""
        from app.services.engine.prose import verify_prose

        scene, book_id = await self._scene_and_book(scene_id)
        foreshadows = (await self.repo.list_foreshadows(book_id)) if book_id else []
        beliefs = (await self.repo.list_beliefs(book_id)) if book_id else []
        world_states = (await self.repo.list_world_states(book_id)) if book_id else []
        characters = await self.repo.list_characters_for_scene(scene_id) if scene_id else []
        opinion = await verify_prose(llm_client, text, foreshadows, beliefs, world_states,
                                     characters=characters)
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
                scene, book_id = await self._scene_and_book(note["scene_id"])
                if book_id and (opinion.get("foreshadow_updates") or opinion.get("belief_deltas")):
                    bookkeeping = await self._apply_verify_bookkeeping(
                        book_id, opinion, source_event_id="PROSE_VERIFY")
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
        return written

    async def save_scene_prose(self, scene_id: str, text: str) -> dict:
        """作者保存正文 → final_prose 幂等落库，保存后**自动触发记账 Agent**（后台任务，
        同内容重复保存 unchanged 不触发，天然幂等）。"""
        scene, _ = await self._scene_and_book(scene_id)
        text = (text or "").strip()
        if str(scene.get("final_prose") or "") == text:
            return {"scene_id": scene_id, "unchanged": True, "word_count": len(text)}
        await self.update_scene(scene_id, {"final_prose": text})
        # 后台记账：不阻塞保存返回；异常被 _bookkeep_after_save 捕获，不影响正文落库
        asyncio.create_task(self._bookkeep_after_save(scene_id, text))
        return {"scene_id": scene_id, "unchanged": False, "word_count": len(text)}

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
            "word_count": len(prose),
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
            "word_count": len(prose),
            "finalized": bool(prose),
        }

    async def get_chapter_prose(self, chapter_id: str) -> dict | None:
        """章节正文 = 该章全部场景定稿正文聚合（按 cursor_pos 排序）。"""
        ch = await self.repo.get_chapter(chapter_id)
        if ch is None:
            return None
        scenes = await self.repo.list_scenes_by_chapter(chapter_id)
        parts = [s.get("final_prose") or "" for s in scenes]
        prose = "\n\n".join(p for p in parts if p)
        return {
            "chapter_id": chapter_id,
            "book_id": ch.get("book_id", ""),
            "title": ch.get("title", ""),
            "order_no": ch.get("order_no", 0),
            "word_count": len(prose),
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
        """全书导出 markdown：书名 / 简介 / 各章（order_no）/ 已定稿场景正文。"""
        book = await self.repo.get_book(book_id)
        if book is None:
            raise SceneNotFoundError(f"未找到书: {book_id}")
        chapters = await self.repo.list_chapters_by_book(book_id)

        lines: list[str] = [f"# {book.get('title', '无题之书')}", ""]
        if book.get("synopsis"):
            lines += [f"> {book['synopsis']}", ""]
        has_prose = False
        for ch in sorted(chapters, key=lambda c: c.get("order_no") or 0):
            scenes = await self.repo.list_scenes_by_chapter(ch["id"])
            finalized = [s for s in scenes if s.get("final_prose")]
            if not finalized:
                continue  # 未定稿章节不入书稿
            has_prose = True
            lines += [f"## {ch.get('title', '')}", ""]
            for s in finalized:
                lines += [s["final_prose"], ""]
        if not has_prose:
            raise InvalidActionError("本书尚无已定稿内容，先在导演台定稿再导出")
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
        characters: dict[str, models.CharacterCard] = {}
        for row in char_rows:
            try:
                card = models.CharacterCard.model_validate_json(row["spec_json"])
            except Exception:  # noqa: BLE001
                continue
            if not card.id or card.id in characters:  # 防御：spec 未回填 id 的坏卡不装配
                continue
            characters[card.id] = card
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
        # 空世界标记（characters 与 facts 皆空）：前端据此显示"配置上场角色"引导
        if not sim.characters and not sim.world.facts:
            sim.scratch["empty_world"] = True
        await self._inject_world_rules(sim, book_id)

        sid = f"sim-{uuid.uuid4().hex[:8]}"
        await self.repo.save(sid, sim)
        return {"sim_id": sid, "resumed": False}

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

    # ---------------------------------------------------------------- step 回合
    async def step(self, sim_id: str, n: int = 1) -> models.SimulationState:
        sim = await self._load_sim(sim_id)
        spec = scenarios.load_scenario(sim.scenario)  # 按 sim 的场景，而非全局默认
        static_cfg = spec["plan_cfg"] if isinstance(spec["plan_cfg"], PlanCfg) else PlanCfg()
        # 导演 PlanCfg：sim 快照里的数据库配置优先（复用 sim.world.facts 同源），
        # 工程内 PlanCfg 不随快照持久化 → 每次按场景默认加载（DB 配置已融入 seed/装配）
        plan_cfg = _scene_plan_cfg(static_cfg=static_cfg, plan_cfg_json="{}")
        plan_cfg.ending_options = list(sim.director.ending_options)  # 快照里的是权威
        graph = self._graph(plan_cfg, spec["decide_fn"], spec["converge_fn"])
        for _ in range(n):
            # 终止：真正完结（收束/超上限）才停；举手(pending)是暂停，accept 后可继续
            if sim.ended or sim.director.converged:
                break
            prev_events = len(sim.events)
            state = await graph.ainvoke({"sim": sim, "last_main_actor": sim.last_main_actor or None})
            # 主戏角色写回 sim（跨回合轮换记忆持久化）
            if state.get("last_main_actor"):
                sim.last_main_actor = state["last_main_actor"]
            # 回合归档：回退/查看任意回合
            self._archive_turn(sim, prev_events)
            await self.repo.save(sim_id, sim)
            await self._sync_beliefs(sim)  # 信念账本草稿 → 书级 Belief 表（幂等）
            # 单回合内举手 → 暂停等作者拍板（外层 SSE 循环会因 pending 停止）
            if sim.director.raise_request.pending:
                break
        return sim

    async def stream_step(self, sim_id: str) -> AsyncIterator[dict]:
        """事件级流式 step：用 graph.astream(stream_mode='custom') 驱动单回合图，
        节点内每完成一步即 writer 推事件，本生成器逐事件 yield（即到即出），
        回合结束落库。供 SSE 消费；客户端断连以 CancelledError 终止。
        """
        sim = await self._load_sim(sim_id)
        spec = scenarios.load_scenario(sim.scenario)
        static_cfg = spec["plan_cfg"] if isinstance(spec["plan_cfg"], PlanCfg) else PlanCfg()
        plan_cfg = _scene_plan_cfg(static_cfg=static_cfg, plan_cfg_json="{}")
        plan_cfg.ending_options = list(sim.director.ending_options)
        graph = self._graph(plan_cfg, spec["decide_fn"], spec["converge_fn"])

        if sim.ended or sim.director.converged:
            return
        prev_events = len(sim.events)
        async for event in graph.astream(
            {"sim": sim, "last_main_actor": sim.last_main_actor or None},
            stream_mode="custom",
        ):
            if isinstance(event, dict) and "kind" in event:
                yield event
        # 回合结束：主戏角色写回 + 归档 + 落库
        self._archive_turn(sim, prev_events)
        await self.repo.save(sim_id, sim)
        await self._sync_beliefs(sim)  # 信念账本草稿 → 书级 Belief 表（幂等）

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
    def _archive_turn(sim: models.SimulationState, prev_events: int) -> None:
        """把刚结束的回合归档进 sim.turn_archives（供时间线回退查看）。"""
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
        """每场景最新 sim 的回合归档：{scene_id: [archive dict]}（S3/S4 共用）。"""
        latest: dict[str, str] = {}
        for s in await self.repo.list_sims_by_book(book_id):
            sid = str(s.get("scene_id") or "")
            if sid and sid not in latest:
                latest[sid] = str(s.get("id"))
        out: dict[str, list[dict]] = {}
        for scene_id, sim_id in latest.items():
            sim = await self.repo.load(sim_id)
            if sim is not None:
                out[scene_id] = [a.model_dump() for a in (sim.turn_archives or [])]
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

        await self.repo.save(sim_id, sim)
        return sim

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