(function () {
    'use strict';

    function getCsrfToken() {
        const match = document.cookie.match(/(^|;\s*)csrftoken=([^;]+)/);
        if (match) {
            return decodeURIComponent(match[2]);
        }
        const input = document.querySelector('[name=csrfmiddlewaretoken]');
        return input ? input.value : '';
    }

    function cssEscape(value) {
        if (window.CSS && typeof window.CSS.escape === 'function') {
            return window.CSS.escape(value);
        }
        return String(value).replace(/["\\]/g, '\\$&');
    }

    function toast(message, variant) {
        const container = document.getElementById('tupas-toast-container');
        if (!container || typeof bootstrap === 'undefined') {
            return;
        }
        const element = document.createElement('div');
        element.className = 'toast align-items-center text-bg-' + (variant || 'dark') + ' border-0 mb-2';
        element.setAttribute('role', 'status');
        element.setAttribute('aria-live', 'polite');
        element.setAttribute('aria-atomic', 'true');
        element.innerHTML =
            '<div class="d-flex">' +
            '<div class="toast-body"></div>' +
            '<button type="button" class="btn-close btn-close-white me-2 m-auto" data-bs-dismiss="toast" aria-label="Close"></button>' +
            '</div>';
        element.querySelector('.toast-body').textContent = message;
        container.appendChild(element);
        const instance = new bootstrap.Toast(element, { delay: 2800 });
        element.addEventListener('hidden.bs.toast', () => element.remove());
        instance.show();
    }

    window.tupasToast = toast;

    async function sendProgress(button) {
        const url = button.dataset.progressUrl;
        const status = button.dataset.itemStatus;
        if (!url || !status || button.dataset.loading === 'true') {
            return null;
        }

        const originalHtml = button.innerHTML;
        button.dataset.loading = 'true';
        button.disabled = true;
        button.innerHTML = '<span class="spinner-border spinner-border-sm me-1" aria-hidden="true"></span>Saving';

        try {
            const confidence = button.dataset.itemConfidence || '';
            const status = button.dataset.itemStatus || '';
            const body = confidence
                ? { confidence: confidence, status: status }
                : { status: status };
            const response = await fetch(url, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'X-CSRFToken': getCsrfToken(),
                    'X-Requested-With': 'XMLHttpRequest'
                },
                body: JSON.stringify(body)
            });

            const payload = await response.json().catch(() => ({}));
            if (!response.ok || !payload.ok) {
                throw new Error(payload.error || 'Could not save progress.');
            }

            const group = button.closest('.progress-actions');
            const itemId = group ? group.dataset.itemId : '';

            if (itemId) {
                document.querySelectorAll('[data-item-status="' + cssEscape(itemId) + '"]').forEach((badge) => {
                    badge.textContent = payload.status_label;
                    badge.classList.remove('d-none', 'badge-mastered', 'badge-review', 'badge-soft');
                    badge.classList.add(payload.status === 'MASTERED' ? 'badge-mastered' : 'badge-review');
                });
            }

            document.dispatchEvent(new CustomEvent('tupas:progress', {
                detail: Object.assign({ itemId: itemId, button: button }, payload)
            }));

            toast(
                payload.status === 'MASTERED'
                    ? 'Marked as Got It. +' + (payload.xp_awarded || 10) + ' XP'
                    : 'Marked for Study Again. +' + (payload.xp_awarded || 5) + ' XP',
                'success',
            );
            return payload;
        } catch (error) {
            toast(error.message || 'Could not save progress. Please try again.', 'danger');
            return null;
        } finally {
            button.dataset.loading = 'false';
            button.disabled = false;
            button.innerHTML = originalHtml;
        }
    }

    document.addEventListener('click', (event) => {
        const button = event.target.closest('[data-progress-url]');
        if (button) {
            event.preventDefault();
            sendProgress(button);
        }
    });
})();

