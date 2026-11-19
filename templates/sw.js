{% load static %}/*
 * TUPAS service worker.
 *
 * Served from /sw.js so its scope covers the whole app. The install step
 * precaches the local shell; runtime caching keeps CDN CSS, scripts and fonts
 * available after the first online visit. Navigations fall back to /offline/.
 */

const CACHE_VERSION = 'v9';
const STATIC_CACHE = `tupas-static-${CACHE_VERSION}`;
const RUNTIME_CACHE = `tupas-runtime-${CACHE_VERSION}`;
const OFFLINE_URL = '{% url "tupas:offline" %}';

const PRECACHE_URLS = [
    '{% static "css/style.css" %}',
    '{% static "js/tupas.js" %}',
    '{% static "manifest.json" %}',
    '{% static "icons/icon.svg" %}',
    '{% static "icons/icon-192.png" %}',
    '{% static "icons/icon-512.png" %}',
    '{% static "icons/icon-maskable-512.png" %}',
    '{% static "icons/apple-touch-icon.png" %}',
    '{% static "icons/tupas-mascot.png" %}',
    OFFLINE_URL,
];

const CDN_HOSTS = new Set([
    'cdn.jsdelivr.net',
    'cdnjs.cloudflare.com',
    'fonts.googleapis.com',
    'fonts.gstatic.com',
]);

function canCache(response) {
    return Boolean(response) && (response.ok || response.type === 'opaque');
}

function cacheResponse(cacheName, request, response) {
    if (!canCache(response)) {
        return;
    }
    const copy = response.clone();
    caches
        .open(cacheName)
        .then((cache) => cache.put(request, copy))
        .catch(() => {
            /* A failed background cache write must never break the response. */
        });
}

self.addEventListener('install', (event) => {
    event.waitUntil(
        caches
            .open(STATIC_CACHE)
            .then((cache) => cache.addAll(PRECACHE_URLS))
            .then(() => self.skipWaiting())
    );
});

self.addEventListener('activate', (event) => {
    event.waitUntil(
        caches
            .keys()
            .then((keys) => Promise.all(
                keys
                    .filter((key) => (
                        key.startsWith('tupas-')
                        && key !== STATIC_CACHE
                        && key !== RUNTIME_CACHE
                    ))
                    .map((key) => caches.delete(key))
            ))
            .then(() => self.clients.claim())
    );
});

self.addEventListener('fetch', (event) => {
    const { request } = event;

    if (request.method !== 'GET') {
        return;
    }

    const url = new URL(request.url);

    // Let Django admin bypass the worker entirely.
    if (url.pathname.startsWith('/admin/') || url.pathname === '/sw.js') {
        return;
    }

    // App pages: always prefer fresh data, fall back to the offline screen.
    if (request.mode === 'navigate') {
        event.respondWith((async () => {
            try {
                return await fetch(request);
            } catch (error) {
                const cachedPage = await caches.match(request);
                return (
                    cachedPage
                    || (await caches.match(OFFLINE_URL))
                    || new Response('TUPAS is offline.', {
                        status: 503,
                        headers: { 'Content-Type': 'text/plain; charset=utf-8' },
                    })
                );
            }
        })());
        return;
    }

    const isLocalStatic = (
        url.origin === self.location.origin
        && url.pathname.startsWith('/static/')
    );
    const isCdnAsset = CDN_HOSTS.has(url.hostname);

    // Local CSS/JS: network-first so style/topbar fixes actually ship.
    // CDN assets: cache-first (they are versioned by URL and rarely change).
    if (isLocalStatic) {
        event.respondWith((async () => {
            try {
                const fresh = await fetch(request);
                cacheResponse(STATIC_CACHE, request, fresh);
                return fresh;
            } catch (error) {
                const cached = await caches.match(request);
                if (cached) {
                    return cached;
                }
                throw new Error('Asset unavailable while offline.');
            }
        })());
        return;
    }

    if (isCdnAsset) {
        event.respondWith((async () => {
            const cached = await caches.match(request);

            const network = fetch(request)
                .then((response) => {
                    cacheResponse(RUNTIME_CACHE, request, response);
                    return response;
                })
                .catch(() => null);

            const response = cached || (await network);
            if (response) {
                return response;
            }
            throw new Error('Asset unavailable while offline.');
        })());
        return;
    }

    // Anything else: network first, then whatever happens to be cached.
    event.respondWith(
        fetch(request).catch(async () => {
            const cached = await caches.match(request);
            return cached || (await caches.match(OFFLINE_URL)) || Response.error();
        })
    );
});

self.addEventListener('message', (event) => {
    if (event.data === 'SKIP_WAITING') {
        self.skipWaiting();
    }
});
