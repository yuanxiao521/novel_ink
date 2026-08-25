/* ==========================================================================
   characters.js —— 人物页交互
   ========================================================================== */

(function () {
  var root = document.documentElement;

  /* --- 纸墨主题切换 --- */
  var themeToggle = document.getElementById('theme-toggle');
  var themeLabel = document.getElementById('theme-label');

  function setTheme(theme) {
    root.dataset.theme = theme;
    if (themeLabel) {
      themeLabel.textContent = theme === 'paper' ? '纸' : '墨';
    }
  }

  if (themeToggle) {
    themeToggle.addEventListener('click', function () {
      var current = root.dataset.theme;
      var next = (current === 'ink' || current === 'dark') ? 'paper' : 'ink';
      setTheme(next);
    });
  }

  /* --- Tab 切换 --- */
  var tabs = document.querySelectorAll('.char-tab');
  var panes = document.querySelectorAll('.tab-pane');

  tabs.forEach(function (tab) {
    tab.addEventListener('click', function () {
      var target = tab.getAttribute('data-tab');

      tabs.forEach(function (t) { t.classList.remove('active'); });
      panes.forEach(function (p) { p.classList.remove('active'); });

      tab.classList.add('active');
      var targetPane = document.querySelector('.tab-pane[data-pane="' + target + '"]');
      if (targetPane) {
        targetPane.classList.add('active');
      }
    });
  });

  /* --- 角色列表切换 --- */
  var charItems = document.querySelectorAll('.char-list-item');

  charItems.forEach(function (item) {
    item.addEventListener('click', function () {
      charItems.forEach(function (i) { i.classList.remove('active'); });
      item.classList.add('active');
      // 原型阶段：只切换高亮，不加载真实数据
    });
  });

  /* --- 信念筛选 --- */
  var beliefFilters = document.querySelectorAll('.belief-filter .filter-chip');

  beliefFilters.forEach(function (chip) {
    chip.addEventListener('click', function () {
      beliefFilters.forEach(function (c) { c.classList.remove('active'); });
      chip.classList.add('active');
    });
  });

  /* --- 记忆筛选 --- */
  var memoryFilters = document.querySelectorAll('.memory-filter .filter-chip');

  memoryFilters.forEach(function (chip) {
    chip.addEventListener('click', function () {
      memoryFilters.forEach(function (c) { c.classList.remove('active'); });
      chip.classList.add('active');
    });
  });
})();
