"""写手素材 dump（诊断工具 · v1.44）：把某个场景写手**实际会收到**的素材打印出来。

用途：模型输出不对劲时（跳步 / 立场倒置 / 不知道某个设定），先看它到底拿到了什么——
比反复猜 prompt 快得多。它走的是**真实装配路径** `service._draft_context()`，不是另写一套
（早先的临时脚本自己复制了过滤逻辑，结果量的是旧行为，白折腾一轮）。

用法（Git Bash）：
    cd backend && PYTHONPATH=. ../.venv/Scripts/python.exe ../scripts/dump-writer-materials.py <scene_id> [out.md]
"""
from __future__ import annotations

import asyncio
import sys

from app.db.repo import Repo
from app.services.engine.prose import (_characters_block, _emergence_block, _motives_block,
                                      _scene_block, _world_states_block, draft_prompt)
from app.services.service import SimulationService

NL = chr(10)
F = "~~~"


async def main() -> None:
    scene_id = sys.argv[1] if len(sys.argv) > 1 else "scene-85bc6bfe"
    out_path = sys.argv[2] if len(sys.argv) > 2 else "../logs/writer_materials.md"
    svc = SimulationService(Repo())
    ctx = await svc._draft_context(scene_id)
    scene = ctx["scene"]
    chars = ctx["characters"]
    ws = ctx["world_states"]
    dropped = ctx["dropped"]
    em = ctx["emergence"]
    mo = ctx["motives"]

    L: list[str] = []
    L.append("# 写手实际拿到的素材（真实装配路径）")
    L.append("")
    L.append("场景：" + str(scene.get("title")) + "（" + scene_id + "）· 书：" + str(ctx["book_id"]))

    L.append("")
    L.append("## [1] 场景设定" + ("  ⚠️ 有未填项" if "（未填" in _scene_block(scene) else ""))
    L.append(F)
    L.append(_scene_block(scene))
    L.append(F)

    L.append("")
    L.append("## [2] 出场角色卡（" + str(len(chars)) + " 张，已解析 spec_json）")
    L.append(F)
    L.append(_characters_block(chars))
    L.append(F)

    L.append("")
    L.append("## [3] 世界状态（按场景相关性筛选后）")
    L.append(F)
    L.append(_world_states_block(ws))
    L.append(F)
    if dropped:
        L.append("**被裁掉的 " + str(len(dropped)) + " 条**（连同原因，不静默）：")
        for d in dropped:
            L.append("- [" + str(d.get("kind")) + "] " + str(d.get("name")) + "：" + str(d.get("value"))[:40] + "  ← " + str(d.get("reason")))

    L.append("")
    L.append("## [4] 前文摘要（" + str(len(ctx["prev_prose"])) + " 字）")
    L.append(F)
    L.append(ctx["prev_prose"] or "（无前文，本场为开场）")
    L.append(F)

    L.append("")
    L.append("## [5] 书级记忆（已关闭账本段，避免与 [3] 重复）——" + str(len(ctx["memory"])) + " 字")
    L.append(F)
    L.append(ctx["memory"] or "（无）")
    L.append(F)

    L.append("")
    L.append("## [6] 书级约束——" + str(len(ctx["constraints"])) + " 字")
    L.append(F)
    L.append(ctx["constraints"] or "（无）")
    L.append(F)

    L.append("")
    L.append("## [7] 涌现素材（导演台采纳的高光）：" + str(len(em)) + " 条")
    L.append(F)
    L.append(_emergence_block(em))
    L.append(F)

    L.append("")
    L.append("## [8] 角色动机依据（推演回合 reasoning）：" + str(len(mo)) + " 条")
    L.append(F)
    L.append(_motives_block(mo))
    L.append(F)

    L.append("")
    L.append("## [9] existing_block：已落库正文 " + str(len(ctx["existing"])) + " 字")
    L.append(F)
    L.append(ctx["existing"][:300] or "（空）")
    L.append(F)

    prompt = draft_prompt(scene, ctx["memory"], ctx["existing"], characters=chars,
                          prev_prose=ctx["prev_prose"], world_states=ws,
                          emergence=em, motives=mo, constraints=ctx["constraints"])
    sizes = [("场景", len(_scene_block(scene))), ("角色卡", len(_characters_block(chars))),
             ("世界状态", len(_world_states_block(ws))), ("前文", len(ctx["prev_prose"])),
             ("记忆", len(ctx["memory"])), ("约束", len(ctx["constraints"])),
             ("涌现", len(_emergence_block(em))), ("动机", len(_motives_block(mo))),
             ("已有正文", len(ctx["existing"]))]
    L.append("")
    L.append("## [10] 体量")
    L.append("- 完整 prompt：**" + str(len(prompt)) + " 字符**（中文按 ~2 字符/token 粗估 ≈ " + str(len(prompt) // 2) + " tokens）")
    L.append("- 各块：" + "｜".join(k + " " + str(v) for k, v in sizes))
    L.append("- 空/近乎空：" + ("、".join(k for k, v in sizes if v < 20) or "无"))

    with open(out_path, "w", encoding="utf-8") as fh:
        fh.write(NL.join(L))
    print("WROTE", out_path, "lines=", len(L))


asyncio.run(main())
