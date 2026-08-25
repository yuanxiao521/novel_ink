"""信任/背叛微场景（MVP M3 基线）：陈默书房 · 夜 · 雨。

- 3 角色：陈默(主)、李文(主)、周婶(管家)
- 初始事实 + 导演 PlanCfg（隐藏的 secret fact：保险柜文件 + 周婶的泪）
- decide_fn（确定性涌现脚本，映射感知上下文 → 行动）
- converge_fn（显式结局判定：背叛暴露/隐瞒成功/关系修复/公开决裂）

无 LLM Key 时全部确定性 → 可三次运行对比。接 LLM 时替换 decide_fn/converge_fn 即可。
"""
from __future__ import annotations

from app.schemas import models
from app.services.engine.director import PlanCfg


def betrayal_night() -> dict:
    """返回：{characters, world, initial_facts, plan_cfg, decide_fn, converge_fn}"""
    chars = {
        "chenmo": models.CharacterCard(
            id="chenmo", name="陈默",
            summary="书房主人，敏锐冷静的收藏家，近期发现书房异样",
            traits=["敏锐", "克制", "重证据"],
            voice="话不多，句句见血",
            core_beliefs=["坚持真相", "坚持不冤枉人"],
            dynamic_goals=[
                models.Goal(id="truth", text="查明真相", weight=0.7),
                models.Goal(id="trust", text="守住对李文的情谊", weight=0.4),
            ],
            bottom_lines=["不施暴", "不承认不实指控"],
        ),
        "liwen": models.CharacterCard(
            id="liwen", name="李文",
            summary="陈默旧识，似卷入书房文件之事，强作镇定",
            traits=["要强", "细腻", "心虚"],
            voice="看似大方，总在试探",
            core_beliefs=["坚持自保", "坚持不拖累他人"],
            dynamic_goals=[
                models.Goal(id="self", text="自保", weight=0.8),
                models.Goal(id="cover", text="掩盖文件真相", weight=0.5),
            ],
            bottom_lines=["不施暴"],
        ),
        "zhoushen": models.CharacterCard(
            id="zhoushen", name="周婶",
            summary="老管家，众人皆以为杂物可忽略，实则知晓最多",
            traits=["沉默", "尽责", "念旧"],
            voice="寡言，偶尔一句点破",
            core_beliefs=["坚持守住家宅", "坚持不嚼舌根"],
            dynamic_goals=[
                models.Goal(id="home", text="守住家宅", weight=0.4),
                models.Goal(id="peace", text="让两人少生嫌隙", weight=0.3),
            ],
            bottom_lines=["不背叛旧主"],
        ),
    }

    initial_facts = [
        # 环境事实（带可见度，制造信息差）
        models.Fact(id="F-1", text="书房保险柜门半开", visible_to=["chenmo", "liwen","zhoushen"]),
        models.Fact(id="F-2", text="保险柜锁孔有新鲜划痕", visible_to=["chenmo"]),
        models.Fact(id="F-3", text="窗下列着雨", visible_to=["chenmo","liwen","zhoushen"]),
        models.Fact(id="F-4", text="一份旧文件压在书页里", visible_to=["chenmo"]),  # secret，待曝光
        models.Fact(id="F-5", text="周婶十点给书房送过茶", visible_to=["chenmo","liwen"]),
    ]

    plan_cfg = PlanCfg(
        secret_fact_ids=["F-4"],
        expose_target={"F-4": "liwen"},                 # F-4 文件 → 曝光给李文，制造冲突抓手
        goal_pressure=[
            {"char": "liwen", "goal": "self", "delta": 0.2,
             "reason": "陈默已起疑、保险柜划痕指向她", "at_tension": 55},
            {"char": "chenmo", "goal": "truth", "delta": 0.15,
             "reason": "划痕与文件印证、疑云加深", "at_tension": 60},
        ],
        env_pressure_lines=["窗外雨声骤密，一道闪电照亮书页"],
        raise_after_turn=3,
        ending_options=["背叛暴露", "隐瞒成功", "关系修复", "公开决裂"],
    )

    def decide_fn(context: str) -> dict:
        # --- 确定性涌现脚本：按关键词映射到该角色的行动 ---
        # 李文：试探 / 遮掩
        if "需要：推进冲突、避免空转" not in context:
            pass
        if "陈默" in context and "划痕" in context and "保险柜" in context:
            if "李文" in context or "你（李文）" in context:
                return {"actor": "liwen", "kind": "dialogue",
                        "text": "（试探）那文件……怎么会在书页里？",
                        "new_fact": ""}
            return {"actor": "chenmo", "kind": "dialogue",
                    "text": "（追问）锁孔被撬过，你最近动过这柜子吗？"}
        if "文件" in context and "李文" in context and "旧文件" in context:
            return {"actor": "chenmo", "kind": "action",
                    "text": "他抽出旧文件，锁线看向李文"}
        if "你（李文）" in context and "自保" in context:
            return {"actor": "liwen", "kind": "dialogue",
                    "text": "（强撑）我什么都不知道，你多疑了。",
                    "new_fact": ""}
        if "你（周婶）" in context:
            if "竟敢" in context or "反叛" in context:
                return {"actor": "zhoushen", "kind": "action",
                        "text": "他没有附和，只是微微摇头"}  # 触发护栏(不背叛)
            return {"actor": "zhoushen", "kind": "dialogue",
                    "text": "（低声）有些事，说出来伤和气。"}
        if "你（陈默）" in context and "证据" in context:
            return {"actor": "chenmo", "kind": "conflict",
                    "text": "（一拍桌）今晚这事，你我必须说清楚"}
        # 兜底
        return {"actor": "chenmo", "kind": "dialogue", "text": "雨声里，两人沉默地对峙。"}

    def converge_fn(sim: models.SimulationState) -> str:
        # 启发式结局判定：需要剧情积累（张力足够、回合够多）才收束，避免回合1秒走出结局。
        if sim.world.turn < 3 or sim.director.tension < 50:
            return ""
        liwen_found = any(b.fact_id == "F-4" and b.char_id == "liwen"
                          for b in sim.beliefs.get("liwen", []))
        conflicted = any(e.payload.get("kind") == "conflict" for e in sim.events)
        if liwen_found and conflicted:
            return "背叛暴露"
        if liwen_found:
            return "公开决裂"
        if sim.world.turn >= 6:
            return "关系修复"
        return ""

    return {
        "characters": chars,
        "initial_facts": initial_facts,
        "plan_cfg": plan_cfg,
        "decide_fn": decide_fn,
        "converge_fn": converge_fn,
    }