/* ---------- PWA install experience ---------- */
(function () {
    'use strict';

    const installItem = document.getElementById('tupas-install-item');
    const installButton = document.getElementById('tupas-install-btn');
    const installHint = document.getElementById('tupas-install-hint');

    const isStandalone = () => (
        window.matchMedia('(display-mode: standalone)').matches
        || window.navigator.standalone === true
    );
    const isIos = (
        /iphone|ipad|ipod/i.test(navigator.userAgent)
        || (navigator.platform === 'MacIntel' && navigator.maxTouchPoints > 1)
    );
    const isSafari = (
        /safari/i.test(navigator.userAgent)
        && !/chrome|crios|fxios|edgios|android/i.test(navigator.userAgent)
    );

    let deferredPrompt = null;

    function revealInstallButton() {
        if (!installItem || !installButton || isStandalone()) {
            return;
        }
        installItem.classList.remove('d-none');
        installButton.classList.remove('d-none');
    }

    function hideInstallButton() {
        if (installItem) {
            installItem.classList.add('d-none');
        }
    }

    function showIosInstructions() {
        if (typeof bootstrap !== 'undefined' && installHint) {
            bootstrap.Modal.getOrCreateInstance(installHint).show();
            return;
        }
        window.alert('Tap the Share button in Safari, then choose "Add to Home Screen".');
    }

    window.addEventListener('beforeinstallprompt', (event) => {
        // Custom auth pages have no navbar button; let the browser show its
        // own install UI there instead of swallowing the event.
        if (!installItem || !installButton) {
            return;
        }
        event.preventDefault();
        deferredPrompt = event;
        revealInstallButton();
    });

    if (installButton) {
        installButton.addEventListener('click', async () => {
            if (deferredPrompt) {
                const promptEvent = deferredPrompt;
                deferredPrompt = null;
                promptEvent.prompt();
                try {
                    const choice = await promptEvent.userChoice;
                    if (!choice || choice.outcome !== 'accepted') {
                        hideInstallButton();
                    }
                } catch (error) {
                    hideInstallButton();
                }
                return;
            }

            if (isIos && isSafari) {
                showIosInstructions();
            }
        });
    }

    window.addEventListener('appinstalled', () => {
        deferredPrompt = null;
        hideInstallButton();
        if (window.tupasToast) {
            window.tupasToast('TUPAS is installed. Open it from your home screen!', 'success');
        }
    });

    // iOS Safari never fires beforeinstallprompt, so surface manual instructions.
    if (isIos && isSafari && !isStandalone()) {
        revealInstallButton();
    }

    if ('serviceWorker' in navigator) {
        window.addEventListener('load', () => {
            navigator.serviceWorker.register('/sw.js').then((registration) => {
                registration.addEventListener('updatefound', () => {
                    const newWorker = registration.installing;
                    if (!newWorker) {
                        return;
                    }
                    newWorker.addEventListener('statechange', () => {
                        if (newWorker.state === 'installed'
                            && navigator.serviceWorker.controller) {
                            if (window.tupasToast) {
                                window.tupasToast(
                                    'A new version of TUPAS is available. Refresh to update.',
                                    'info'
                                );
                            }
                        }
                    });
                });
            }).catch((error) => {
                console.warn('TUPAS service worker registration failed.', error);
            });
        });
    }
})();

/* ==========================================================================
   Gamification UX: haptics, Pomodoro, study reminders, notes autosave
   ========================================================================== */
