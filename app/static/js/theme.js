// IPMI-Web Theme Management (Dark / Light)
(function () {
    const STORAGE_KEY = 'ipmi_web_theme';
    
    function getPreferredTheme() {
        const stored = localStorage.getItem(STORAGE_KEY);
        if (stored === 'dark' || stored === 'light') {
            return stored;
        }
        return window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
    }

    function applyTheme(theme) {
        document.documentElement.setAttribute('data-theme', theme);
        updateToggleButton(theme);
    }

    function updateToggleButton(theme) {
        const btn = document.getElementById('theme-toggle-btn');
        if (!btn) return;
        const isDark = theme === 'dark';
        btn.setAttribute('aria-label', isDark ? 'Переключить на светлую тему' : 'Переключить на тёмную тему');
        btn.setAttribute('title', isDark ? 'Светлая тема' : 'Тёмная тема');
        const iconSun = btn.querySelector('.icon-sun');
        const iconMoon = btn.querySelector('.icon-moon');
        if (iconSun && iconMoon) {
            iconSun.style.display = isDark ? 'inline-block' : 'none';
            iconMoon.style.display = isDark ? 'none' : 'inline-block';
        }
    }

    window.toggleTheme = function () {
        const current = document.documentElement.getAttribute('data-theme') || getPreferredTheme();
        const next = current === 'dark' ? 'light' : 'dark';
        localStorage.setItem(STORAGE_KEY, next);
        applyTheme(next);
    };

    // Apply immediately to prevent flash
    const initial = getPreferredTheme();
    document.documentElement.setAttribute('data-theme', initial);

    document.addEventListener('DOMContentLoaded', () => {
        applyTheme(document.documentElement.getAttribute('data-theme') || initial);
        const btn = document.getElementById('theme-toggle-btn');
        if (btn) {
            btn.addEventListener('click', window.toggleTheme);
        }
    });
})();
