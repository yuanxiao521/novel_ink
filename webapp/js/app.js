/* ==========================================================================
   app.js —— 视图切换、主题切换、mood 切换
   ========================================================================== */

(function () {
  const root = document.documentElement;

  /* --- 视图切换 --- */
  const viewButtons = document.querySelectorAll('.view-switcher button');
  const views = {
    workbench: document.getElementById('view-workbench'),
    reader: document.getElementById('view-reader'),
  };

  function setView(target) {
    Object.keys(views).forEach(function (key) {
      views[key].classList.toggle('active', key === target);
    });
    viewButtons.forEach(function (b) {
      b.classList.toggle('active', b.dataset.view === target);
    });
  }

  viewButtons.forEach(function (btn) {
    btn.addEventListener('click', function () {
      setView(btn.dataset.view);
    });
  });

  /* 支持 #view-reader 等 hash 指定初始视图（便于深链与验证） */
  const initial = location.hash.replace('#view-', '');
  if (views[initial]) setView(initial);

  /* --- 纸墨主题切换 --- */
  const themeToggle = document.getElementById('theme-toggle');
  const themeLabel = document.getElementById('theme-label');

  function setTheme(theme) {
    root.dataset.theme = theme;
    if (themeLabel) {
      themeLabel.textContent = theme === 'paper' ? '纸' : '墨';
    }
  }

  themeToggle.addEventListener('click', function () {
    const next = root.dataset.theme === 'ink' || root.dataset.theme === 'dark' ? 'paper' : 'ink';
    setTheme(next);
  });

  /* --- mood 切换（循环：悬疑 → 紧张 → 温情 → 宁静） --- */
  const moodMap = {
    suspense: { label: '悬疑' },
    tension: { label: '紧张' },
    warmth: { label: '温情' },
    serene: { label: '宁静' },
  };
  const moodOrder = ['suspense', 'tension', 'warmth', 'serene'];

  const moodToggle = document.getElementById('mood-toggle');
  const moodText = moodToggle ? moodToggle.querySelector('.mood-text') : null;

  moodToggle.addEventListener('click', function () {
    const current = root.dataset.mood || 'suspense';
    const idx = moodOrder.indexOf(current);
    const next = moodOrder[(idx + 1) % moodOrder.length];
    root.dataset.mood = next;
    if (moodText) moodText.textContent = moodMap[next].label;
  });

  /* --- 中央舞台 tab 切换（世界黑板 / 成文） --- */
  const stageTabs = document.querySelectorAll('.tab-switch .tab');
  const stagePanes = document.querySelectorAll('.stage-pane');

  function setStageTab(target) {
    stageTabs.forEach(function (t) {
      t.classList.toggle('active', t.dataset.stageTab === target);
    });
    stagePanes.forEach(function (p) {
      p.classList.toggle('active', p.dataset.pane === target);
    });
  }

  stageTabs.forEach(function (tab) {
    tab.addEventListener('click', function () {
      setStageTab(tab.dataset.stageTab);
    });
  });
})();

/* ==========================================================================
   v0.2 Task6 —— 导演台 SSE 数据驱动层
   建 sim → 拉 state 填初始数据 → EventSource 订阅 /stream 流式渲染。
   不触碰上方主题/mood/tab 交互逻辑。API_BASE 可用 window.API_BASE 覆盖。
   ========================================================================== */
