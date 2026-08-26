"""数据层/护栏/服务 冒烟测试。

说明：DB 未就绪时 Repo 自动降级内存态，因此这些测试不依赖真实 Postgres；
这一点正是"内存态兜底"设计要保障的（无库可验证闭环）。
"""
import sys
from pathlib import Path

# 确保 backend/ 在 path（服务层无 DB 也可实例化）
_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import pytest  # noqa: E402

from app.db.repo import Repo  # noqa: E402
from app.scenarios.betrayal_night import betrayal_night  # noqa: E402
from app.schemas import models  # noqa: E402
from app.services.engine.character import CharacterEngine  # noqa: E402
from app.services.engine.director import DirectorEngine  # noqa: E402
from app.services.engine.world import WorldEngine  # noqa: E402
from app.services.service import SimulationService  # noqa: E402


def _make_sim() -> models.SimulationState:
    spec = betrayal_night()
    sim = models.SimulationState(
        scenario="betrayal_night",
        world=models.WorldState(scene_id="betrayal_night", title="test"),
    )
    sim.characters = spec["characters"]
    sim.world.facts = list(spec["initial_facts"])
    sim.director.ending_options = list(spec["plan_cfg"].ending_options)
    sim.action_order = [c for c in sim.characters]
    return sim, spec


# ---------------------------------------------------------------- 第3层护栏（世界事实）
def test_world_guard_blocks_contradiction():
    sim, _ = _make_sim()
    world = WorldEngine(sim)
    # 现存事实：F-1 保险柜门半开（positive）
    ok, reason = world.check_fact_consistent("保险柜门没有敞开")
    assert ok is False, reason


def test_world_guard_passes_consistent():
    sim, _ = _make_sim()
    world = WorldEngine(sim)
    ok, _ = world.check_fact_consistent("窗外下着雨")
    assert ok is True


# ---------------------------------------------------------------- 4.1 角色卡 ④ 层 prompt 模板（Task 1）
def test_character_card_editable_prompt_fields():
    """④ 层可编辑模板：默认空，作者可在前端填 system_prompt/think_schema/decide_schema/static_world，可覆盖。"""
    sim, _ = _make_sim()
    card = sim.characters["chenmo"]
    # 默认空（运行时用内置默认模板）
    assert card.system_prompt == ""
    assert card.think_schema == ""
    assert card.decide_schema == ""
    assert card.static_world == ""
    # 可写：作者覆盖后参与感知→思考→决策拼装
    card.system_prompt = "你是陈默，冷静克制，话不多。"
    card.think_schema = '{"thought":"...","emotion":"...","reasoning":"..."}'
    card.decide_schema = '{"kind":"...","text":"...","new_fact":"..."}'
    card.static_world = "书房 · 雨夜"
    assert card.system_prompt == "你是陈默，冷静克制，话不多。"
    assert "thought" in card.think_schema
    assert "new_fact" in card.decide_schema
    assert card.static_world == "书房 · 雨夜"


def test_character_card_prompt_fields_backward_compatible():
    """旧快照（无 ④ 层字段）可 load；新字段序列化后 round-trip 一致。"""
    old = {"id": "x", "name": "某", "summary": "s"}
    card = models.CharacterCard(**old)                 # 旧快照 load
    assert card.system_prompt == ""
    assert card.think_schema == ""
    assert card.decide_schema == ""
    assert card.static_world == ""
    card.system_prompt = "p"
    card.static_world = "w"
    card2 = models.CharacterCard(**card.model_dump())  # round-trip
    assert card2.system_prompt == "p"
    assert card2.static_world == "w"


# ---------------------------------------------------------------- 思考层/决策层（Task 3）
@pytest.mark.asyncio
async def test_think_decide_fallback_without_key():
    """无 LLM key 时 think→None 无副作用；decide 回落 decide_fn，产物与直接调脚本一致，scratch 标注 fallback。"""
    sim, spec = _make_sim()
    char_eng = CharacterEngine(sim)
    # think：LLM 不可用 → None，不写 scratch['thoughts']
    assert await char_eng.think("chenmo") is None
    assert "thoughts" not in sim.scratch
    # decide：回落决定脚本，产物与 decide_fn(context) 完全一致
    ctx = char_eng.perceive_context("chenmo")
    expected = spec["decide_fn"](ctx)
    action = await char_eng.decide("chenmo", spec["decide_fn"])
    assert action == expected
    assert "text" in action
    assert sim.scratch["decisions"]["chenmo"] == {"fallback": True}