(function () {
    'use strict';

    window.tupasHaptic = function (pattern) {
        try {
            if (typeof navigator !== 'undefined' && typeof navigator.vibrate === 'function') {
                navigator.vibrate(pattern || 10);
            }
        } catch (error) {
            /* Haptics are best-effort. */
        }
    };

    document.addEventListener('click', function (event) {
        const target = event.target.closest(
            '[data-progress-url], .flip-card, .highlight-reveal, .listen-btn, .tupas-bottom-tab'
        );
        if (target) {
            window.tupasHaptic(10);
        }
    }, true);

    /* ---------------- Pomodoro (sessionStorage) ---------------- */
    const fab = document.getElementById('pomodoro-fab');
    const panel = document.getElementById('tupas-pomodoro');
    const timeEl = document.getElementById('pomodoro-time');
    const toggleBtn = document.getElementById('pomodoro-toggle');
    const modeBtn = document.getElementById('pomodoro-mode');
    const closeBtn = document.getElementById('pomodoro-close');
    const FOCUS_SECONDS = 25 * 60;
    const BREAK_SECONDS = 5 * 60;

    function loadPomodoro() {
        try {
            return JSON.parse(sessionStorage.getItem('tupas-pomodoro') || '{}');
        } catch (error) {
            return {};
        }
    }

    function savePomodoro(state) {
        try {
            sessionStorage.setItem('tupas-pomodoro', JSON.stringify(state));
        } catch (error) {
            /* Storage unavailable. */
        }
    }

    let state = Object.assign(
        { mode: 'focus', remaining: FOCUS_SECONDS, running: false, endsAt: null },
        loadPomodoro(),
    );

    function renderTime() {
        if (!timeEl) return;
        const total = Math.max(0, state.remaining);
        const minutes = String(Math.floor(total / 60)).padStart(2, '0');
        const seconds = String(total % 60).padStart(2, '0');
        timeEl.textContent = minutes + ':' + seconds;
        if (toggleBtn) {
            toggleBtn.textContent = state.running ? 'Pause' : 'Start';
        }
        if (modeBtn) {
            modeBtn.textContent = state.mode === 'focus' ? 'Break' : 'Focus';
            modeBtn.dataset.mode = state.mode;
        }
        document.documentElement.setAttribute(
            'data-pomodoro-mode',
            state.mode,
        );
    }

    function tick() {
        if (!state.running) return;
        const now = Date.now();
        state.remaining = Math.max(0, Math.round((state.endsAt - now) / 1000));
        if (state.remaining === 0) {
            state.running = false;
            window.tupasHaptic([80, 40, 80]);
            if (window.tupasToast) {
                window.tupasToast(
                    state.mode === 'focus' ? 'Focus done! Take a 5-minute break.' : 'Break over. Back to focus!',
                    'success',
                );
            }
            state.mode = state.mode === 'focus' ? 'break' : 'focus';
            state.remaining = state.mode === 'focus' ? FOCUS_SECONDS : BREAK_SECONDS;
        }
        savePomodoro(state);
        renderTime();
    }

    if (panel) {
        if (state.running && state.endsAt) {
            state.remaining = Math.max(0, Math.round((state.endsAt - Date.now()) / 1000));
        }
        renderTime();
        setInterval(tick, 500);
    }

    if (fab && panel) {
        fab.addEventListener('click', () => {
            panel.hidden = !panel.hidden;
        });
    }
    if (closeBtn && panel) {
        closeBtn.addEventListener('click', () => {
            panel.hidden = true;
        });
    }
    if (toggleBtn) {
        toggleBtn.addEventListener('click', () => {
            state.running = !state.running;
            if (state.running) {
                state.endsAt = Date.now() + state.remaining * 1000;
            }
            savePomodoro(state);
            renderTime();
        });
    }
    if (modeBtn) {
        modeBtn.addEventListener('click', () => {
            state.mode = state.mode === 'focus' ? 'break' : 'focus';
            state.remaining = state.mode === 'focus' ? FOCUS_SECONDS : BREAK_SECONDS;
            state.running = false;
            savePomodoro(state);
            renderTime();
        });
    }

    /* ---------------- Sidebar drawer (works with or without Bootstrap) ---------------- */
    const sidebar = document.getElementById('tupasSidebar');
    const burger = document.querySelector('.tupas-burger');

    function openSidebar() {
        if (!sidebar) return;
        if (window.bootstrap && window.bootstrap.Offcanvas) {
            window.bootstrap.Offcanvas.getOrCreateInstance(sidebar).show();
            return;
        }
        sidebar.classList.add('show');
        document.body.style.overflow = 'hidden';
        let backdrop = document.querySelector('.offcanvas-backdrop');
        if (!backdrop) {
            backdrop = document.createElement('div');
            backdrop.className = 'offcanvas-backdrop';
            backdrop.addEventListener('click', closeSidebar);
            document.body.appendChild(backdrop);
        }
    }

    function closeSidebar() {
        if (!sidebar) return;
        if (window.bootstrap && window.bootstrap.Offcanvas) {
            const inst = window.bootstrap.Offcanvas.getInstance(sidebar);
            if (inst) {
                inst.hide();
            } else {
                sidebar.classList.remove('show');
            }
        } else {
            sidebar.classList.remove('show');
        }
        document.body.style.overflow = '';
        document.querySelectorAll('.offcanvas-backdrop').forEach((el) => el.remove());
    }

    if (burger && sidebar) {
        burger.addEventListener('click', (event) => {
            event.preventDefault();
            event.stopPropagation();
            if (sidebar.classList.contains('show')) {
                closeSidebar();
            } else {
                openSidebar();
            }
        });
        sidebar.querySelectorAll('[data-bs-dismiss="offcanvas"]').forEach((btn) => {
            btn.addEventListener('click', closeSidebar);
        });
    }

    /* ---------------- Daily study reminder ---------------- */
    const reminderToggle = document.getElementById('study-reminder-toggle');
    if (reminderToggle) {
        if (!('Notification' in window)) {
            reminderToggle.disabled = true;
            reminderToggle.checked = false;
        } else {
            reminderToggle.checked = localStorage.getItem('tupas-reminder') === 'on';
            reminderToggle.addEventListener('change', async () => {
                if (!reminderToggle.checked) {
                    localStorage.setItem('tupas-reminder', 'off');
                    return;
                }
                let permission = Notification.permission;
                if (permission === 'default') {
                    permission = await Notification.requestPermission();
                }
                if (permission !== 'granted') {
                    reminderToggle.checked = false;
                    localStorage.setItem('tupas-reminder', 'off');
                    if (window.tupasToast) {
                        window.tupasToast('Notification permission was denied.', 'danger');
                    }
                    return;
                }
                localStorage.setItem('tupas-reminder', 'on');
                new Notification('TUPAS reminders are on', {
                    body: 'We will nudge you to review a few cards every day.',
                });
            });
        }
    }

    /* ---------------- AI notes autosave ---------------- */
    const notesField = document.getElementById('id_notes');
    if (notesField) {
        const key = 'tupas-notes-draft';
        const saved = localStorage.getItem(key);
        if (saved && !notesField.value.trim()) {
            notesField.value = saved;
        }
        let saveTimer = null;
        notesField.addEventListener('input', () => {
            clearTimeout(saveTimer);
            saveTimer = setTimeout(() => {
                try {
                    localStorage.setItem(key, notesField.value);
                } catch (error) {
                    /* Ignore quota errors. */
                }
            }, 400);
        });
        const generatorForm = notesField.closest('form');
        if (generatorForm) {
            generatorForm.addEventListener('submit', () => {
                try {
                    localStorage.removeItem(key);
                } catch (error) {
                    /* Ignore. */
                }
            });
        }
    }
})();
