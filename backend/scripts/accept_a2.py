"""A2 验收脚本（质量回环：评分 / 门控 / 三闸复检 / 路由 / 上限一次）。

用法：
    python scripts/accept_a2.py          # 确定性验收（不依赖 DB/后端/LLM，约 4s）
    python scripts/accept_a2.py --live   # 追加真实后端：对真实文本跑一次质量回环

对应《写作优化方案-A2质量回环.md》§5 验收 8 条；退出码 0=全通过，可接 CI。
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

from app.services.engine.quality import (  # noqa: E402
    RISE_MARGIN, _validate_quality, combine_scores, hard_signal_score,
    judge_improvement, quality_loop, sanitize_weak_points, should_rewrite,
    whole_fact_gate,
)

RESULTS: list = []
DIMS = {"coherence": 85, "character": 85, "pacing": 82, "imagery": 88, "ending": 84}
CLEAN = ("雨敲着窗格，烛火矮了一截。陈默把残页按在桌面，指节泛白。李文端起茶盏，没有喝。")


def record(name: str, ok: bool, note: str = "") -> None:
    RESULTS.append((name, ok, note))
    print("[%s] %s%s" % ("PASS" if ok else "FAIL", name, (" — " + note) if note else ""))


def _rep(total, dims=None, weak=None):
    return {"scores": dict(dims or DIMS), "total": total, "weak_points": weak or []}


class _SeqLLM:
    def __init__(self, responses, available=True):
        self.available = available
        self.responses = list(responses)
        self.calls: list[str] = []

    async def call_cheap(self, prompt, json_schema=None):
        self.calls.append(prompt)
        return self.responses.pop(0) if self.responses else None


def check_scoring():
    """§5-1/5：校验 + 证据闸门 + 硬信号参与合分。"""
    ok = (_validate_quality(_rep(80)) is None and _validate_quality({"scores": DIMS}) is not None)
    record("① 评分校验：必须有 total 与 scores", ok)

    text = "他把残页推过去。李文没有接。"
    rep = {"weak_points": [{"dim": "pacing", "evidence": "李文没有接。", "why": "太顺"},
                           {"dim": "imagery", "evidence": "正文里没有这句", "why": "幻觉"}]}
    kept = sanitize_weak_points(rep, text)["weak_points"]
    record("① 弱项证据闸门：无逐字原句的丢弃（防空评骗重写）",
           [w["dim"] for w in kept] == ["pacing"])

    clean, cliche = hard_signal_score(CLEAN), hard_signal_score("他深吸一口气，眼中闪过一丝犹豫，缓缓开口。")
    record("⑤ 硬信号参与合分：AI 味文本显著低于干净文本",
           clean["parts"]["tone"] == 100 and cliche["parts"]["tone"] < 100
           and combine_scores(100, clean) > combine_scores(100, cliche))


def check_gates():
    """§5-2/3：触发条件（实测校准）+ 复评门槛 + 全文事实闸门。"""
    record("② 触发：总分 <78 或任一维度 <70（达标不重写）",
           should_rewrite(70, DIMS)[0] and should_rewrite(90, dict(DIMS, pacing=62))[0]
           and not should_rewrite(85, DIMS)[0])

    before = _rep(70)
    ok1 = judge_improvement(before, _rep(70 + RISE_MARGIN))[0]
    ok2, why2 = judge_improvement(before, _rep(72))
    ok3, why3 = judge_improvement(before, _rep(90, dict(DIMS, pacing=60)))
    record("③ 复评门槛：上升 ≥%d 才采纳，且维度降 >8 不采纳" % RISE_MARGIN,
           ok1 and not ok2 and "未上升够" in why2 and not ok3 and "维度下降过大" in why3)

    prot = {"names": {"陈默"}, "numbers": set(), "terms": set(), "quotes": {"夹层里。"}}
    record("③ 全文事实闸门：专名/台词被改则拦",
           whole_fact_gate("陈默说：「夹层里。」", "陈默低声道：「夹层里。」", prot) is None
           and "专名" in (whole_fact_gate("陈默说。", "林凡说。", prot) or "")
           and "台词" in (whole_fact_gate("他说：「夹层里。」", "他说：「懂了。」", prot) or ""))


async def check_loop():
    """§5-2/3/4/7：达标不重写、全文重写采纳、未涨回退、LLM 不可用。"""
    l1 = _SeqLLM([_rep(88)])
    o1 = await quality_loop(l1, CLEAN)
    record("② 达标 → 不重写（只 1 次调用，不花冤枉钱）",
           o1["accepted"] is False and o1["skipped"] == "score-ok" and len(l1.calls) == 1)

    rewritten = "雨敲着窗格，烛火矮了一截。陈默把残页按在桌面，指节泛白，像按着一块冰。李文端起茶盏，终究没有喝。"
    l2 = _SeqLLM([_rep(60), {"text": rewritten}, _rep(80)])
    o2 = await quality_loop(l2, CLEAN)
    record("④ 低分 → 全文重写一次 → 复评上升够 → 采纳",
           o2["accepted"] and o2["route"] == "full" and o2["rewrites"] == 1 and o2["after"] == rewritten)

    short_rewrite = "雨敲着窗格，烛火矮了一截。陈默把残页按在桌面，指节泛白。"   # ≥20 字（过校验）
    l3 = _SeqLLM([_rep(60), {"text": short_rewrite}, _rep(62)])
    o3 = await quality_loop(l3, CLEAN)
    record("④ 复评没涨够 → 回退初稿（不采纳噪声）",
           o3["accepted"] is False and o3["reverted"] and o3["after"] == CLEAN)

    o4 = await quality_loop(_SeqLLM([], available=False), CLEAN)
    record("⑦ LLM 不可用 → 明确失败且不改文", o4["accepted"] is False and o4["after"] == CLEAN and o4["error"])


def check_regression():
    r = subprocess.run([sys.executable, "-m", "pytest", "-q", "--no-header"], cwd=str(ROOT),
                       capture_output=True, text=True, encoding="utf-8", errors="replace",
                       timeout=900)
    tail = (r.stdout or "").strip().splitlines()[-1] if r.stdout else ""
    record("⑧ 回归：全量 pytest 全绿", r.returncode == 0, tail)


def live() -> None:
    """--live：真实后端跑一次质量回环（真 LLM 评分）。"""
    print("\n== --live 真实后端核对 ==")
    try:
        import httpx

        base = os.environ.get("A2_API_BASE", "http://127.0.0.1:8000")
        scene = os.environ.get("A2_LIVE_SCENE", "scene-betrayal-night")
        with httpx.Client(timeout=300) as cli:
            r = cli.post("%s/api/v1/scenes/%s/prose/quality-loop" % (base, scene),
                         json={"text": CLEAN})
            j = r.json()
            b = j.get("before") or {}
            print("   HTTP %s | accepted=%s | skipped=%s | 初评 total=%s dims=%s"
                  % (r.status_code, j.get("accepted"), j.get("skipped"), b.get("total"), b.get("scores")))
            record("⑥ 真实评分可用（HTTP 200 且给出 total/维度）",
                   r.status_code == 200 and isinstance(b.get("total"), int) and bool(b.get("scores")))
            record("⑥ 真实文本达标不误触发（skipped=score-ok 或已采纳）",
                   j.get("skipped") == "score-ok" or j.get("accepted") is True)
    except Exception as e:  # noqa: BLE001
        record("⑥ --live：后端不可达或调用失败", False, str(e)[:120])


async def run(with_live: bool) -> int:
    check_scoring()
    check_gates()
    await check_loop()
    check_regression()
    if with_live:
        live()
    failed = [r for r in RESULTS if not r[1]]
    print("\n==== A2 验收汇总：%d/%d 通过 ====" % (len(RESULTS) - len(failed), len(RESULTS)))
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
    ap = argparse.ArgumentParser(description="A2 质量回环验收")
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
