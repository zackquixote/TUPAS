/* ==========================================================================
   TUPAS accessibility & retention helpers
   - Dark-mode toggle (persisted in localStorage)
   - Read-aloud buttons using the Web Speech API
   - Session recap refresh after a progress mark
   ========================================================================== */

/* ---------- Theme toggle ---------- */
(function () {
    'use strict';

    function currentTheme() {
        return document.documentElement.getAttribute('data-theme') === 'dark' ? 'dark' : 'light';
    }

    function saveTheme(next) {
        try {
            localStorage.setItem('tupas-theme', next);
        } catch (error) {
            /* Private browsing can block storage; the theme still applies */
        }
    }

    function buildToggle() {
        const button = document.createElement('button');
        button.type = 'button';
        button.className = 'tupas-theme-toggle-admin';
        button.setAttribute('aria-label', 'Toggle dark mode');
        button.innerHTML = '<i class="bi bi-moon-stars-fill"></i>';
        return button;
    }

    function wireToggle(toggle) {
        const icon = toggle.querySelector('[data-theme-icon], i');
        const label = toggle.querySelector('[data-theme-label]');

        function render() {
            const dark = currentTheme() === 'dark';
            if (label) {
                label.textContent = dark ? 'Light mode' : 'Dark mode';
            }
            if (icon) {
                icon.className = dark ? 'bi bi-sun-fill' : 'bi bi-moon-stars-fill';
            }
            toggle.setAttribute('aria-pressed', dark ? 'true' : 'false');
            toggle.title = dark ? 'Switch to light mode' : 'Switch to dark mode';
        }

        render();
        toggle.addEventListener('click', () => {
            const next = currentTheme() === 'dark' ? 'light' : 'dark';
            document.documentElement.setAttribute('data-theme', next);
            saveTheme(next);
            render();
        });
    }

    // Existing TUPAS navbar toggle (base.html).
    const toggle = document.getElementById('tupas-theme-toggle');
    if (toggle) {
        wireToggle(toggle);
    }

    // Pages without the TUPAS navbar (e.g. admin) get a floating toggle.
    if (!toggle && !document.querySelector('#theme-toggle-admin')) {
        const button = buildToggle();
        button.id = 'theme-toggle-admin';
        button.style.cssText =
            'position: fixed; right: 20px; bottom: 20px; z-index: 100; ' +
            'border-radius: 50%; width: 50px; height: 50px; background: #a8ee00; ' +
            'border: none; color: #080e2c; font-size: 1.2rem; cursor: pointer; ' +
            'box-shadow: 0 2px 8px rgba(0,0,0,0.2);';
        document.body.appendChild(button);
        wireToggle(button);
    }
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
