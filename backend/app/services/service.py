"""服务层 · SimulationService：面向外部（API/脚本）的业务编排（async）。

能力：启动叙事（四层装配）/ 恢复上次 / step 回合 / 查看状态 / 作者介入 /
四层目录查询（books/chapters/scenes/characters/book-tree）。
数据经 async Repo（SQLAlchemy 2.0 ORM）走 DB；DB 未就绪时内存态兜底。
"""
from __future__ import annotations

import hashlib
import json
import uuid
from typing import AsyncIterator

from langgraph.graph.state import CompiledStateGraph

from app import scenarios
from app.config import settings
from app.db.repo import Repo
from app.errors import InvalidActionError, SceneNotFoundError, SimNotFoundError
from app.schemas import models
from app.services.engine.director import PlanCfg


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
        return await self.repo.list_characters_by_scene(scene_id)

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

    async def create_character(self, scene_id: str, data: dict) -> dict:
        cid = data.get("id") or f"char-{uuid.uuid4().hex[:8]}"
        data = {**data, "id": cid, "scene_id": scene_id}
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

        plan = await plan_skelly(direction, book.get("synopsis", ""),
                                 inspirations=inspirations or None)
        return {"book_id": book_id, "plan": plan}

    async def commit_book_plan(self, book_id: str, plan: dict) -> dict:
        """确认骨架 → 落库：worldview/world_rules + chapters/scenes + foreshadows。"""
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

        # 2) 章节 + 场景（幂等：按标题匹配已有记录，存在则跳过不重复创建）
        existing_chapters = await self.list_chapters(book_id)
        existing_chapter_map = {c.get("title"): c for c in existing_chapters}
        chapters = plan.get("chapters") or []
        scene_ids: list[str] = []
        for i, ch in enumerate(chapters):
            ch_title = ch.get("title", f"第{i + 1}章")
            if ch_title in existing_chapter_map:
                cid = existing_chapter_map[ch_title]["id"]
                # 更新已有章节的元信息（摘要/基调/字数）
                await self.update_chapter(cid, {
                    "summary": ch.get("summary", ""),
                    "order_no": i + 1,
                    "tone": ch.get("tone", ""),
                    "tension_curve": ch.get("tension_curve", ""),
                    "word_target": int(ch.get("word_target") or 0),
                })
            else:
                cid = (await self.create_chapter(book_id, {
                    "title": ch_title,
                    "summary": ch.get("summary", ""),
                    "order_no": i + 1,
                    "tone": ch.get("tone", ""),
                    "tension_curve": ch.get("tension_curve", ""),
                    "word_target": int(ch.get("word_target") or 0),
                }))["id"]

            # 收集该章已有场景标题
            existing_scenes = await self.list_scenes(cid)
            existing_scene_titles = {s.get("title") for s in existing_scenes}
            scenes = ch.get("scenes") or []
            for j, sc in enumerate(scenes):
                sc_title = sc.get("title", f"场景{j + 1}")
                if sc_title in existing_scene_titles:
                    # 场景已存在，跳过
                    continue
                r = await self.create_scene(cid, {
                    "title": sc_title,
                    "stage_desc": sc.get("stage_desc", ""),
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

    # ---------------------------------------------------------------- 主笔共创对话（P0-2）
    async def chief_chat_stream(self, book_id: str, messages: list[dict]):
        """主笔共创对话：注入书骨架上下文，逐 token 流式返回（无 LLM 时回退确定性文案）。"""
        from app.services.llm.client import client as llm_client

        tree = await self.repo.get_book_tree(book_id)
        title = (tree or {}).get("title", "（本书）")
        synopsis = ((tree or {}).get("synopsis") or "").strip()
        chapters = [(c.get("title") or "") for c in ((tree or {}).get("chapters") or [])][:6]
        chap_txt = " / ".join(chapters) if chapters else "（暂无章节）"

        last_user = ""
        for m in messages:
            if m.get("role") == "user" and m.get("content"):
                last_user = str(m["content"])
        last_user = last_user[-800:]

        prompt = (
            f"你是小说【主笔】（总编剧）。下面是当前这本书的骨架上下文：\n"
            f"书名：{title}\n一句方向：{synopsis or '（未填写）'}\n"
            f"章节骨架：{chap_txt}\n"
            f"请基于以上上下文，回答作者问题：{last_user}\n"
            f"回答用中文，简洁有干货，可给建议或反问，不要空话。"
        )

        if llm_client.available:
            async for token in llm_client.stream_cheap_text(prompt):
                yield token
        else:
            yield f"主笔已收到：{last_user}。当前模型未接入，我先按这本书的骨架给你一点参考——"
            yield "可以先从「一句话方向」或选中骨架里的某一章让我展开；需要真实创作建议时接入模型即可。"

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

        char_rows = await self.repo.list_characters_by_scene(scene_id)
        characters: dict[str, models.CharacterCard] = {}
        for row in char_rows:
            try:
                card = models.CharacterCard.model_validate_json(row["spec_json"])
            except Exception:  # noqa: BLE001
                continue
            characters[card.id] = card
        if not characters:  # 兜底：DB 无角色卡 → 静态场景装配
            characters = dict(spec["characters"])

        sim = models.SimulationState(
            scenario=scenario_def,
            book_id=book_id,
            chapter_id=chapter_id,
            scene_id=scene_id,
            world=models.WorldState(scene_id=scene_id, title=scene["title"]),
        )
        sim.characters = characters
        sim.world.facts = list(initial_facts) if initial_facts else list(spec["initial_facts"])
        sim.director.ending_options = list(plan_cfg.ending_options)
        sim.action_order = [c for c in sim.characters]
        await self._inject_world_rules(sim, book_id)

        sid = f"sim-{uuid.uuid4().hex[:8]}"
        await self.repo.init_schema()
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
        sim.turn_archives = [a for a in sim.turn_archives if a.turn != turn]
        sim.turn_archives.append(models.TurnArchive(
            turn=turn,
            tension=sim.director.tension,
            tension_trend=sim.director.tension_trend,
            cls=cls,
            summary=summary[:18] or f"回合 T-{turn:02d}",
            prose=prose,
            events=acted,
        ))

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
                self._append_director_hint(sim, text)
        elif a in {"reject", "驳回"}:
            sim.director.raise_request.pending = False
        elif a == "inject_event":
            text = payload.get("text", "")
            if text:
                self._append_director_hint(sim, text)
        elif a in {"adjust_weight", "expose"}:
            # 预留：导演工具（软引导走 plan()，intervene 仅处理举手 + 注入）
            pass
        else:
            raise InvalidActionError(f"未知介入动作: {action}")
        await self.repo.save(sim_id, sim)
        return sim

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