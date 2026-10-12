"""v1.34 成书收口回归：字数唯一口径（B39）· 章号 1-based（B38）· 导出目录 · 跨书搜索。

覆盖用户实测反馈的两个点：
  1) 概览 KPI 与导出头部「全书约 N 字」曾经同屏打架（271 vs 259）→ 现在必须**相等**；
  2) 概览页「跨书搜索」输入框曾经完全没接线 → 现在有接口且命中带可跳转 path。
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
# 注：只有最后的 repo 级用例是 async（逐个打 marker）；TestClient 用例是同步的

PROSE = '第一段。\n\n  第二段 有空格。\n'   # 去掉所有空白后 = 13 字


@pytest.fixture(scope='module')
def client():
    from app.db.repo import Repo
    from app.api.deps import get_repo
    from app.main import app

    shared = Repo(use_db=False)

    async def _fake_repo():
        return shared

    app.dependency_overrides[get_repo] = _fake_repo
    with TestClient(app) as c:
        c._shared_repo = shared  # type: ignore[attr-defined]
        yield c, shared
    app.dependency_overrides.clear()


@pytest.fixture(autouse=True)
def _fresh(client):
    c, shared = client
    shared._mem.clear()
    r = c.post('/api/v1/books', json={'title': '成书收口测试书', 'genre': '悬疑'})
    assert r.status_code in (200, 201), r.text
    c._book_id = r.json()['id']  # type: ignore[attr-defined]
    yield c, shared


def _seed_written(c, bid, prose=PROSE):
    ch = c.post(f'/api/v1/books/{bid}/chapters', json={'title': '第一章 起'})   # 不传 order_no
    assert ch.status_code == 201, ch.text
    cid = ch.json()['id']
    sc = c.post(f'/api/v1/chapters/{cid}/scenes', json={'title': '排字房'})
    assert sc.status_code == 201, sc.text
    sid = sc.json()['id']
    assert c.put(f'/api/v1/scenes/{sid}/prose', json={'text': prose}).status_code == 200
    return cid, sid


def test_chapter_order_no_starts_at_1(client):
    """B38：不传 order_no 也要落 1-based（此前落 0 → 时间线渲染「第 0 章」）。"""
    c, _ = client
    bid = c._book_id
    c1, _ = _seed_written(c, bid)
    c2 = c.post(f'/api/v1/books/{bid}/chapters', json={'title': '第二章 承'}).json()['id']
    tree = c.get(f'/api/v1/books/{bid}/tree').json()
    orders = {ch['id']: ch['order_no'] for ch in tree['chapters']}
    assert orders[c1] == 1 and orders[c2] == 2, orders
    tl = c.get(f'/api/v1/books/{bid}/dashboard').json()['timeline']
    assert [t['order_no'] for t in tl] == [1, 2]


def test_word_count_is_single_source(client):
    """B39：概览 KPI、章时间线、场景、章节聚合、导出头部必须同一个数。"""
    c, _ = client
    bid = c._book_id
    cid, sid = _seed_written(c, bid)
    expect = len(re.sub(r'\s', '', PROSE))
    dash = c.get(f'/api/v1/books/{bid}/dashboard').json()
    assert dash['kpi']['word_count'] == expect
    assert dash['timeline'][0]['word_count'] == expect
    assert c.get(f'/api/v1/scenes/{sid}/prose').json()['word_count'] == expect
    assert c.get(f'/api/v1/chapters/{cid}/prose').json()['word_count'] == expect
    md = c.get(f'/api/v1/books/{bid}/export').text
    m = re.search(r'全书约 ([\d,]+) 字', md)
    assert m, md[:120]
    assert int(m.group(1).replace(',', '')) == expect


def test_export_has_toc_and_chapter_stats(client):
    """成书收口：导出必须有目录（章 + 字数 + 场次）。"""
    c, _ = client
    bid = c._book_id
    _seed_written(c, bid)
    md = c.get(f'/api/v1/books/{bid}/export').text
    assert '## 目录' in md
    # 章标题已含「第一章」→ 目录不再重复加「第 1 章」前缀
    assert re.search(r'1\. 第一章 起 — \d+ 字 / 1 场', md), md
    assert '全书约' in md and '共 1 章' in md


def test_search_hits_carry_paths(client):
    """概览搜索框接线：跨书命中带可跳转 path（书/章/场景/角色）。"""
    c, _ = client
    bid = c._book_id
    cid, sid = _seed_written(c, bid)
    c.post(f'/api/v1/books/{bid}/characters', json={
        'name': '顾云笙',
        'spec_json': json.dumps({'id': 'char-gu', 'name': '顾云笙', 'summary': '记者'}),
    })

    r = c.get('/api/v1/search', params={'q': '第一章'})
    assert r.status_code == 200, r.text
    hits = r.json()['hits']
    assert hits and hits[0]['kind'] == 'chapter' and hits[0]['path'].startswith('/maestro?book=')

    scenes = [h for h in c.get('/api/v1/search', params={'q': '排字房'}).json()['hits'] if h['kind'] == 'scene']
    assert scenes and scenes[0]['path'] == f'/director/{sid}?book={bid}&chapter={cid}'

    chars = [h for h in c.get('/api/v1/search', params={'q': '顾云笙'}).json()['hits'] if h['kind'] == 'character']
    assert chars and chars[0]['path'] == f'/characters?book={bid}'

    books = [h for h in c.get('/api/v1/search', params={'q': '成书收口'}).json()['hits'] if h['kind'] == 'book']
    assert books and books[0]['path'] == f'/dashboard?book={bid}'

    empty = c.get('/api/v1/search', params={'q': '   '}).json()
    assert empty['hits'] == [] and empty['counts'] == {}


@pytest.mark.asyncio
async def test_search_matches_foreshadow_in_memory():
    """伏笔也要能搜到（内存态分支）。"""
    from app.db.repo import Repo

    repo = Repo(use_db=False)
    await repo.save_book({'id': 'book-s', 'title': '搜索书', 'genre': '悬疑'})
    await repo.save_foreshadow({'id': 'f-1', 'book_id': 'book-s', 'type': 'plot',
                                'text': '半张旧头版指向沈宅灭门案', 'status': 'buried'})
    raw = await repo.search_all('旧头版')
    assert [f['id'] for f in raw['foreshadows']] == ['f-1']
    assert raw['books'] == []
