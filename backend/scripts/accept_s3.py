"""S3 验收脚本（全局结构与张力：派生 + 诊断规则 + 护栏 + 真实书接口）。

用法：
    python scripts/accept_s3.py          # 确定性验收（不依赖 DB/后端，约 3-5s）
    python scripts/accept_s3.py --live   # 追加真实书接口核对（需后端在 127.0.0.1:8000）

对应《写作优化方案-S3全局结构.md》§5 验收标准；退出码 0=全通过，可接 CI。
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

from app.services.engine.global_view import build_global_view, scene_tension  # noqa: E402

RESULTS: list = []
BOOKS = ["book-rain", "book-0938c02c", "book-7d16f6cd", "book-dde34aa2"]


def record(name: str, ok: bool, note: str = "") -> None:
    RESULTS.append((name, ok, note))
    print("[%s] %s%s" % ("PASS" if ok else "FAIL", name, (" — " + note) if note else ""))


def _book(tensions, archives=True):
    chapters, scenes, arch = [], [], {}
    for i, t in enumerate(tensions):
        cid, sid = "ch%d" % (i + 1), "sc%d" % (i + 1)
        chapters.append({"id": cid, "order_no": i + 1, "title": "第%d章" % (i + 1)})
        scenes.append({"id": sid, "chapter_id": cid, "final_prose": "正文" * 30})
        if archives:
            arch[sid] = [{"tension": t, "tension_trend": "up"}]
    return chapters, scenes, arch


def _rules(view):
    return [d["rule"] for d in view["diagnostics"]]


def check_derive():
    chapters, scenes, arch = _book([20, 50, 80])
    v = build_global_view(chapters, scenes, arch, [])
    ok = ([c["tension_avg"] for c in v["curve"]] == [20.0, 50.0, 80.0]
          and v["summary"]["peak_chapter"]["order_no"] == 3
          and v["summary"]["mean"] == 50.0)
    record("① 派生：曲线/峰值/均值正确（20/50/80）", ok)


def check_rules():
    chapters, scenes, arch = _book([36, 37, 38, 50])
    v = build_global_view(chapters, scenes, arch, [])
    record("② R1 平铺命中且唯一扣分（评分 80）", _rules(v) == ["R1"] and v["structure_score"] == 80)

    v3 = build_global_view(*_book([80, 40, 30, 20]), [])
    record("③ R3 高潮缺失（峰值在前段）", "R3" in _rules(v3))

    chapters, scenes, arch = _book([40, 50, 60])
    fs = [{"id": "f1", "status": "buried", "text": "夹层剑诀", "buried_scene": "sc1",
           "expected_close_scene": "sc1"}]
    v5 = build_global_view(chapters, scenes, arch, fs)
    record("④ R5 伏笔逾期进网络（overdue + 埋→收边）",
           v5["foreshadows"]["overdue"][0]["id"] == "f1" and len(v5["foreshadows"]["edges"]) == 1
           and "R5" in _rules(v5))


def check_guardrails():
    v = build_global_view(*_book([20]), [])
    record("⑤ 护栏：单章样本不足不报 R1-R4（评分 100）",
           _rules(v) == [] and v["structure_score"] == 100)

    chapters, scenes, _ = _book([10, 10, 10], archives=False)
    v2 = build_global_view(chapters, scenes, {}, [])
    record("⑤ 护栏：无张力不按 0 计入（曲线 None + R7 如实提示）",
           v2["curve"][0]["tension_avg"] is None and "R7" in _rules(v2)
           and not {"R1", "R2", "R3", "R4"} & set(_rules(v2)))
    record("⑤ 护栏：张力 0 视为未评估（has=False）",
           scene_tension([{"tension": 0}])["has"] is False)


def check_regression():
    r = subprocess.run([sys.executable, "-m", "pytest", "-q", "--no-header"], cwd=str(ROOT),
                       capture_output=True, text=True, encoding="utf-8", errors="replace",
                       timeout=900)
    tail = (r.stdout or "").strip().splitlines()[-1] if r.stdout else ""
    record("⑥ 回归：全量 pytest 全绿", r.returncode == 0, tail)


def live_books() -> None:
    """--live：真实书接口核对（结构完整 + 打印曲线摘要）。"""
    print("\n== --live 真实书接口核对 ==")
    try:
        import httpx
    except Exception as e:  # noqa: BLE001
        record("⑦ --live：真实书接口", False, "httpx 不可用：%s" % e)
        return
    base = os.environ.get("S3_API_BASE", "http://127.0.0.1:8000")
    ok_all, rows = True, []
    try:
        with httpx.Client(timeout=20) as cli:
            for b in BOOKS:
                resp = cli.get("%s/api/v1/books/%s/global-view" % (base, b))
                if resp.status_code != 200:
                    ok_all = False
                    rows.append("%s → HTTP %s" % (b, resp.status_code))
                    continue
                v = resp.json()
                need = {"curve", "summary", "diagnostics", "foreshadows", "structure_score"}
                if not need <= set(v):
                    ok_all = False
                rows.append("%-16s 章=%d 评分=%3d 规则=%-18s 伏笔=%d 逾期=%d"
                            % (b, len(v["curve"]), v["structure_score"],
                               ",".join(d["rule"] for d in v["diagnostics"]) or "-",
                               len(v["foreshadows"]["nodes"]), len(v["foreshadows"]["overdue"])))
    except Exception as e:  # noqa: BLE001
        record("⑦ --live：真实书接口", False, "后端不可达（%s）：%s" % (base, e))
        return
    for line in rows:
        print("   " + line)
    record("⑦ --live：真实书接口结构完整（4 本）", ok_all)


async def run(with_live: bool) -> int:
    check_derive()
    check_rules()
    check_guardrails()
    check_regression()
    if with_live:
        live_books()
    failed = [r for r in RESULTS if not r[1]]
    print("\n==== S3 验收汇总：%d/%d 通过 ====" % (len(RESULTS) - len(failed), len(RESULTS)))
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
    ap = argparse.ArgumentParser(description="S3 全局张力验收")
    ap.add_argument("--live", action="store_true", help="追加真实书接口核对（需后端在跑）")
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
