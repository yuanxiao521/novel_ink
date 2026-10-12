"""整书体检引擎回归（v1.38）：口音漂移 + 时间线回放（0-token，零 LLM）。

回归点：
  - 样本不足不下结论（角色台词 <2 场不判漂移；无对白的场不计入）；
  - 时间线：账本是 UNIQUE upsert（同 key 一行），倒流检测走 **previous_value → value**；
    作者手改（edited=True）标注但不静默；非 time/numeric 不参与；无数字跳过。
"""
from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from app.services.engine.book_audit import timeline_replay, voice_drift_by_scene  # noqa: E402


def test_voice_drift_flags_len_outliers():
    scenes = [
        {'scene_id': 's1', 'order': 1, 'title': '开场', 'text': '陈默说：「走吧。」陈默又说：「嗯。」'},
        {'scene_id': 's2', 'order': 2, 'title': '爆发', 'text': '陈默说：「我知道你不信，可我还是要说——那晚在城门口拦住我的根本不是巡捕，而是沈家自己人，他们要的不是灭口，是那份名单。」'},
    ]
    out = voice_drift_by_scene(scenes, [{'name': '陈默'}])
    assert len(out) == 1
    row = out[0]
    assert row['name'] == '陈默' and row['scenes_count'] == 2
    assert row['flagged'], '两场句长 2.5 vs 65 → 必须报漂移'


def test_voice_drift_needs_two_scenes():
    """只有一场台词 → 样本不足，不判漂移（不许误诊）。"""
    scenes = [{'scene_id': 's1', 'order': 1, 'title': '开场', 'text': '陈默说：「走吧。」'}]
    assert voice_drift_by_scene(scenes, [{'name': '陈默'}]) == []


def test_voice_drift_ignores_no_dialogue_scenes():
    scenes = [
        {'scene_id': 's1', 'order': 1, 'title': '开场', 'text': '陈默说：「走吧。」陈默又说：「嗯。」'},
        {'scene_id': 's2', 'order': 2, 'title': '无对白', 'text': '他站在巷口，没有说话。'},
        {'scene_id': 's3', 'order': 3, 'title': '平稳', 'text': '陈默说：「走吧。」陈默又说：「嗯。」'},
    ]
    out = voice_drift_by_scene(scenes, [{'name': '陈默'}])
    assert out and out[0]['scenes_count'] == 2, '无对白的场不计入'
    assert out[0]['flagged'] == [], '两场句长一致 → 不报漂移'


def test_timeline_replay_detects_rewind_via_previous_value():
    """账本是 upsert（同 key 一行）→ 倒流检测走 previous_value→value。"""
    ws = [{
        'id': 'ws-1', 'key': 'time:全书:当前日', 'kind': 'time', 'name': '当前日',
        'value': '第1日', 'previous_value': '第3日', 'scene_no': 5, 'ts': 5, 'edited': False,
    }]
    out = timeline_replay(ws)
    assert len(out['rewinds']) == 1
    rw = out['rewinds'][0]
    assert rw['from'] == 3.0 and rw['to'] == 1.0
    assert '数值倒流' in rw['detail'] and '作者手改' not in rw['detail']


def test_timeline_replay_flags_edited_rewind():
    """作者手改造成的倒流也要报，但必须标注「作者手改」（那是主动改设定）。"""
    ws = [{
        'id': 'ws-2', 'key': 'numeric:他:距离', 'kind': 'numeric', 'name': '距离',
        'value': '10米', 'previous_value': '30米', 'scene_no': 3, 'ts': 3, 'edited': True,
    }]
    out = timeline_replay(ws)
    assert out['rewinds'] and out['rewinds'][0]['edited'] is True
    assert '作者手改' in out['rewinds'][0]['detail']


def test_timeline_replay_forward_and_nonnumeric_skipped():
    ws = [
        {'id': 'ws-3', 'key': 'time:全书:当前日', 'kind': 'time', 'name': '当前日',
         'value': '第3日', 'previous_value': '第1日', 'scene_no': 2, 'ts': 2, 'edited': False},   # 推进
        {'id': 'ws-4', 'key': 'term:通用:异常者', 'kind': 'term', 'name': '异常者',
         'value': '异常者', 'previous_value': '', 'scene_no': 0, 'ts': 0, 'edited': False},       # 非 time/numeric
        {'id': 'ws-5', 'key': 'numeric:他:战力', 'kind': 'numeric', 'name': '战力',
         'value': '深不可测', 'previous_value': '', 'scene_no': 0, 'ts': 0, 'edited': False},     # 无数字
    ]
    out = timeline_replay(ws)
    assert out['rewinds'] == []
    assert {f['key'] for f in out['flows']} == {'time:全书:当前日', 'numeric:他:战力'}, 'term 不进；无数字的 numeric 仍展示现状'


def test_timeline_replay_empty():
    out = timeline_replay([])
    assert out == {'flows': [], 'rewinds': []}
