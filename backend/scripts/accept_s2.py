"""S2 验收脚本（角色口吻一致性：注入 + 体检回环 + 质检并增 + 0-token 先验 + 回归）。

用法：
    python scripts/accept_s2.py          # 确定性验收（不调 LLM/不依赖 DB，约 3-5s）
    python scripts/accept_s2.py --live   # 追加真 LLM「多角色目视对比」（需 .env 的 key + DB 场景）

对应《写作优化方案-S2角色口吻.md》§5 验收标准五条 + §3.5 的 0-token 先验（step5）。
退出码：全部通过 0，任一失败 1（可直接接 CI）。
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.services.engine.prose import (  # noqa: E402
    _validate_verify,
    draft_prose,
    review_prose,
    verify_prose,
)
from app.services.engine.style_checks import voice_prior  # noqa: E402

RESULTS: list[tuple[str, bool, str]] = []


def record(name: str, ok: bool, note: str = "") -> None:
    RESULTS.append((name, ok, note))
    print("[%s] %s%s" % ("PASS" if ok else "FAIL", name, (" — " + note) if note else ""))


class _CaptureLLM:
    """假 LLM：记录 prompt，返回预设结构化产出（确定性验收用）。"""

    def __init__(self, response=None):
        self.available = True
        self.calls: list[str] = []
        self.response = response

    async def call_cheap(self, prompt, json_schema=None):
        self.calls.append(prompt)
        return self.response

    async def call_strong(self, prompt, json_schema=None):
        return await self.call_cheap(prompt, json_schema)


class _UnavailableLLM:
    """未接入 LLM（验证确定性回退契约）。"""

    available = False

    async def call_cheap(self, prompt, json_schema=None):  # pragma: no cover
        return None


async def check1_injection() -> None:
    """§5-1 注入：卡内真实字段逐字进 prompt；空卡明确标注。"""
    spec = {"name": "林尘", "summary": "落魄剑修", "traits": ["隐忍"], "voice": "话少，短句",
            "core_beliefs": ["剑不欺人"], "bottom_lines": ["不伤妇孺"]}
    draft_text = "破庙夜谈，灯花落了一地，他把刀横在膝上。" * 2   # 满足 DRAFT_VALIDATOR（≥20 字）
    llm = _CaptureLLM(draft_text)
    await draft_prose(llm, {"title": "破庙夜谈"}, "", characters=[
        {"name": "林尘", "spec_json": json.dumps(spec, ensure_ascii=False)}])
    p = llm.calls[0]
    ok = all(s in p for s in ("落魄剑修", "隐忍", "话少，短句", "不伤妇孺"))
    record("① 注入：角色卡真实字段逐字进写手 prompt", ok and "（无详细设定）" not in p)

    llm2 = _CaptureLLM(draft_text)
    await draft_prose(llm2, {"title": "x"}, "", characters=[{"name": "路人", "spec_json": "{}"}])
    record("① 注入：空卡明确标注「未填角色卡」", "未填角色卡" in llm2.calls[0])


async def check2_review() -> None:
    """§5-2/3 体检回环 + 无证据不成立。"""
    text = "“夹层里。”陈默说。李文却笑了笑：“我帮你查了这么久，你才肯拿出来？”"
    report = {"issues": [], "voice_findings": [
        {"char": "陈默", "evidence": "“夹层里。”陈默说。", "issue": "话太少", "suggestion": "补一句"},
        {"char": "陈默", "evidence": "（正文里没有这句）", "issue": "幻觉", "suggestion": "x"},
        {"char": "张三", "evidence": "“夹层里。”陈默说。", "issue": "伪角色", "suggestion": "x"},
        {"char": "李文", "evidence": "", "issue": "无证据", "suggestion": "x"},
    ], "overall": "尚可"}
    llm = _CaptureLLM(report)
    chars = [{"name": "陈默", "spec_json": json.dumps(
        {"name": "陈默", "voice": "话不多，句句见血"}, ensure_ascii=False)}]
    out = await review_prose(llm, {"title": "书房夜谈"}, text, characters=chars)
    kept = [v["char"] for v in out["voice_findings"]]
    record("② 体检回环：角色卡进体检 prompt 且保留可核对条目",
           "话不多，句句见血" in llm.calls[0] and kept == ["陈默"], "kept=%s" % kept)
    record("③ 无证据不成立：缺原句/伪角色/空证据条目一律丢弃", len(kept) == 1)


async def check4_verify() -> None:
    """§5-4 质检并增：6 键回退契约 + 缺键点名 + 逐字闸门 + 并增到 risks。"""
    out = await verify_prose(_UnavailableLLM(), "正文内容足够长的一句话。", [], [], [])
    keys = {"foreshadow_updates", "belief_deltas", "causal", "risks", "voice_risks", "state_deltas"}
    record("④ 质检并增：回退值含全部 6 键（含 voice_risks）", keys <= set(out), "keys=%d" % len(out))

    err = _validate_verify({"foreshadow_updates": []}) or ""
    record("④ 质检并增：缺键反馈逐一点名缺失字段",
           all(k in err for k in ("belief_deltas", "risks", "state_deltas")), err[:48])

    opinion = {"foreshadow_updates": [], "belief_deltas": [], "causal": [], "risks": [],
               "voice_risks": [
                   {"char": "陈默", "evidence": "“夹层里。”陈默说。", "risk": "过于健谈", "suggestion": "删"},
                   {"char": "陈默", "evidence": "（不存在这句）", "risk": "幻觉", "suggestion": "x"}],
               "state_deltas": []}
    out2 = await verify_prose(_CaptureLLM(opinion), "“夹层里。”陈默说。", [], [], [],
                              characters=[{"name": "陈默"}])
    mirrored = any(str(r).startswith("口吻：") for r in out2["risks"])
    record("④ 质检并增：voice_risks 逐字闸门 + 并增一行到 risks",
           [v["char"] for v in out2["voice_risks"]] == ["陈默"] and mirrored)


async def check5_prior() -> None:
    """step5：0-token 先验分账正确、标红生效、且注入体检 prompt。"""
    text = "陈默：「嗯。」「好。」「行。」李文：「我帮你查了这么久，你才肯拿出来？」"
    cast = [{"name": "陈默"}, {"name": "李文"}]
    prior = voice_prior(text, cast)
    rows = {m["name"]: m for m in prior["per_char"]}
    kinds = {f["kind"] for f in prior["flags"]}
    record("⑥ 先验：短句角色被标红、句长分账正确",
           "avg_len" in kinds and rows["陈默"]["dialogues"] == 3
           and rows["陈默"]["avg_len"] < rows["李文"]["avg_len"],
           "陈默 %.1f vs 李文 %.1f 字/句" % (rows["陈默"]["avg_len"], rows["李文"]["avg_len"]))

    llm = _CaptureLLM({"issues": [], "voice_findings": [], "overall": "ok"})
    await review_prose(llm, {"title": "x"}, text, characters=cast, voice_prior=prior)
    record("⑥ 先验：作为「确定性事实」注入体检 prompt",
           "0-token 口吻先验" in llm.calls[0] and "字/句" in llm.calls[0])


def check6_regression() -> None:
    """§5-5 回归：全量 pytest（含 S1 state_deltas 记账与幂等保存用例）。"""
    r = subprocess.run([sys.executable, "-m", "pytest", "-q", "--no-header"], cwd=str(ROOT),
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace", timeout=900)
    tail = (r.stdout or "").strip().splitlines()[-1] if r.stdout else ""
    record("⑤ 回归：全量 pytest 全绿", r.returncode == 0, tail)


async def live_compare() -> None:
    """--live：真 LLM 多角色目视对比（初稿声线分账 + 体检/质检结论）。"""
    from app.db.repo import Repo
    from app.services.llm.client import client as llm_client
    from app.services.service import SimulationService

    print("\n== --live 真 LLM 多角色目视对比 ==")
    if not llm_client.available:
        record("⑦ --live：真 LLM 端到端", False, "LLM 不可用（未配 NOVEL_OPENAI_API_KEY）")
        return
    scene_id = os.environ.get("S2_LIVE_SCENE", "scene-betrayal-night")
    svc = SimulationService(Repo())
    scene, _ = await svc._scene_and_book(scene_id)
    chars = await svc.repo.list_characters_for_scene(scene_id)
    cast = [{"name": c.get("name")} for c in chars]
    print("场景：%s（%s）| 角色：%s" % (scene_id, scene.get("title"), "、".join(str(n.get("name")) for n in chars)))
    draft = await svc.prose_draft(scene_id)
    text = str(draft.get("text") or "")
    print("初稿 %d 字：%s" % (len(text), text[:200].replace("\n", " ")))
    prior = voice_prior(text, cast)
    print("\n-- 声线分账（目视对比：能否对号入座）--")
    for m in prior["per_char"]:
        print("   %s：%d 句，均 %.1f 字/句，语气词「%s」，填充词 %.1f/百字"
              % (m["name"], m["dialogues"], m["avg_len"],
                 "".join(m["particles"]) or "无", m["filler_per_100"]))
    for f in prior["flags"]:
        print("   [先验] " + str(f["detail"]))
    res = await svc.prose_review(scene_id, text)
    vf = res["report"].get("voice_findings") or []
    print("\n-- 体检 voice_findings：%d 条 --" % len(vf))
    for v in vf:
        print("   %s：「%s」→ %s" % (v.get("char"), str(v.get("evidence"))[:34], str(v.get("issue"))[:44]))
    op = (await svc.prose_verify(scene_id, text))["opinion"]
    vr = op.get("voice_risks") or []
    print("-- 质检 voice_risks：%d 条 --" % len(vr))
    for v in vr:
        print("   %s：「%s」→ %s" % (v.get("char"), str(v.get("evidence"))[:34], str(v.get("risk"))[:44]))
    notes = await svc.list_prose_notes(scene_id)
    payloads = [json.loads(n.get("payload_json") or "{}") for n in notes]
    has_findings = any("voice_findings" in p for p in payloads)
    has_risks = any("voice_risks" in p for p in payloads)
    has_prior = any("voice_prior" in p for p in payloads)
    record("⑦ --live：真 LLM 端到端，体检/质检/先验三类明细均落 payload",
           bool(text) and has_findings and has_risks and has_prior,
           "体检 %d 条 / 质检 %d 条 / payload 覆盖 findings=%s risks=%s prior=%s"
           % (len(vf), len(vr), has_findings, has_risks, has_prior))


async def run(with_live: bool) -> int:
    await check1_injection()
    await check2_review()
    await check4_verify()
    await check5_prior()
    check6_regression()
    if with_live:
        try:
            await live_compare()
        except Exception as e:  # noqa: BLE001
            record("⑦ --live：真 LLM 端到端", False, "%s: %s" % (type(e).__name__, e))
    failed = [r for r in RESULTS if not r[1]]
    print("\n==== S2 验收汇总：%d/%d 通过 ====" % (len(RESULTS) - len(failed), len(RESULTS)))
    for name, ok, note in RESULTS:
        print(("  [OK] " if ok else "  [NG] ") + name + ((" — " + note) if note else ""))
    return 1 if failed else 0


class _Tee:
    """把 stdout 同时写控制台与归档文件（控制台编码不一，归档件必须 UTF-8）。"""

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
    ap = argparse.ArgumentParser(description="S2 口吻一致性验收")
    ap.add_argument("--live", action="store_true",
                    help="追加真 LLM 多角色目视对比（需 key + DB 场景）")
    ap.add_argument("--report", default="",
                    help="把完整验收输出另存为 UTF-8 文件（归档用）")
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