(function () {
  const API_BASE = window.API_BASE || 'http://localhost:8000';

  /* ---------- DOM 引用 ---------- */
  const $ = (sel, ctx) => (ctx || document).querySelector(sel);
  const $$ = (sel, ctx) => Array.prototype.slice.call((ctx || document).querySelectorAll(sel));

  const els = {
    turnLabel: $('.scene-group .turn-label'),   // 顶栏回合
    runText: $('.run-tag .run-text'),           // 运行状态文字
    runDot: $('.run-tag .run-dot'),
    envSection: $('.blackboard .env-section'),  // 世界黑板·环境事实
    eventSection: $('.blackboard .event-section'), // 世界黑板·当前回合事件
    tensionVal: $('.tension-val'),
    tensionFill: $('.tension-fill'),
    tensionTrend: $('.tension-trend'),
    directorPanel: $('.director-panel'),
    raiseCard: $('.raise-card'),
    raiseReason: $('.raise-card .raise-reason'),
    raiseAgree: $('.raise-card .raise-btn.agree'),
    raiseReject: $('.raise-card .raise-btn.reject'),
    guardFootLeft: $('.guard-panel .guard-foot-left'),
    guardFootRight: $('.guard-panel .guard-foot-right'),
    proseContent: $('.prose-view .prose-content'),
    timelineTrack: $('.tl-track'),
  };

  /* 按 data-char-id 建立角色卡索引；每张卡注入一条内心独白位 */
  const charCards = {};
  const charNames = {};
  const charThoughtEls = {};
  const charActionEls = {};
  const charMoodEls = {};
  $$('.char-card').forEach(function (card) {
    const id = card.getAttribute('data-char-id');
    if (!id) return;
    charCards[id] = card;
    charNames[id] = card.querySelector('.char-name') ? card.querySelector('.char-name').textContent : id;
    charMoodEls[id] = card.querySelector('.char-mood');

    // 内心独白小行
    const th = document.createElement('div');
    th.className = 'char-thought';
    th.style.display = 'none';
    card.appendChild(th);
    charThoughtEls[id] = th;

    // 行动/台词小行
    const ac = document.createElement('div');
    ac.className = 'char-action';
    ac.style.display = 'none';
    card.appendChild(ac);
    charActionEls[id] = ac;
  });

  /* ---------- 运行态 ---------- */
  const state = {
    sid: null,
    currentTurn: null,
    currentNodeEl: null,
    currentTurnType: { cls: 'type-info', summary: '' }, // 本回合正在累积的类型/摘要
    timelineInited: false,
    eventInited: false,
    envInited: false,
    proseInited: false,
    lastFinishedTurnTypes: {},   // turn -> {cls,summary}
    es: null,
  };

  const esc = function (t) { return String(t == null ? '' : t); };

  function setRunText(text, ok) {
    if (!els.runText) return;
    els.runText.textContent = text;
    if (els.runDot) els.runDot.style.background = ok === false ? 'var(--accent-gold)' : 'var(--accent-green)';
  }

  /* ---------- 建 sim + 拉初始状态 ---------- */
  async function initDirector() {
    try {
      const started = await fetchJson(`${API_BASE}/api/v1/sims?scenario=betrayal_night`, { method: 'POST' });
      if (!started.sim_id) throw new Error('建 sim 未返回 sim_id');
      state.sid = started.sim_id;

      const st = await fetchJson(`${API_BASE}/api/v1/sims/${state.sid}/state`);
      applyState(st);

      connectStream();
      setRunText('运行中');
    } catch (e) {
      // 后端未起/跨域等 —— 保留页面上硬编码静态数据，仅在控制台提示
      console.warn('[导演台] 后端连接失败，回退为静态展示：', e);
      setRunText('未连接', false);
    }
  }

  async function fetchJson(url, opts) {
    const res = await fetch(url, Object.assign({ headers: { 'Content-Type': 'application/json' } }, opts));
    if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
    return res.json();
  }

  /* 用 GET state 回填初始：张力 + 角色信念 + 护栏 */
  function applyState(st) {
    if (typeof st.tension === 'number') renderTension(st.tension, st.tension_trend);
    if (st.characters && st.characters.length) {
      // 回填各角色信念账本（当前端有对应卡时）
      const beliefs = st.beliefs || {};
      Object.keys(charCards).forEach(function (id) {
        const list = beliefs[id];
        if (!list || !list.length) return;
        const card = charCards[id];
        const items = card.querySelector('.belief-items');
        const countEl = card.querySelector('.belief-count');
        if (!items) return;
        items.innerHTML = '';
        list.forEach(function (b) {
          items.appendChild(buildBeliefItem(b));
        });
        if (countEl) countEl.textContent = list.length + ' 条';
      });
    }
    if (st.guard) renderGuard(st.guard);
  }

  const channelTag = { perceived: '亲见', told: '被告知', inferred: '推测' };
  const channelCls = { perceived: 'tag-witness', told: 'tag-told', inferred: 'tag-infer' };
  function buildBeliefItem(b) {
    const item = document.createElement('div');
    item.className = 'belief-item';
    const tag = document.createElement('span');
    tag.className = 'belief-tag ' + (channelCls[b.channel] || 'tag-infer');
    tag.textContent = channelTag[b.channel] || '推测';
    const text = document.createElement('span');
    text.className = 'belief-text';
    text.textContent = b.text || '';
    item.appendChild(tag);
    item.appendChild(text);
    return item;
  }

  /* ---------- SSE 订阅 ---------- */
  function connectStream() {
    if (!state.sid) return;
    if (state.es) { try { state.es.close(); } catch (e) {} }
    const es = new EventSource(`${API_BASE}/api/v1/sims/${state.sid}/stream`);
    state.es = es;

    es.addEventListener('turn_start', function (e) { onTurnStart(parse(e)); });
    es.addEventListener('character_perceive', function (e) { onPerceive(parse(e)); });
    es.addEventListener('character_think', function (e) { onThink(parse(e)); });
    es.addEventListener('character_decide', function (e) { /* 决策中间量已随 act 呈现，此处轻量占位 */ });
    es.addEventListener('character_act', function (e) { onAct(parse(e)); });
    es.addEventListener('director', function (e) { onDirector(parse(e)); });
    es.addEventListener('guard', function (e) { onGuard(parse(e)); });
    es.addEventListener('prose', function (e) { onProse(parse(e)); });
    es.addEventListener('turn_end', function (e) { onTurnEnd(parse(e)); });
    es.addEventListener('done', function (e) { onDone(parse(e)); });
    es.onerror = function () {
      // 流结束/后端停止时静默；done 事件已处理收束
    };
  }

  function parse(e) {
    try { return JSON.parse(e.data); } catch (err) { return {}; }
  }

  /* ---------- 回合生命周期 ---------- */
  function onTurnStart(d) {
    const n = d.turn;
    state.currentTurn = n;
    state.currentTurnType = { cls: 'type-info', summary: '' };
    if (els.turnLabel) els.turnLabel.textContent = '回合 T-' + pad(n);
    ensureTimeline();
    finalizeCurrentNode();      // 把上一个 current 节点定稿
    const node = document.createElement('div');
    node.className = 'turn-node current';
    node.innerHTML = '<span class="turn-dot"></span><span class="turn-label">T-' + pad(n) + '</span><span class="turn-desc">推演中…</span>';
    els.timelineTrack.appendChild(node);
    state.currentNodeEl = node;
  }

  function onPerceive(d) { /* 感知事件无需强 UI，留作日志扩展位 */ }

  function onThink(d) {
    const el = charThoughtEls[d.char_id];
    if (!el || d.thought == null || d.thought === '') { if (el) el.style.display = 'none'; return; }
    el.innerHTML = '<span class="thought-arrow">·思考</span>' + esc(d.thought);
    el.style.display = '';
  }

  function onAct(d) {
    const ac = charActionEls[d.char_id];
    let lastEv = null;
    const events = d.events || [];
    events.forEach(function (ev) {
      const kind = evKind(ev);
      const txt = evText(ev);
      lastEv = ev;
      renderEventCard(d, ev, kind);
      renderEnvIfNeeded(ev);
      // 台词不在这里单独进成文栏：后端成文段落已把全部台词拼进叙事文本
      //（见 graph.py render_prose），再渲一轮会导致同一句重复出现。
      applyMoodFromText(d.char_id, txt);
      accumulateTurnType(ev, kind, txt);
    });
    // 角色卡内最新行动/台词
    if (ac && lastEv) {
      const kind = evKind(lastEv);
      const txt = evText(lastEv);
      ac.textContent = kind === 'dialogue' ? '『' + esc(speakerName(d, lastEv)) + '』　' + txt : txt;
      ac.style.display = '';
    }
  }

  /* 优先级 conflict > dialogue > action > info，摘要取最新一句 */
  function accumulateTurnType(ev, kind, txt) {
    const order = { conflict: 0, dialogue: 1, action: 2, info: 3 };
    const cur = state.currentTurnType;
    const map = {
      conflict: 'type-conflict', dialogue: 'type-dialogue',
      action: 'type-action', info: 'type-info',
    };
    const key = map[kind] || 'type-info';
    const curKey = cur.cls.replace('type-', '');
    if (order[kind] < (order[curKey] !== undefined ? order[curKey] : 4)) {
      cur.cls = key;
    }
    if (txt) cur.summary = txt.replace(/^（.*?）/, '').slice(0, 12) || cur.summary;
  }

  function onDirector(d) {
    if (typeof d.tension === 'number') renderTension(d.tension, d.tension_trend);
    if (d.hint) renderHints(d);
    if (d.raise_request) renderRaise(d.raise_request);
  }

  function onGuard(d) { if (d.guard) renderGuard(d.guard); }

  function onProse(d) {
    if (!d.text) return;
    if (!state.proseInited) { els.proseContent.innerHTML = ''; state.proseInited = true; }
    const p = document.createElement('p');
    p.className = 'prose-paragraph';
    p.textContent = d.text;
    els.proseContent.appendChild(p);
    els.proseContent.scrollTop = els.proseContent.scrollHeight;
  }

  function onTurnEnd(d) {
    const n = d.turn;
    state.lastFinishedTurnTypes[n] = state.currentTurnType;
    state.currentTurnType = { cls: 'type-info', summary: '' };
    finalizeCurrentNode();
  }

  function onDone(d) {
    if (state.es) { try { state.es.close(); } catch (e) {} }
    finalizeCurrentNode();
    if (d.raise_pending) { if (els.raiseCard) els.raiseCard.classList.remove('hidden'); }
    let text = '已收束';
    if (d.converged) text = '已收敛';
    else if (d.ended) text = '已结束';
    else if (d.raise_pending) text = '等待导演介入';
    setRunText(text, d.raise_pending ? false : true);
  }

  /* ---------- 渲染函数 ---------- */

  function ensureTimeline() {
    if (state.timelineInited || !els.timelineTrack) return;
    state.timelineInited = true;
    els.timelineTrack.innerHTML = '';
  }

  function pad(n) { return n < 10 ? '0' + n : String(n); }

  function finalizeCurrentNode() {
    const node = state.currentNodeEl;
    if (!node) return;
    const n = currentTurnNumber(node);
    const type = nodeTypeForTurn(n);
    node.classList.remove('current');
    node.classList.remove('type-dialogue', 'type-action', 'type-conflict', 'type-info');
    node.classList.add(type.cls);
    const desc = node.querySelector('.turn-desc');
    if (desc && type.summary) desc.textContent = type.summary;
    state.currentNodeEl = null;
  }

  function currentTurnNumber(node) {
    const lbl = node.querySelector('.turn-label');
    return lbl ? parseInt(lbl.textContent.replace(/\D+/g, ''), 10) || 0 : 0;
  }

  function nodeTypeForTurn(n) {
    const t = state.lastFinishedTurnTypes[n] || {};
    return { cls: t.cls || 'type-info', summary: t.summary || '回合 T-' + pad(n) };
  }

  function evKind(ev) {
    const p = ev.payload || {};
    if (p.kind) return p.kind;
    const t = ev.type;
    if (t === 'environment') return 'environment';
    if (t === 'action') return 'action';
    return 'info';
  }

  function evText(ev) {
    const p = ev.payload || {};
    return p.text || (Array.isArray(p.hints) ? p.hints.join('；') : '');
  }

  /* 事件真正的发言/行动者：优先 payload.actor（SSE 按 char_id 分组但事件有独立 actor） */
  function speakerName(d, ev) {
    const p = ev.payload || {};
    const id = p.actor || d.char_id;
    return charNames[id] || String(id || '') || charNames[d.char_id] || d.char_id;
  }

  /* 把一条行动事件渲染为黑板上当前回合事件卡 */
  function renderEventCard(d, ev, kind) {
    if (!els.eventSection) return;
    if (!state.eventInited) {
      // 保留小节标题，清除静态示例卡
      const title = els.eventSection.querySelector('.section-title');
      els.eventSection.innerHTML = '';
      if (title) els.eventSection.appendChild(title);
      state.eventInited = true;
    }
    const map = {
      dialogue: { label: '对话', cls: 'type-dialogue' },
      action: { label: '行动', cls: 'type-action' },
      conflict: { label: '冲突', cls: 'type-conflict' },
      environment: { label: '环境', cls: 'type-info' },
    };
    const m = map[kind] || { label: '信息', cls: 'type-info' };
    const txt = evText(ev);
    if (!txt) return;

    const card = document.createElement('div');
    card.className = 'event-card ' + m.cls;
    const meta = document.createElement('div');
    meta.className = 'event-meta';
    meta.innerHTML = '<span class="event-type-tag"><span class="event-type ' + m.cls + '">' + m.label + '</span></span>' +
                     '<span class="event-id">' + esc(ev.id || '') + '</span>';
    const quote = document.createElement('div');
    quote.className = 'event-quote';
    quote.textContent = kind === 'dialogue' ? '「' + txt + '」' : txt;
    const src = document.createElement('div');
    src.className = 'event-source';
    const who = speakerName(d, ev);
    src.textContent = (kind === 'dialogue' ? who + ' 说' : who + ' · 展开行动') +
                      (ev.guard_flags && ev.guard_flags.length ? '　护栏: ' + ev.guard_flags.join('、') : '');
    card.appendChild(meta);
    card.appendChild(quote);
    card.appendChild(src);
    els.eventSection.appendChild(card);
    els.eventSection.scrollTop = els.eventSection.scrollHeight;
  }

  /* 环境/新事实 → 世界黑板 env 区追加一行 */
  function renderEnvIfNeeded(ev) {
    if (!els.envSection) return;
    const p = ev.payload || {};
    const isEnv = ev.type === 'environment' || p.kind === 'environment' || (p.new_fact && String(p.new_fact).length);
    let txt = '';
    if (p.kind === 'environment' || ev.type === 'environment') txt = evText(ev);
    if (!txt && p.new_fact && String(p.new_fact).length) txt = String(p.new_fact);
    if (!txt) return;

    if (!state.envInited) {
      const title = els.envSection.querySelector('.section-title');
      els.envSection.innerHTML = '';
      if (title) els.envSection.appendChild(title);
      state.envInited = true;
    }
    const row = document.createElement('div');
    row.className = 'env-row';
    row.innerHTML = '<span class="env-dot"></span><span class="env-text">' + esc(txt) + '</span>';
    els.envSection.appendChild(row);
  }

  /* 用台词文本中的情绪词近似更新角色 mood */
  function applyMoodFromText(charId, text) {
    const el = charMoodEls[charId];
    if (!el || !text) return;
    const m = detectMood(text);
    if (!m) return;
    el.textContent = m;
  }

  const moodMap = [
    ['警觉', ['警觉', '防备', '警惕', '盯']],
    ['怀疑', ['起疑', '怀疑', '多疑', '追问']],
    ['心虚', ['心虚', '强撑', '试探', '绞着', '闪躲']],
    ['愤怒', ['一拍桌', '猛拍', '怒', '质问', '发抖', '怒喝']],
    ['不安', ['不安', '焦躁', '忐忑', '咬唇']],
    ['愧疚', ['愧疚', '愧', '低语', '示弱', '让步']],
    ['冷静', ['冷静', '克制', '淡然', '平稳']],
    ['沉默', ['沉默', '不发', '闭嘴', '无言', '缓缓', '低头', '沉默地']],
    ['紧张', ['紧张', '绞着衣角', '手心', '慌乱']],
  ];
  function detectMood(text) {
    for (let i = 0; i < moodMap.length; i++) {
      const words = moodMap[i][1];
      for (let j = 0; j < words.length; j++) {
        if (text.indexOf(words[j]) !== -1) return moodMap[i][0];
      }
    }
    return null;
  }

  /* 张力指数 + 趋势 */
  function renderTension(v, trend) {
    const pct = Math.max(0, Math.min(100, v));
    if (els.tensionVal) els.tensionVal.textContent = Math.round(pct) + '%';
    if (els.tensionFill) els.tensionFill.style.width = pct + '%';
    if (els.tensionTrend) {
      const map = { up: '张力上升中', down: '张力回落中', flat: '张力趋于平稳' };
      els.tensionTrend.textContent = map[trend] || '张力指数';
    }
  }

  /* 导演提示面板 */
  function renderHints(d) {
    if (!els.directorPanel) return;
    els.directorPanel.innerHTML = '<div class="panel-title">本回合导演提示</div>';
    const hint = document.createElement('div');
    hint.className = 'hint';
    hint.textContent = d.hint || '(无可写提示)';
    els.directorPanel.appendChild(hint);
    const rr = d.raise_request;
    if (rr && rr.pending && rr.reason) {
      const g = document.createElement('div');
      g.className = 'hint gold';
      g.textContent = '作者介入点：' + rr.reason;
      els.directorPanel.appendChild(g);
    }
  }

  /* 举手卡片：pending 展示 + 同意/驳回 调用 intervene */
  function renderRaise(rr) {
    if (!els.raiseCard) return;
    if (!rr.pending) { els.raiseCard.classList.add('hidden'); return; }
    els.raiseCard.classList.remove('hidden');
    if (els.raiseReason) els.raiseReason.textContent = rr.reason || '剧情走到作者决策点';
  }

  /* 护栏面板 */
  function renderGuard(g) {
    if (!g) return;
    if (els.guardFootLeft) els.guardFootLeft.textContent = '本回合拦截 ' + (g.last_turn_blocks || 0) + ' 次';
    if (els.guardFootRight) {
      const fused = Object.keys(g.fuse_counts || {}).reduce((s, k) => s + (g.fuse_counts[k] || 0), 0);
      els.guardFootRight.textContent = '熔断 ' + fused + ' 次';
    }
  }

  /* 介入批复 → 重新连接流继续 */
  function resolveRaise(action) {
    if (!state.sid) return;
    fetchJson(`${API_BASE}/api/v1/sims/${state.sid}/intervene`, {
      method: 'POST',
      body: JSON.stringify({ action: action }),
    }).then(function () {
      els.raiseCard.classList.add('hidden');
      connectStream();   // 批复后重开流继续推演
    }).catch(function (e) {
      console.warn('[导演台] intervene 失败：', e);
    });
  }

  function wireRaiseButtons() {
    if (els.raiseAgree) els.raiseAgree.addEventListener('click', function () { resolveRaise('accept'); });
    if (els.raiseReject) els.raiseReject.addEventListener('click', function () { resolveRaise('reject'); });
  }

  /* ---------- 启动 ---------- */
  function boot() {
    if (!els.turnLabel && !els.timelineTrack) return; // 非工作台环境直接跳过
    wireRaiseButtons();
    if (document.readyState === 'loading') {
      document.addEventListener('DOMContentLoaded', initDirector);
    } else {
      initDirector();
    }
  }

  // 暴露给外部（便于调试/手动触发）
  window.Director = {
    init: initDirector,
    connect: connectStream,
    getState: function () { return state; },
  };

  boot();
})();
