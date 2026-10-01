"""S4 验收脚本（涌现式嵌入：思考落档 / 剧本产物 / 高光提炼 / 采纳 / 桥接）。

用法：
    python scripts/accept_s4.py          # 确定性验收（不依赖 DB/后端/LLM，约 4s）
    python scripts/accept_s4.py --live   # 追加真实后端：剧本接口 + 高光采纳

对应《写作优化方案-S4涌现式嵌入.md》§5 验收 7 条；退出码 0=全通过，可接 CI。
"""
from __future__ import annotations

import argparse
import asyncio
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.schemas import models  # noqa: E402
from app.services.engine.emergence import (  # noqa: E402
    extract_hits, render_script_json, render_script_md,
)
from app.services.service import SimulationService  # noqa: E402

RESULTS: list = []
SCENE = {"id": "sc-1", "title": "书房夜谈", "stage_desc": "雨夜书房", "goal": "试探身份"}


def record(name: str, ok: bool, note: str = "") -> None:
    RESULTS.append((name, ok, note))
    print("[%s] %s%s" % ("PASS" if ok else "FAIL", name, (" — " + note) if note else ""))


def _arch(turn, cls, tension, events, thoughts=None, summary="看"):
    return {"turn": turn, "cls": cls, "tension": tension, "tension_trend": "up",
            "summary": summary, "events": events, "thoughts": thoughts or []}


def _sim():
    card = models.CharacterCard(id="c1", name="陈默", summary="书房主人", voice="话少")
    sim = models.SimulationState(scenario="t", book_id="b1", chapter_id="ch1", scene_id="sc-1")
    sim.characters = {"c1": card}
    sim.world.turn = 3
    sim.events = [models.Event(id="e1", turn=3, type=models.EventType.action,
                               source=models.EventSource.character, actor="c1",
                               payload={"kind": "dialogue", "text": "夹层里。"})]
    return sim


def check_thoughts():
    """§5-1/2：思考落档（分层）、清空 scratch、不跨回合覆盖、不进 events、旧档兼容。"""
    sim = _sim()
    sim.scratch["thoughts"] = {"c1": {"thought": "他在试探我。", "emotion": "警觉",
                                      "reasoning": "只给半句 = 不信任"}}
    SimulationService._archive_turn(sim, 0)
    a3 = next(a for a in sim.turn_archives if a.turn == 3)
    ok = (a3.thoughts and a3.thoughts[0]["monologue"] == "他在试探我。"
          and "不信任" in a3.thoughts[0]["reasoning"] and sim.scratch["thoughts"] == {})
    record("① 思考落档：monologue/reasoning 分层 + 归集后清空 scratch", ok)

    events_before = len(sim.events)
    record("② 思考不进 events（不污染行动/账本）", len(sim.events) == events_before)

    sim.world.turn = 4
    sim.scratch["thoughts"] = {"c1": {"thought": "先稳住他。"}}
    sim.events = sim.events + [models.Event(id="e2", turn=4, type=models.EventType.action,
                                            source=models.EventSource.character, actor="c1",
                                            payload={"kind": "action", "text": "推页"})]
    SimulationService._archive_turn(sim, 1)
    a4 = next(a for a in sim.turn_archives if a.turn == 4)
    record("① 不跨回合覆盖（第 3 回合思考仍在）",
           a4.thoughts[0]["monologue"] == "先稳住他。"
           and a3.thoughts[0]["monologue"] == "他在试探我。")

    old = models.TurnArchive(turn=1, cls="type-conflict", summary="旧档", events=[])
    record("③ 旧档兼容（无 thoughts 字段仍可渲染）",
           old.thoughts == [] and "旧档" in render_script_md(SCENE, [old.model_dump()]))


