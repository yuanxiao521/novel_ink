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

from app.config import settings  # noqa: E402
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

def test_collation_reports_what_is_missing(client):
    """逐章校对（C）：每章场景清单 + 字数 + 定稿状态 + 未定稿统计。"""
    c, _ = client
    bid = c._book_id
    cid, sid = _seed_written(c, bid)
    # 同章再加一场没定稿的 → 该章不应算"已定稿"，未定稿场景 +1
    c.post(f'/api/v1/chapters/{cid}/scenes', json={'title': '还没写的场'})
    c.post(f'/api/v1/books/{bid}/chapters', json={'title': '第二章 空章'})

    col = c.get(f'/api/v1/books/{bid}/collation').json()
    kpi = col['kpi']
    assert kpi['chapters_total'] == 2 and kpi['chapters_done'] == 0
    assert kpi['word_count'] == len(re.sub(r'\s', '', PROSE))
    assert kpi['scene_total'] == 2 and kpi['unfinished_scenes'] == 1

    ch1 = col['chapters'][0]
    assert ch1['order_no'] == 1 and ch1['done'] is False and ch1['scene_count'] == 2
    assert [s['finalized'] for s in ch1['scenes']] == [True, False]
    assert ch1['scenes'][0]['preview'].startswith('第一段。')      # 预览压平空白
    assert ch1['scenes'][1]['preview'] == ''                       # 没有正文
    ch2 = col['chapters'][1]
    assert ch2['order_no'] == 2 and ch2['scenes'] == [] and ch2['done'] is False

    assert c.get('/api/v1/books/no-such-book/collation').status_code == 404


def test_collation_counts_match_dashboard(client):
    """校对页与概览页的总字数必须同一个数（B39 口径）。"""
    c, _ = client
    bid = c._book_id
    _seed_written(c, bid)
    col = c.get(f'/api/v1/books/{bid}/collation').json()
    dash = c.get(f'/api/v1/books/{bid}/dashboard').json()
    assert col['kpi']['word_count'] == dash['kpi']['word_count']


@pytest.mark.asyncio
async def test_resume_prefers_sim_with_turns():
    """B40：恢复时必须挑"演过的"那场，不能空 sim 把有内容的挤掉。"""
    from app.db.repo import Repo
    from app.schemas.models import SimulationState, WorldState

    repo = Repo(use_db=False)
    await repo.save_book({'id': 'book-b40', 'title': 'B40', 'genre': '悬疑'})
    await repo.save_chapter({'id': 'ch-b40', 'book_id': 'book-b40', 'title': '第一章', 'order_no': 1})
    await repo.save_scene({'id': 'sc-b40', 'chapter_id': 'ch-b40', 'title': '场景', 'cursor_pos': 1})
    rich = SimulationState(scenario='betrayal_night', book_id='book-b40', chapter_id='ch-b40',
                           scene_id='sc-b40', world=WorldState(scene_id='sc-b40', title='场景', turn=5))
    empty = SimulationState(scenario='betrayal_night', book_id='book-b40', chapter_id='ch-b40',
                            scene_id='sc-b40', world=WorldState(scene_id='sc-b40', title='场景'))
    await repo.save('sim-b40-rich', rich)
    await repo.save('sim-b40-empty', empty)      # 后建的空的（旧逻辑会挑它）

    assert await repo.resume_latest('sc-b40') == 'sim-b40-rich'


@pytest.mark.skipif(not settings.persist, reason='persist=False，走内存态')
@pytest.mark.asyncio
async def test_resume_prefers_sim_with_turns_db():
    """B40 真库版：空 sim 变新，也必须恢复到有回合的那场。"""
    from app.db.repo import Repo
    from app.schemas.models import SimulationState, WorldState

    async def _db_ready() -> bool:
        import asyncio

        from sqlalchemy import text
        from sqlalchemy.ext.asyncio import create_async_engine

        eng = create_async_engine(settings.dsn.replace('postgresql://', 'postgresql+asyncpg://'))
        try:
            async with eng.connect() as conn:
                await conn.execute(text('SELECT 1'))
            return True
        except Exception:  # noqa: BLE001
            return False
        finally:
            await eng.dispose()

    if not await _db_ready():
        pytest.skip('DB 不可用，跳过真实库测试')

    repo = Repo()
    try:
        await repo.delete_book('book-b40db')
    except Exception:  # noqa: BLE001
        pass
    await repo.save_book({'id': 'book-b40db', 'title': 'B40DB', 'genre': '悬疑'})
    await repo.save_chapter({'id': 'ch-b40db', 'book_id': 'book-b40db', 'title': '第一章', 'order_no': 1})
    await repo.save_scene({'id': 'sc-b40db', 'chapter_id': 'ch-b40db', 'title': '场景', 'cursor_pos': 1})
    await repo.save('sim-b40db-rich', SimulationState(
        scenario='betrayal_night', book_id='book-b40db', chapter_id='ch-b40db', scene_id='sc-b40db',
        world=WorldState(scene_id='sc-b40db', title='场景', turn=7)))
    await repo.save('sim-b40db-empty', SimulationState(
        scenario='betrayal_night', book_id='book-b40db', chapter_id='ch-b40db', scene_id='sc-b40db',
        world=WorldState(scene_id='sc-b40db', title='场景')))

    assert await repo.resume_latest('sc-b40db') == 'sim-b40db-rich'
    await repo.delete_book('book-b40db')     # 顺带验证 B36：有 sim 也要删得掉
    assert await repo.get_book('book-b40db') is None
