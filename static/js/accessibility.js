/* ==========================================================================
   TUPAS accessibility & retention helpers
   - Dark-mode toggle (persisted in localStorage)
   - Read-aloud buttons using the Web Speech API
   - Session recap refresh after a progress mark
   ========================================================================== */

/* ---------- Theme toggle ---------- */
(function () {
    'use strict';

    const toggle = document.getElementById('tupas-theme-toggle');
    if (!toggle) {
        return;
    }

    const label = toggle.querySelector('[data-theme-label]');
    const icon = toggle.querySelector('[data-theme-icon]');

    function currentTheme() {
        return document.documentElement.getAttribute('data-theme') === 'dark' ? 'dark' : 'light';
    }

    function render() {
        const dark = currentTheme() === 'dark';
        if (label) {
            label.textContent = dark ? 'Light mode' : 'Dark mode';
        }
        if (icon) {
            icon.className = dark ? 'bi bi-sun-fill me-2' : 'bi bi-moon-stars-fill me-2';
        }
        toggle.setAttribute('aria-pressed', dark ? 'true' : 'false');
    }

    render();

    toggle.addEventListener('click', () => {
        const next = currentTheme() === 'dark' ? 'light' : 'dark';
        document.documentElement.setAttribute('data-theme', next);
        try {
            localStorage.setItem('tupas-theme', next);
        } catch (error) {
            /* Private browsing can block storage; the theme still applies */
        }
        render();
    });
})();

/* ---------- Read aloud ---------- */
(function () {
    'use strict';

    const buttons = document.querySelectorAll('[data-speak-target]');
    if (!buttons.length) {
        return;
    }

    if (!('speechSynthesis' in window) || typeof window.SpeechSynthesisUtterance === 'undefined') {
        buttons.forEach((button) => button.remove());
        return;
    }

    let active = null;

    function reset(button) {
        const label = button.querySelector('[data-speak-label]');
        const icon = button.querySelector('i');
        if (label) {
            label.textContent = 'Listen';
        }
        if (icon) {
            icon.className = 'bi bi-volume-up-fill me-1';
        }
        button.setAttribute('aria-pressed', 'false');
    }

    function stopActive() {
        if (active) {
            reset(active);
        }
        active = null;
        window.speechSynthesis.cancel();
    }

    buttons.forEach((button) => {
        button.hidden = false;
        button.setAttribute('aria-pressed', 'false');

        button.addEventListener('click', (event) => {
            event.preventDefault();
            event.stopPropagation();

            if (active === button) {
                stopActive();
                return;
            }

            stopActive();

            const target = document.getElementById(button.dataset.speakTarget);
            const text = target ? (target.innerText || target.textContent || '').trim() : '';
            if (!text) {
                return;
            }

            const utterance = new window.SpeechSynthesisUtterance(text);
            utterance.rate = 0.95;

            const done = () => {
                if (active === button) {
                    reset(button);
                    active = null;
                }
            };
            utterance.onend = done;
            utterance.onerror = done;

            active = button;
            button.setAttribute('aria-pressed', 'true');
            const label = button.querySelector('[data-speak-label]');
            const icon = button.querySelector('i');
            if (label) {
                label.textContent = 'Stop';
            }
            if (icon) {
                icon.className = 'bi bi-stop-fill me-1';
            }

            window.speechSynthesis.speak(utterance);
        });
    });

    window.addEventListener('beforeunload', () => window.speechSynthesis.cancel());
})();

/* ---------- Session recap ---------- */
(function () {
    'use strict';

    function updateSummary(summary) {
        const card = document.getElementById('study-session-summary');
        if (!card || !summary) {
            return;
        }

        const set = (selector, value) => {
            const node = card.querySelector(selector);
            if (node) {
                node.textContent = value;
            }
        };

        set('[data-session-reviewed]', summary.reviewed);
        set('[data-session-duration]', summary.duration_label);
        set('[data-session-got-it]', summary.got_it_percent + '%');
        set('[data-session-weak]', summary.weak_new);

        if (summary.completed) {
            card.classList.remove('d-none');
        }
    }

    document.addEventListener('tupas:progress', (event) => {
        updateSummary(event.detail ? event.detail.session : null);
    });
})();