# ---------------------------------------------------------------- 第1层护栏（人设 OOC）
def test_persona_guard_blocks_violence():
    sim, _ = _make_sim()
    char_eng = CharacterEngine(sim)
    ok, reason = char_eng.persona_guard("chenmo", {"text": "我直接动手打他一巴掌"})
    assert ok is False
    assert "施暴" in reason


def test_persona_guard_passes_normal():
    sim, _ = _make_sim()
    char_eng = CharacterEngine(sim)
    ok, _ = char_eng.persona_guard("chenmo", {"text": "他冷静地翻开账本"})
    assert ok is True


# ---------------------------------------------------------------- 信念账本溯源
def test_belief_records_source():
    sim, _ = _make_sim()
    world = WorldEngine(sim)
    world.record_belief("liwen", "F-4", "DIR", models.BeliefChannel.perceived, "旧文件", 0.8)
    b = sim.beliefs["liwen"][0]
    assert b.source_event_id == "DIR"
    assert b.channel == models.BeliefChannel.perceived


# ---------------------------------------------------------------- 导演软引导（曝光/权重带内因）
def test_director_goal_adjust_requires_reason():
    sim, spec = _make_sim()
    world = WorldEngine(sim)
    adj = models.GoalAdjust(char_id="liwen", goal_id="self", delta=0.2, reason="陈默已起疑")
    world.apply_goal_adjust(adj)
    g = next(x for x in sim.characters["liwen"].dynamic_goals if x.id == "self")
    assert g.weight == pytest.approx(0.8 + 0.2)  # 原 0.8 -> 1.0
    assert g.last_adjust_reason == "陈默已起疑"


# ---------------------------------------------------------------- 导演 LLM 调度（Task 4）
@pytest.mark.asyncio
async def test_director_plan_equals_fallback_without_key():
    """无 key 时 plan() 完全等同 fallback_plan()：hint/injected/举手 与确定性一致。"""
    sim, spec = _make_sim()
    sim.director.tension = 60.0
    DirectorEngine(sim, spec["plan_cfg"]).fallback_plan()
    a = (sim.director.hint.model_dump(), list(sim.director.injected_events), sim.director.raise_request.pending)

    sim2, _ = _make_sim()
    sim2.director.tension = 60.0
    await DirectorEngine(sim2, spec["plan_cfg"]).plan()  # 无 key → 内部回退 fallback_plan
    b = (sim2.director.hint.model_dump(), list(sim2.director.injected_events), sim2.director.raise_request.pending)
    assert a == b


@pytest.mark.asyncio
async def test_director_llm_plan_mocked(monkeypatch):
    """mock call_cheap 返回合法 JSON 时 llm_plan() 产出软引导，并把 GoalAdjust.reason 写回。"""
    from app.services.engine.world import WorldEngine

    sim, spec = _make_sim()
    sim.world.turn = 1
    sim.director.tension = 60.0
    director = DirectorEngine(sim, spec["plan_cfg"])

    class _MockLLM:
        available = True

        async def call_cheap(self, prompt, json_schema=None):
            assert json_schema is not None  # llm_plan 走结构化输出
            return {
                "info_exposures": [{"fact_id": "F-4", "target_char_id": "liwen", "channel": "inferred"}],
                "goal_adjusts": [{"char_id": "chenmo", "goal_id": "truth", "delta": 0.15,
                                  "reason": "旧文件与划痕相互印证、疑云加深"}],
                "injected_event": "窗外雨声骤密，一道闪电照亮书页",
                "stage_prompt": "舞台提示：让陈默先接话，追问文件的来历",
                "raise_request": {"reason_kind": "", "reason": ""},
            }

    import app.services.engine.director as director_mod

    monkeypatch.setattr(director_mod, "llm_client", _MockLLM())
    assert await director.llm_plan() is True

    h = sim.director.hint
    assert h.info_exposures and h.info_exposures[0].fact_id == "F-4"
    assert h.info_exposures[0].target_char_id == "liwen"
    assert h.info_exposures[0].channel == models.BeliefChannel.inferred
    assert h.goal_adjusts and h.goal_adjusts[0].reason == "旧文件与划痕相互印证、疑云加深"
    assert any(line.startswith("事件注入：") for line in sim.director.injected_events)
    assert sim.director.raise_request.pending is False

    # 因果律：reason 写回 dynamic_goal.last_adjust_reason（graph 的 _apply_guidance 走 apply_goal_adjust）
    WorldEngine(sim).apply_goal_adjust(h.goal_adjusts[0])
    g = next(x for x in sim.characters["chenmo"].dynamic_goals if x.id == "truth")
    assert g.last_adjust_reason == "旧文件与划痕相互印证、疑云加深"