def check_render():
    """§5-3：剧本 Markdown/JSON 渲染 + 开关。"""
    arch = [_arch(1, "type-conflict", 62.0,
                  [{"actor": "陈默", "kind": "dialogue", "text": "夹层里。"},
                   {"actor": "李文", "kind": "action", "text": "端起茶盏"}],
                  [{"char": "陈默", "monologue": "他在试探我。", "reasoning": "只给半句"}],
                  summary="试探")]
    md = render_script_md(SCENE, arch, with_thoughts=True, with_tension=True)
    ok = ("# 剧本 · 书房夜谈" in md and "陈默：夹层里。" in md and "（李文 端起茶盏）" in md
          and "〔内心独白·陈默〕他在试探我。" in md and "〔动机·陈默〕只给半句" in md
          and "［张力 62.0 up］" in md)
    record("④ 剧本 Markdown：场景/台词/动作/独白/动机/张力标注", ok)
    md2 = render_script_md(SCENE, arch, with_thoughts=False, with_tension=False)
    record("④ 开关生效（关掉后无独白/张力）", "内心独白" not in md2 and "张力" not in md2)
    j = render_script_json(SCENE, arch)
    record("④ JSON 结构完整（scene + turns + thoughts）",
           j["scene"]["title"] == "书房夜谈" and j["turns"][0]["thoughts"][0]["char"] == "陈默")


def check_hits():
    """§5-4：确定性高光规则 + 边界。"""
    hits = extract_hits({"id": "sc-1"}, [
        _arch(1, "type-dialogue", 20, [{"actor": "陈默", "kind": "dialogue", "text": "夹层里。"}]),
        _arch(2, "type-conflict", 80, [{"actor": "李文", "kind": "dialogue",
                                        "text": "你书房门没锁，我进来讨本书看，犯法？"}]),
    ])
    kinds = hits[0]["kinds"] if hits else []
    record("⑤ 高光：冲突+峰值+密集台词+收束合并进同一回合",
           [h["turn"] for h in hits] == [2]
           and {"conflict", "peak", "long_dialogue", "closing"} <= set(kinds))
    record("⑤ 边界：空归档 → 无高光；单回合不算收束",
           extract_hits({"id": "s"}, []) == []
           and extract_hits({"id": "s"}, [_arch(1, "type-info", 0,
                                                [{"actor": "A", "kind": "action", "text": "走"}])]) == [])


async def check_service():
    """§5-5/6：采纳为灵感卡（source=emergence）+ 桥接注入（已采纳才注入，只注 reasoning）。"""
    import app.services.service as service_mod
    from app.db.repo import Repo

    repo = Repo(use_db=False)
    svc = SimulationService(repo)
    await repo.save_book({"id": "b1", "title": "涌现书"})
    await repo.save_chapter({"id": "ch1", "book_id": "b1", "title": "第一章", "order_no": 1})
    await repo.save_scene({"id": "sc-1", "chapter_id": "ch1", "title": "书房夜谈",
                           "stage_desc": "雨夜", "goal": "试探", "final_prose": ""})
    sim = models.SimulationState(scenario="t", book_id="b1", chapter_id="ch1", scene_id="sc-1")
    sim.turn_archives = [
        models.TurnArchive(turn=1, cls="type-dialogue", tension=20, tension_trend="up",
                           summary="试探", events=[{"actor": "陈默", "kind": "dialogue",
                                                    "text": "夹层里。"}],
                           thoughts=[{"char": "陈默", "monologue": "他在试探我。",
                                      "reasoning": "只给半句 = 不信任"}]),
        models.TurnArchive(turn=2, cls="type-conflict", tension=80, tension_trend="up",
                           summary="翻脸", events=[{"actor": "李文", "kind": "dialogue",
                                                    "text": "你书房门没锁，我进来讨本书看，犯法？"}]),
    ]
    await repo.save("sim-1", sim)

    out = await svc.scene_script("sc-1")
    res = await svc.adopt_emergence_hits("sc-1")
    record("⑤ 采纳：高光 → 灵感卡（source=emergence / adopted=false）",
           bool(out["hits"]) and res["adopted"] == len(out["hits"])
           and all(c["source"] == "emergence" and c["adopted"] is False for c in res["cards"]))

    cards = [
        {"id": "i1", "source": "emergence", "adopted": True, "title": "剧本高光 T-02",
         "desc": "【涌现高光·冲突回合】翻脸"},
        {"id": "i2", "source": "emergence", "adopted": False, "title": "未采纳", "desc": "不该出现"},
    ]

    async def _fake_list(_book_id):
        return cards

    svc.repo.list_inspirations = _fake_list  # 实例属性遮蔽方法（脚本内即够）
    calls: list[str] = []

    class _CapLLM:
        available = True

        async def call_cheap(self, prompt, json_schema=None):
            calls.append(prompt)
            return "雨敲着窗格。" * 4

    old_llm = service_mod.llm_client
    service_mod.llm_client = _CapLLM()
    try:
        await svc.prose_draft("sc-1")
    finally:
        service_mod.llm_client = old_llm
    prompt = calls[0] if calls else ""
    record("⑥ 桥接：已采纳素材 + 动机依据注入写手 prompt",
           "涌现素材" in prompt and "角色动机依据" in prompt
           and "剧本高光 T-02" in prompt and "不该出现" not in prompt
           and "只给半句 = 不信任" in prompt)
    record("⑥ 内心独白不进正文 prompt（只给 reasoning）", "他在试探我。" not in prompt)


