/* ==========================================================================
   dashboard.js —— 概览页交互
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

  /* --- 视图切换：概览 ↔ 主笔共创工作台（maestro） --- */
  var overviewView = document.getElementById('overview-view');
  var maestroView = document.getElementById('maestro-view');

  function showView(viewName) {
    var showMaestro = viewName === 'maestro';
    if (overviewView) overviewView.style.display = showMaestro ? 'none' : 'flex';
    if (maestroView) maestroView.style.display = showMaestro ? 'flex' : 'none';
    // 工作台由隐藏转显示后，canvas 宽高从 0 恢复，需重绘张力曲线
    if (showMaestro) {
      setTimeout(function () { window.dispatchEvent(new Event('resize')); }, 60);
    }
  }

  document.querySelectorAll('.nav-item[data-view]').forEach(function (item) {
    item.addEventListener('click', function (e) {
      e.preventDefault();
      document.querySelectorAll('.nav-item').forEach(function (n) { n.classList.remove('active'); });
      item.classList.add('active');
      showView(item.getAttribute('data-view'));
    });
  });

  // 初始化：默认停在概览
  showView('overview');
})();
