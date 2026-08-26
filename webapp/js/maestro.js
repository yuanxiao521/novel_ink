(function () {
  'use strict';

  // 初始数据
  const inspirations = [
    {
      id: 1,
      icon: '⚔️',
      title: '废材觉醒',
      desc: '主角在宗门大比前夜，被神秘古玉中的残魂点醒隐藏血脉。',
      type: '剧情',
      typeClass: 'plot',
      adopted: false,
    },
    {
      id: 2,
      icon: '🏛️',
      title: '宗门背叛',
      desc: '养育主角十年的师父为保圣子之位，暗中抽取他的玄脉。',
      type: '剧情',
      typeClass: 'plot',
      adopted: false,
    },
    {
      id: 3,
      icon: '🧭',
      title: '秘境探险',
      desc: '主角坠崖后进入上古剑冢，获得可吞噬血脉的残缺功法。',
      type: '世界观',
      typeClass: 'world',
      adopted: false,
    },
    {
      id: 4,
      icon: '🎭',
      title: '双生妹妹',
      desc: '妹妹并非血亲，而是上古神族封印在人间的钥匙。',
      type: '人物',
      typeClass: 'character',
      adopted: false,
    },
    {
      id: 5,
      icon: '🔥',
      title: '血脉真相',
      desc: '主角被夺走的不是废脉，而是千年难遇的「青冥真龙血」。',
      type: '剧情',
      typeClass: 'plot',
      adopted: false,
    },
  ];

  const outline = [
    {
      id: 'c1',
      number: '第一章',
      title: '玄脉被夺',
      badge: '动作',
      badgeClass: 'action',
      wordcount: '4,200',
      expanded: true,
      active: false,
      scenes: [
        { id: 's1-1', title: '宗门大比前夕', stage: '🏔️ 青云主峰', avatars: ['portrait-chenmo.png'], goal: '铺垫主角处境，暗示师父偏心' },
        { id: 's1-2', title: '月夜抽血', stage: '🌙 禁地密室', avatars: ['portrait-chenmo.png', 'portrait-liwen.png'], goal: '核心冲突爆发，主角被推下悬崖' },
      ],
    },
    {
      id: 'c2',
      number: '第二章',
      title: '古玉残魂',
      badge: '悬念',
      badgeClass: 'tension',
      wordcount: '3,800',
      expanded: false,
      active: true,
      scenes: [
        { id: 's2-1', title: '剑冢苏醒', stage: '⚔️ 上古剑冢', avatars: ['portrait-chenmo.png'], goal: '引入残魂导师，揭示隐藏血脉' },
        { id: 's2-2', title: '吞脉功法', stage: '📜 传承石室', avatars: ['portrait-chenmo.png'], goal: '主角获得复仇的资本' },
      ],
    },
    {
      id: 'c3',
      number: '第三章',
      title: '重返宗门',
      badge: '动作',
      badgeClass: 'action',
      wordcount: '4,500',
      expanded: false,
      active: false,
      scenes: [
        { id: 's3-1', title: '伪装散修', stage: '🏛️ 山门外镇', avatars: ['portrait-chenmo.png', 'portrait-zhoushen.png'], goal: '主角以新身份潜回宗门' },
        { id: 's3-2', title: '初见圣子', stage: '🏟️ 试炼广场', avatars: ['portrait-chenmo.png', 'portrait-liwen.png'], goal: '埋下正面冲突的种子' },
      ],
    },
    {
      id: 'c4',
      number: '第四章',
      title: '妹妹的踪迹',
      badge: '揭秘',
      badgeClass: 'reveal',
      wordcount: '4,000',
      expanded: false,
      active: false,
      scenes: [
        { id: 's4-1', title: '周婶的密信', stage: '🏠 旧居柴房', avatars: ['portrait-zhoushen.png'], goal: '通过 NPC 透露妹妹线索' },
        { id: 's4-2', title: '血脉共鸣', stage: '🌌 后山禁地', avatars: ['portrait-chenmo.png'], goal: '揭示妹妹非血亲的伏笔' },
      ],
    },
  ];

  const messages = [
    {
      sender: 'user',
      text: '我想写一个废材复仇的玄幻小说，主角被宗门背叛，最后发现妹妹是神族钥匙。',
    },
    {
      sender: 'agent',
      text: '已理解你的方向。我建议把主线拆成「玄脉被夺→古玉残魂→重返宗门→妹妹的踪迹」四个章节，先建立复仇动机，再逐步揭开世界观。',
      toolCall: null,
      miniOutline: [
        { num: '第一章', title: '玄脉被夺' },
        { num: '第二章', title: '古玉残魂' },
        { num: '第三章', title: '重返宗门' },
        { num: '第四章', title: '妹妹的踪迹' },
      ],
    },
    {
      sender: 'agent',
      text: '我刚用工具生成了 5 张灵感卡，其中「双生妹妹」和「血脉真相」可以作为贯穿全书的伏笔，你觉得如何？',
      toolCall: '🔧 已生成 5 张灵感卡',
    },
  ];

  // DOM 元素
  const inspirationList = document.getElementById('inspirationList');
  const adoptedList = document.getElementById('adoptedList');
  const inspCount = document.getElementById('inspCount');
  const outlineTree = document.getElementById('outlineTree');
  const chatMessages = document.getElementById('chatMessages');
  const chatInput = document.getElementById('chatInput');
  const btnSend = document.getElementById('btnSend');
  const btnIdeate = document.getElementById('btnIdeate');
  const agentStatus = document.getElementById('agentStatus');
  const directionExpand = document.getElementById('directionExpand');
  const directionPanel = document.getElementById('directionPanel');
  const btnExpandAll = document.getElementById('btnExpandAll');
  const btnCollapseAll = document.getElementById('btnCollapseAll');
  const drawerHandle = document.getElementById('drawerHandle');
  const tensionDrawer = document.getElementById('tensionDrawer');
  const btnToggleDrawer = document.getElementById('btnToggleDrawer');
  const btnAddCard = document.getElementById('btnAddCard');
  const toggleSkeleton = document.getElementById('toggleSkeleton');
  const themeToggle = document.getElementById('themeToggle');

  // 渲染灵感池
  function renderInspirations() {
    const activeList = inspirations.filter((i) => !i.adopted);
    const adoptedItems = inspirations.filter((i) => i.adopted);

    inspCount.textContent = activeList.length;

    inspirationList.innerHTML = activeList
      .map(
        (card) => `
        <div class="inspiration-card" data-id="${card.id}">
          <div class="card-header">
            <div class="card-icon ${card.typeClass}">${card.icon}</div>
            <div class="card-title-wrap">
              <h4 class="card-title">${card.title}</h4>
              <p class="card-desc">${card.desc}</p>
            </div>
          </div>
          <div class="card-footer">
            <span class="type-tag">${card.type}</span>
            <button class="btn-adopt" data-id="${card.id}">采纳</button>
          </div>
        </div>
      `
      )
      .join('');

    adoptedList.innerHTML = adoptedItems.length
      ? adoptedItems
          .map(
            (card) => `
          <div class="inspiration-card adopted" data-id="${card.id}">
            <div class="card-header">
              <div class="card-icon ${card.typeClass}">${card.icon}</div>
              <div class="card-title-wrap">
                <h4 class="card-title">${card.title}</h4>
                <p class="card-desc">${card.desc}</p>
              </div>
            </div>
            <div class="card-footer">
              <span class="type-tag">${card.type}</span>
              <button class="btn-adopt" data-id="${card.id}">已采纳</button>
            </div>
          </div>
        `
          )
          .join('')
      : '<div style="color:var(--maestro-text-3);font-size:12px;padding:8px 0;">暂无已采纳灵感</div>';

    document.querySelectorAll('.btn-adopt').forEach((btn) => {
      btn.addEventListener('click', (e) => {
        e.stopPropagation();
        const id = parseInt(btn.dataset.id, 10);
        const card = inspirations.find((i) => i.id === id);
        if (card) {
          card.adopted = !card.adopted;
          renderInspirations();
        }
      });
    });
  }

  // 渲染骨架树
  function renderOutline() {
    outlineTree.innerHTML = outline
      .map((chapter) => {
        const sceneHtml = chapter.scenes
          .map(
            (scene) => `
            <div class="outline-row scene" data-scene="${scene.id}">
              <span class="row-number"></span>
              <span class="row-title">${scene.title}</span>
              <div class="scene-meta">
                <span class="scene-stage">${scene.stage}</span>
                <div class="scene-avatars">
                  ${scene.avatars.map((src) => `<img src="assets/${src}" alt="" />`).join('')}
                </div>
                <span class="scene-goal">${scene.goal}</span>
              </div>
            </div>
          `
          )
          .join('');

        return `
          <div class="outline-chapter" data-chapter="${chapter.id}">
            <div class="outline-row chapter ${chapter.active ? 'active' : ''}" data-id="${chapter.id}">
              <span class="row-toggle">${chapter.expanded ? '▾' : '▸'}</span>
              <span class="row-number">${chapter.number}</span>
              <span class="row-title">${chapter.title}</span>
              <span class="row-badge ${chapter.badgeClass}">${chapter.badge}</span>
              <span class="row-wordcount">${chapter.wordcount}字</span>
            </div>
            <div class="outline-children ${chapter.expanded ? '' : 'collapsed'}">
              ${sceneHtml}
            </div>
          </div>
        `;
      })
      .join('');

    document.querySelectorAll('.outline-row.chapter').forEach((row) => {
      row.addEventListener('click', () => {
        const chapterId = row.dataset.id;
        const chapter = outline.find((c) => c.id === chapterId);
        if (chapter) {
          outline.forEach((c) => (c.active = false));
          chapter.active = true;
          renderOutline();
        }
      });
    });

    document.querySelectorAll('.row-toggle').forEach((toggle) => {
      toggle.addEventListener('click', (e) => {
        e.stopPropagation();
        const chapterId = toggle.closest('.outline-chapter').dataset.chapter;
        const chapter = outline.find((c) => c.id === chapterId);
        if (chapter) {
          chapter.expanded = !chapter.expanded;
          renderOutline();
        }
      });
    });
  }

  // 渲染对话
  function renderMessages() {
    chatMessages.innerHTML = messages
      .map((msg, index) => {
        const isLast = index === messages.length - 1;
        const miniOutlineHtml =
          toggleSkeleton.checked && msg.miniOutline
            ? `
            <div class="mini-outline">
              <div class="mini-outline-title">骨架预览</div>
              ${msg.miniOutline
                .map((ch) => `<div class="mini-chapter"><span class="num">${ch.num}</span><span>${ch.title}</span></div>`)
                .join('')}
            </div>
          `
            : '';

        const toolCallHtml = msg.toolCall ? `<div class="tool-call">${msg.toolCall}</div>` : '';

        return `
          <div class="chat-bubble ${msg.sender}">
            <div class="bubble-sender">${msg.sender === 'user' ? '你' : '主笔 Agent'}</div>
            <p class="bubble-text">${msg.text}${isLast && msg.sender === 'agent' ? '<span class="cursor"></span>' : ''}</p>
            ${toolCallHtml}
            ${miniOutlineHtml}
          </div>
        `;
      })
      .join('');

    chatMessages.scrollTop = chatMessages.scrollHeight;
  }

  // 张力曲线绘制
  function drawTensionCurve() {
    const canvas = document.getElementById('tensionCanvas');
    if (!canvas) return;
    const ctx = canvas.getContext('2d');
    const dpr = window.devicePixelRatio || 1;
    const rect = canvas.getBoundingClientRect();
    canvas.width = rect.width * dpr;
    canvas.height = rect.height * dpr;
    ctx.scale(dpr, dpr);

    const w = rect.width;
    const h = rect.height;
    const padding = { top: 18, bottom: 28, left: 24, right: 24 };

    const chapters = ['开篇', '上升', '转折', '高潮', '回落', '结局'];
    const tension = [12, 28, 55, 92, 48, 20];
    const planted = [0, 1, 2, 0, 1, 0];
    const resolved = [0, 0, 0, 1, 2, 1];

    ctx.clearRect(0, 0, w, h);

    // 网格
    ctx.strokeStyle = 'rgba(212, 168, 83, 0.08)';
    ctx.lineWidth = 1;
    for (let i = 0; i <= 4; i++) {
      const y = padding.top + ((h - padding.top - padding.bottom) * i) / 4;
      ctx.beginPath();
      ctx.moveTo(padding.left, y);
      ctx.lineTo(w - padding.right, y);
      ctx.stroke();
    }

    // 张力曲线
    const points = chapters.map((_, i) => ({
      x: padding.left + ((w - padding.left - padding.right) * i) / (chapters.length - 1),
      y: padding.top + (h - padding.top - padding.bottom) * (1 - tension[i] / 100),
    }));

    ctx.beginPath();
    ctx.moveTo(points[0].x, points[0].y);
    for (let i = 0; i < points.length - 1; i++) {
      const cp1 = { x: (points[i].x + points[i + 1].x) / 2, y: points[i].y };
      const cp2 = { x: (points[i].x + points[i + 1].x) / 2, y: points[i + 1].y };
      ctx.bezierCurveTo(cp1.x, cp1.y, cp2.x, cp2.y, points[i + 1].x, points[i + 1].y);
    }
    ctx.strokeStyle = '#c96b6b';
    ctx.lineWidth = 2.5;
    ctx.stroke();

    // 节点
    points.forEach((p, i) => {
      // 张力点
      ctx.beginPath();
      ctx.arc(p.x, p.y, 5, 0, Math.PI * 2);
      ctx.fillStyle = i === 3 ? '#d4a853' : '#c96b6b';
      ctx.fill();
      ctx.strokeStyle = 'rgba(13, 17, 23, 0.9)';
      ctx.lineWidth = 2;
      ctx.stroke();

      // 章节标签
      ctx.fillStyle = '#b8b2a6';
      ctx.font = '11px "Noto Sans SC", sans-serif';
      ctx.textAlign = 'center';
      ctx.fillText(chapters[i], p.x, h - 10);

      // 伏笔埋设
      if (planted[i]) {
        ctx.beginPath();
        ctx.arc(p.x, p.y - 16, 3, 0, Math.PI * 2);
        ctx.fillStyle = '#7db9a8';
        ctx.fill();
      }

      // 伏笔回收
      if (resolved[i]) {
        ctx.beginPath();
        ctx.arc(p.x, p.y + 16, 3, 0, Math.PI * 2);
        ctx.fillStyle = '#d4a853';
        ctx.fill();
      }
    });
  }

  // 让主笔构思
  function runIdeation() {
    agentStatus.dataset.state = 'thinking';
    agentStatus.querySelector('.status-text').textContent = '主笔思考中';
    btnIdeate.disabled = true;

    setTimeout(() => {
      agentStatus.dataset.state = 'tool';
      agentStatus.querySelector('.status-text').textContent = '调用工具中';

      setTimeout(() => {
        const newCard = {
          id: Date.now(),
          icon: '🔮',
          title: '残魂试炼',
          desc: '古玉残魂要求主角通过三道心魔试炼，才能继承真正的力量。',
          type: '世界观',
          typeClass: 'world',
          adopted: false,
        };
        inspirations.unshift(newCard);
        renderInspirations();

        messages.push({
          sender: 'agent',
          text: '基于你的方向，我新增了「残魂试炼」这个灵感卡：主角获得力量不是白送的，而要付出代价。这让成长更有分量。',
          toolCall: '🔧 已生成 1 张灵感卡',
        });
        renderMessages();

        agentStatus.dataset.state = 'idle';
        agentStatus.querySelector('.status-text').textContent = '主笔空闲中';
        btnIdeate.disabled = false;
      }, 1400);
    }, 1600);
  }

  // 发送消息
  function sendMessage() {
    const text = chatInput.value.trim();
    if (!text) return;

    messages.push({ sender: 'user', text });
    chatInput.value = '';
    renderMessages();

    agentStatus.dataset.state = 'thinking';
    agentStatus.querySelector('.status-text').textContent = '主笔思考中';

    setTimeout(() => {
      messages.push({
        sender: 'agent',
        text: '收到，我会根据这个方向调整骨架。如果你希望我把某个灵感卡直接落地成章节场景，点击卡片上的「采纳」即可。',
      });
      renderMessages();
      agentStatus.dataset.state = 'idle';
      agentStatus.querySelector('.status-text').textContent = '主笔空闲中';
    }, 1200);
  }

  // 事件绑定
  btnIdeate.addEventListener('click', runIdeation);

  btnSend.addEventListener('click', sendMessage);
  chatInput.addEventListener('keydown', (e) => {
    if (e.key === 'Enter') sendMessage();
  });

  directionExpand.addEventListener('click', () => {
    directionPanel.classList.toggle('open');
    directionExpand.textContent = directionPanel.classList.contains('open') ? '▴' : '▾';
  });

  btnExpandAll.addEventListener('click', () => {
    outline.forEach((c) => (c.expanded = true));
    renderOutline();
  });

  btnCollapseAll.addEventListener('click', () => {
    outline.forEach((c) => (c.expanded = false));
    renderOutline();
  });

  drawerHandle.addEventListener('click', () => {
    tensionDrawer.classList.toggle('collapsed');
    if (!tensionDrawer.classList.contains('collapsed')) {
      setTimeout(drawTensionCurve, 260);
    }
  });

  btnAddCard.addEventListener('click', () => {
    const title = prompt('输入灵感标题');
    if (!title) return;
    inspirations.unshift({
      id: Date.now(),
      icon: '✏️',
      title,
      desc: '作者自建灵感卡，可后续补充细节或让主笔扩展。',
      type: '自定义',
      typeClass: 'plot',
      adopted: false,
    });
    renderInspirations();
  });

  toggleSkeleton.addEventListener('change', renderMessages);

  themeToggle.addEventListener('click', () => {
    document.body.classList.toggle('paper-theme');
    themeToggle.textContent = document.body.classList.contains('paper-theme') ? '纸' : '墨';
    drawTensionCurve();
  });

  // 初始化
  renderInspirations();
  renderOutline();
  renderMessages();

  window.addEventListener('load', drawTensionCurve);
  window.addEventListener('resize', drawTensionCurve);
})();