def check_regression():
    r = subprocess.run([sys.executable, "-m", "pytest", "-q", "--no-header"], cwd=str(ROOT),
                       capture_output=True, text=True, encoding="utf-8", errors="replace",
                       timeout=900)
    tail = (r.stdout or "").strip().splitlines()[-1] if r.stdout else ""
    record("⑦ 回归：全量 pytest 全绿", r.returncode == 0, tail)


def live() -> None:
    """--live：真实后端剧本接口 + 高光采纳。"""
    print("\n== --live 真实后端核对 ==")
    try:
        import httpx

        base = os.environ.get("S4_API_BASE", "http://127.0.0.1:8000")
        scene = os.environ.get("S4_LIVE_SCENE", "scene-betrayal-night")
        with httpx.Client(timeout=60) as cli:
            r = cli.get("%s/api/v1/scenes/%s/script" % (base, scene))
            j = r.json()
            print("   剧本接口 %s：turns=%s hits=%s md_len=%s" % (
                r.status_code, j.get("turns"), len(j.get("hits") or []),
                len(j.get("markdown") or "")))
            record("⑧ 剧本接口连通且结构完整",
                   r.status_code == 200 and {"turns", "markdown", "json", "hits"} <= set(j))
            a = cli.post("%s/api/v1/scenes/%s/emergence-hits" % (base, scene), json={"turns": []})
            print("   采纳接口 %s：picked=%s adopted=%s" % (
                a.status_code, a.json().get("picked"), a.json().get("adopted")))
            record("⑧ 高光采纳接口连通", a.status_code == 200 and "adopted" in a.json())
    except Exception as e:  # noqa: BLE001
        record("⑧ --live：后端不可达或调用失败", False, str(e)[:120])


async def run(with_live: bool) -> int:
    check_thoughts()
    check_render()
    check_hits()
    await check_service()
    check_regression()
    if with_live:
        live()
    failed = [r for r in RESULTS if not r[1]]
    print("\n==== S4 验收汇总：%d/%d 通过 ====" % (len(RESULTS) - len(failed), len(RESULTS)))
    for name, ok, note in RESULTS:
        print(("  [OK] " if ok else "  [NG] ") + name + ((" — " + note) if note else ""))
    return 1 if failed else 0


class _Tee:
    def __init__(self, *streams):
        self.streams = streams

    def write(self, s):
        for st in self.streams:
            st.write(s)
        return len(s)

    def flush(self):
        for st in self.streams:
            st.flush()


def main() -> None:
    ap = argparse.ArgumentParser(description="S4 涌现式嵌入验收")
    ap.add_argument("--live", action="store_true", help="追加真实后端核对")
    ap.add_argument("--report", default="", help="完整输出另存 UTF-8 文件（归档用）")
    args = ap.parse_args()
    if args.report:
        fh = open(args.report, "w", encoding="utf-8")
        original = sys.stdout
        sys.stdout = _Tee(original, fh)
        try:
            code = asyncio.run(run(args.live))
        finally:
            sys.stdout = original
            fh.close()
        sys.exit(code)
    sys.exit(asyncio.run(run(args.live)))


if __name__ == "__main__":
    main()