@pytest.mark.asyncio
async def test_director_llm_plan_none_falls_back(monkeypatch):
    """call_cheap 返回 None（LLM 不可用/调用失败）→ llm_plan() False，plan() 回退确定性。"""
    import app.services.engine.director as director_mod

    sim, spec = _make_sim()
    sim.director.tension = 60.0
    sim.world.turn = 0

    class _NoneLLM:
        available = True

        async def call_cheap(self, prompt, json_schema=None):
            return None

    monkeypatch.setattr(director_mod, "llm_client", _NoneLLM())
    director = DirectorEngine(sim, spec["plan_cfg"])
    assert await director.llm_plan() is False
    await director.plan()
    assert len(sim.director.hint.info_exposures) >= 1  # 已走到确定性曝光


# ---------------------------------------------------------------- 服务层闭环（内存态）
def test_guard_fuse_and_no_fact_pollution():
    """第3层护栏超上限 → 熔断降级（带瑕疵标记），且不把矛盾事实写入世界。"""
    spec = betrayal_night()
    sim = models.SimulationState(scenario="betrayal_night", world=models.WorldState(scene_id="x", title="t"))
    sim.characters = spec["characters"]
    sim.world.facts = list(spec["initial_facts"])  # 包含 F-1 保险柜门半开
    sim.director.ending_options = ["终结"]

    from app.services.engine.graph import _guard_and_record

    char_eng = CharacterEngine(sim)
    world = WorldEngine(sim)
    # 反复产出与 F-1 矛盾的 new_fact（guard_retry=0 → 一次失败即熔断）
    action = {"actor": "chenmo", "kind": "conflict", "text": "他一口咬定柜门从未动过", "new_fact": "保险柜门没有敞开"}
    ev = _guard_and_record(sim, "chenmo", action, char_eng, world, guard_retry=0)
    assert ev.guard_flags == ["fuse"]          # 熔断标记
    assert sim.guard.total_intercepts >= 1      # 拦截计数
    assert sim.guard.fuse_counts.get("guard") == 1
    # 不污染事实：active_facts 中无"柜门没有敞开"
    assert not any(f.text == "保险柜门没有敞开" for f in sim.active_facts())


# ---------------------------------------------------------------- 服务层闭环（内存态）
@pytest.mark.asyncio
async def test_service_start_and_step_in_memory():
    """无 DB（use_db=False 强制内存态）时 start/step 闭环可跑、回合递增。"""
    repo = Repo(use_db=False)
    svc = SimulationService(repo)
    # 内存态下无 scenes 表 → 直接构造 sim 并落库测试 step
    spec = betrayal_night()
    sim = models.SimulationState(
        scenario="betrayal_night",
        world=models.WorldState(scene_id="x", title="t"),
    )
    sim.characters = spec["characters"]
    sim.world.facts = list(spec["initial_facts"])
    sim.director.ending_options = list(spec["plan_cfg"].ending_options)
    sim.action_order = [c for c in sim.characters]
    await repo.save("sim-mem-test", sim)

    sim = await svc.step("sim-mem-test", n=1)
    assert sim.world.turn == 1
    assert len(sim.events) >= 1
    sim2 = await svc.step("sim-mem-test", n=1)
    assert sim2.world.turn == 2


# ---------------------------------------------------------------- API 依赖注入连通
def test_get_service_injects_repo():
    """依赖注入链可实例化（repo 默认参数、use_db 由 settings 决定）。"""
    from app.api.deps import get_service
    from app.db.repo import Repo

    repo = Repo(use_db=False)
    svc = get_service(repo=repo)
    assert svc.repo is repo


def test_all_layers_import():
    import app.main
    import app.api.routers.simulation
    assert hasattr(app.main, "app")