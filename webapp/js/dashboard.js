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
})();
