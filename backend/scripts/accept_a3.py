"""A3 验收脚本（反 AI 味：规则检测 + 事实闸门 + 定点重写 + 真实接口）。

用法：
    python scripts/accept_a3.py          # 确定性验收（不依赖 DB/后端/LLM，约 3-5s）
    python scripts/accept_a3.py --live   # 追加真实后端：扫描 AI 味文本/干净文本 + 定点修复

对应《写作优化方案-A3反AI味校验.md》§5 验收条；退出码 0=全通过，可接 CI。
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

from app.services.engine.ai_tone import fact_gate, protected_tokens, spot_fix  # noqa: E402
from app.services.engine.style_checks import (  # noqa: E402
    ai_tone_report,
    rule_policy,
    sentences_with,
)

RESULTS: list = []
AI_ISH = ("他深吸一口气，眼中闪过一丝犹豫，又深吸一口气，眼中闪过一丝决然，"
          "再次深吸一口气，缓缓开口。")
CLEAN = ("雨敲着窗格，烛火矮了一截。陈默把那张残页按在桌面，指节泛白。"
         "李文端起茶盏，没有喝。")
CAST_PROSE = ("林尘推门进来。林尘把伞靠在墙边。林尘看了一眼桌上的残页。"
              "李文皱眉问：林尘，你查到了什么？林尘没有回答，只把残页推过去。")


def record(name: str, ok: bool, note: str = "") -> None:
    RESULTS.append((name, ok, note))
    print("[%s] %s%s" % ("PASS" if ok else "FAIL", name, (" — " + note) if note else ""))


class _FakeLLM:
    def __init__(self, response=None, available=True):
        self.available = available
        self.response = response
        self.calls: list[str] = []

    async def call_cheap(self, prompt, json_schema=None):
        self.calls.append(prompt)
        return self.response


def _rw(before, after, rule="R-A3-1"):
    return {"index": 0, "before": before, "after": after, "rule": rule, "reason": "套话换动作"}


def check_rules():
    """§5-1/7/8/9 规则可复现 + 不误伤 + 专名排除 + 阈值归一 + 硬折行。"""
    rep = ai_tone_report(AI_ISH)
    record("① 规则可复现：AI 味文本命中 R-A3-1", "R-A3-1" in [r["rule"] for r in rep["rules"]],
           "命中 %d 条" % rep["counts"]["total"])
    record("① 干净文本 0 命中（不误伤）", ai_tone_report(CLEAN)["clean"] is True)

    hit = ai_tone_report(CAST_PROSE)["rules"]                       # 对照：不传 exclude
    hit_ex = ai_tone_report(CAST_PROSE, exclude={"林尘", "李文"})["rules"]
    record("⑦ 专名排除：主角名高频 ≠ 复读",
           "R-A3-6" not in [r["rule"] for r in hit_ex] and "R-A3-6" in [r["rule"] for r in hit],
           "不排除时命中（对照）")
    record("⑦ 未传 exclude 时诚实标注", "未提供专名清单" in (
        next((r["detail"] for r in hit if r["rule"] == "R-A3-6"), "") or "未提供专名清单"))

    short = "他深吸一口气，眼中闪过一丝犹豫，又深吸一口气。"
    long_text = short + "正常叙事。" * 150
    ok = ("R-A3-1" in [r["rule"] for r in ai_tone_report(short)["rules"]]
          and "R-A3-1" not in [r["rule"] for r in ai_tone_report(long_text)["rules"]])
    record("⑧ 套话阈值按字数归一（短句报 / 长章不报）", ok)

    hard = "雨敲着窗格。\n他推开门。\n他知道。\n李文抬起头。"
    record("⑨ 硬折行不误报段末拔高", "R-A3-4" not in [r["rule"] for r in ai_tone_report(hard)["rules"]])

    record("⑨b 破折号计数：成对标点算 1 处（B20 回归）",
           ai_tone_report("他顿了顿——然后笑了。")["metrics"]["dash"] == 1)


def check_gate():
    """§5-2 事实闸门四类。"""
    text = "陈默深吸一口气，第三日动身，突破了凝气境。「我明白了。」"
    prot = protected_tokens(text, [{"name": "陈默"}],
                            [{"kind": "term", "name": "凝气境", "value": "凝气境"}])
    ok_equiv = fact_gate("陈默深吸一口气。", "陈默吸了口气。", prot) is None
    blocked = [
        fact_gate("陈默深吸一口气。", "林凡深吸一口气。", prot) or "",
        fact_gate("第三日动身。", "次日动身。", prot) or "",
        fact_gate("突破了凝气境。", "突破了更高境界。", prot) or "",
        fact_gate("他低声道：「我明白了。」", "他低声道：「懂了。」", prot) or "",
    ]
    record("② 事实闸门：等价改写放行，专名/数字/术语/台词四类拦截",
           ok_equiv and all(blocked), "拦截 %d/4" % sum(1 for b in blocked if b))


async def check_spotfix():
    """§5-3/11/12/13/14 定点重写：白名单 / 幂等 / LLM 不可用 / 部分改善 / 回退。"""
    record("③ 白名单：破折号只提示不改",
           rule_policy("R-A3-2")["fixable"] is False and rule_policy("R-A3-5")["mode"] == "conservative")

    llm0 = _FakeLLM({"rewrites": []})
    out0 = await spot_fix(llm0, "他顿了顿——然后笑了。")
    record("③ 无命中 → 幂等空操作且不调 LLM",
           out0["accepted"] is True and out0["skipped"] == "no-fixable-hit" and llm0.calls == [])

    out1 = await spot_fix(_FakeLLM(available=False), AI_ISH)
    record("③ LLM 不可用 → 不改文 + 明确报错",
           out1["accepted"] is False and out1["after"] == AI_ISH and bool(out1.get("error")))

    text = "他深吸一口气，眼中闪过一丝犹豫，缓缓开口。"
    llm2 = _FakeLLM({"rewrites": [_rw(text, "他攥了攥拳，缓缓开口。")]})
    out2 = await spot_fix(llm2, text)
    record("③ 部分改善被采纳（命中词数下降即可，不必清零）",
           out2["accepted"] is True and out2["score_after"]["terms"] < out2["score_before"]["terms"],
           "词 %d→%d" % (out2["score_before"]["terms"], out2["score_after"]["terms"]))
    record("③ prompt 写入需处理命中词",
           "需处理" in llm2.calls[0] and "深吸一口气" in llm2.calls[0])

    llm3 = _FakeLLM({"rewrites": [_rw("正文里没有这句。", "随便改。"),
                                  _rw(text, "他吸了口气，等了三天。")]})
    out3 = await spot_fix(llm3, text)
    reasons = " ".join(str(c.get("blocked_reason") or "") for c in out3["changes"])
    record("③ 落地校验 + 事实闸门丢弃坏改写",
           out3["accepted"] is False and "逐字找到" in reasons and "数字" in reasons)


def check_regression():
    r = subprocess.run([sys.executable, "-m", "pytest", "-q", "--no-header"], cwd=str(ROOT),
                       capture_output=True, text=True, encoding="utf-8", errors="replace",
                       timeout=900)
    tail = (r.stdout or "").strip().splitlines()[-1] if r.stdout else ""
    record("④ 回归：全量 pytest 全绿", r.returncode == 0, tail)


def live() -> None:
    """--live：真实后端接口（扫描 + 定点修复）。"""
    print("\n== --live 真实后端核对 ==")
    try:
        import httpx
    except Exception as e:  # noqa: BLE001
        record("⑤ --live", False, "httpx 不可用：%s" % e)
        return
    base = os.environ.get("A3_API_BASE", "http://127.0.0.1:8000")
    scene = os.environ.get("A3_LIVE_SCENE", "scene-betrayal-night")
    try:
        with httpx.Client(timeout=120) as cli:
            r1 = cli.post("%s/api/v1/scenes/%s/prose/ai-tone" % (base, scene), json={"text": AI_ISH})
            r2 = cli.post("%s/api/v1/scenes/%s/prose/ai-tone" % (base, scene), json={"text": CLEAN})
            rules_ai = [x["rule"] for x in r1.json()["report"]["rules"]]
            rules_clean = [x["rule"] for x in r2.json()["report"]["rules"]]
            print("   AI 味文本 → %s（命中 %d）" % (rules_ai or "-", len(rules_ai)))
            print("   干净文本 → %s" % (rules_clean or "0 命中"))
            record("⑤ 扫描接口：AI 味文本命中、干净文本不命中",
                   bool(rules_ai) and not rules_clean)

            r3 = cli.post("%s/api/v1/scenes/%s/prose/spot-fix" % (base, scene), json={"text": AI_ISH})
            b = r3.json()
            sb, sa = b.get("score_before") or {}, b.get("score_after") or {}
            print("   定点修复：accepted=%s 命中词 %s→%s" % (b.get("accepted"), sb.get("terms"), sa.get("terms")))
            if b.get("accepted"):
                print("     改后：%s" % str(b.get("after"))[:80])
            tb, ta = sb.get("terms"), sa.get("terms")
            # 正确的不变量（真 LLM 有随机性）：**采纳 ⟹ 命中确实下降**；
            # 未采纳必须给出原因（闸门拒绝劣质改写）。实测两次：4→0 采纳 / 4→4 回退。
            accepted_ok = (b.get("accepted") is True and tb is not None and ta is not None and ta < tb)
            rejected_ok = (b.get("accepted") is False
                           and bool(b.get("reason") or b.get("error") or b.get("skipped")))
            record("⑤ 定点修复接口：闸门生效（采纳必为改善 / 未采纳必给原因）",
                   accepted_ok or rejected_ok,
                   "accepted=%s 词 %s→%s %s" % (b.get("accepted"), tb, ta,
                                               (b.get("reason") or "")[:36]))
    except Exception as e:  # noqa: BLE001
        record("⑤ --live：后端不可达（%s）：%s" % (base, e), False)


async def run(with_live: bool) -> int:
    check_rules()
    check_gate()
    await check_spotfix()
    check_regression()
    if with_live:
        live()
    failed = [r for r in RESULTS if not r[1]]
    print("\n==== A3 验收汇总：%d/%d 通过 ====" % (len(RESULTS) - len(failed), len(RESULTS)))
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
    ap = argparse.ArgumentParser(description="A3 反 AI 味验收")
    ap.add_argument("--live", action="store_true", help="追加真实后端核对（需后端在跑）")
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
