/*
  Service worker.

  Served from /sw.js rather than /static/js/sw.js: a worker's scope is the
  directory it is served from, so one under /static/ could never control the
  pages it is meant to serve. See api/pages.py.

  What this is for is installability and an honest offline page -- NOT offline
  chat. LLeMbas renders every page on the server, so a cached conversation
  would be a snapshot that silently went stale, and a cached one belonging to
  whoever was signed in last. The shell is cached; nothing with a user in it is.

  The cache is versioned from the query string the registration adds
  (/sw.js?v=<app version>), so a release invalidates it with no separate step.
*/
"use strict";

var VERSION = new URL(self.location).searchParams.get("v") || "dev";
var CACHE = "lembas-" + VERSION;

/* The shell: everything needed to draw a page, plus the page shown when there
   is no network. Deliberately no HTML but /offline -- see above. */
var SHELL = [
  "/offline",
  "/static/css/tokens.css",
  "/static/css/app.css",
  "/static/css/chat.css",
  "/static/css/admin.css",
  "/static/js/app.js",
  "/static/js/ui.js",
  "/static/js/inplace.js",
  "/static/js/dnd.js",
  "/static/js/commands.js",
  "/static/js/composer.js",
  "/static/js/audio.js",
  "/static/js/steps.js",
  "/static/js/terminal.js",
  // Deliberately not the three xterm files below it: ~300KB precached on every
  // install, for a panel most people never open, to spare one fetch from the
  // people who do. The runtime branch caches them the first time it is opened.
  "/static/vendor/htmx.min.js",
  "/static/vendor/htmx-ext-sse.js",
  "/static/vendor/alpine.min.js",
  "/static/img/favicon.svg",
  "/static/img/logo-mark.svg",
  "/static/img/icon-192.png",
  "/static/img/icon-512.png",
  // The two a device reaches for when the network is not there: the maskable
  // one is what every Android launcher crops, and the Apple one is the home
  // screen. Both were absent from this list while the two nothing crops were
  // in it.
  "/static/img/icon-maskable-512.png",
  "/static/img/apple-touch-icon-180.png",
];

/* The URL a page will actually ask for.

   Every `/static/` link carries `?v=<release>` -- see `templating.asset` -- and
   `caches.match` compares the whole URL, query included. So precaching the bare
   path would fill the cache with entries no page ever requests, and every asset
   would go to the network on every load while looking perfectly cached.

   `/offline` is a route rather than an asset and is left alone. */
function versioned(path) {
  return path.indexOf("/static/") === 0 ? path + "?v=" + VERSION : path;
}

self.addEventListener("install", function (event) {
  event.waitUntil(
    caches.open(CACHE).then(function (cache) {
      // addAll is all-or-nothing: one 404 would leave the worker uninstalled
      // and the whole feature silently off, so each entry is added on its own.
      return Promise.all(
        SHELL.map(function (path) {
          return cache.add(new Request(versioned(path), { cache: "reload" }))
            .catch(function () {});
        })
      );
    })
  );
  /* Deliberately NOT skipWaiting() here.

     It used to, unconditionally, together with clients.claim() below -- so a
     release took over every open tab the moment it was installed, while the
     cache those tabs were reading from was being emptied underneath them. A
     page could end up drawing itself from two releases at once, and nothing
     said so.

     The new worker waits instead, the page is told, and the reader decides.
     `messages/SKIP_WAITING` below is how they say yes. A worker that is never
     activated costs a few hundred kilobytes and is replaced by the next one. */
});

/* The page asking to be taken over now. The only message this worker answers,
   and it does exactly one thing, because a message channel into a service
   worker is a thing any script on the origin can post to. */
self.addEventListener("message", function (event) {
  if (event.data && event.data.type === "SKIP_WAITING") self.skipWaiting();
});

self.addEventListener("activate", function (event) {
  event.waitUntil(
    /* Without this, every navigation waits for this worker to start before its
       request is even made -- which on a cold phone is the difference between
       a page and a pause. The navigate branch below is a plain fetch, so the
       preloaded response is used simply by preferring it when it exists. */
    (self.registration.navigationPreload
      ? self.registration.navigationPreload.enable().catch(function () {})
      : Promise.resolve()
    ).then(function () {
      return caches.keys();
    }).then(function (names) {
      return Promise.all(
        names.map(function (name) {
          if (name !== CACHE && name.indexOf("lembas-") === 0) return caches.delete(name);
          return null;
        })
      );
    }).then(function () { return self.clients.claim(); })
  );
});

/* Paths this worker must never touch. /api/ carries the reply stream, the
   unread poll, uploads and attachment downloads; /auth/ and /admin/ carry
   credentials and settings. A cached response on any of them is at best stale
   and at worst somebody else's. */
function isExcluded(url) {
  return url.pathname.indexOf("/api/") === 0 ||
         url.pathname.indexOf("/auth/") === 0 ||
         url.pathname.indexOf("/admin/") === 0 ||
         url.pathname === "/sw.js";
}

self.addEventListener("fetch", function (event) {
  var request = event.request;
  if (request.method !== "GET") return;

  var url = new URL(request.url);
  if (url.origin !== self.location.origin) return;
  if (isExcluded(url)) return;

  /* A reply arrives as an endless event stream. Passing one through a worker
     is the reliable way to turn a streaming answer into a single delivery at
     the end, or into nothing at all -- so it is left entirely alone. */
  if ((request.headers.get("accept") || "").indexOf("text/event-stream") !== -1) return;

  if (request.mode === "navigate") {
    event.respondWith(
      Promise.resolve(event.preloadResponse)
        .then(function (preloaded) { return preloaded || fetch(request); })
        .catch(function () { return caches.match("/offline"); })
    );
    return;
  }

  // Static assets: serve from cache, refresh in the background. They are
  // versioned by the cache name, so a stale one only lasts until the next
  // release.
  event.respondWith(
    caches.match(request).then(function (hit) {
      var live = fetch(request).then(function (response) {
        if (response && response.ok) {
          var copy = response.clone();
          caches.open(CACHE).then(function (cache) { cache.put(request, copy); });
        }
        return response;
      }).catch(function () { return hit; });
      return hit || live;
    })
  );
});

/*
  Notifications that arrive with no page open.

  This is the only part of LLeMbas that runs when nothing of ours is on screen,
  and it is why web push exists here at all: everything else is polled by an open
  page, which is exactly what is missing at seven in the morning when a schedule
  fires and the laptop is shut.

  The payload was encrypted end to end (see services/push.py), so what arrives
  here is the first plaintext anybody but this browser and that server has seen.
*/
self.addEventListener("push", function (event) {
  var payload = {};
  try {
    payload = event.data ? event.data.json() : {};
  } catch (error) {
    payload = { title: "LLeMbas", body: "Something new arrived." };
  }

  event.waitUntil(
    self.clients.matchAll({ type: "window", includeUncontrolled: true }).then(function (clients) {
      /* Somebody is looking at it. The page has its own toast and its own
         count in the tab title, and a system notification on top of those is
         the same news three times -- which is how notifications come to be
         switched off for good. This is the only place that can be known: the
         server cannot see whether a window is focused, and the page cannot see
         a push that it did not receive. */
      for (var i = 0; i < clients.length; i++) {
        if (clients[i].focused) return null;
      }
      return self.registration.showNotification(payload.title || "LLeMbas", {
        body: payload.body || "",
        /* One at a time. A browser left closed all day must not be opened to a
           stack of twelve. */
        tag: "lembas-" + (payload.kind || "unread"),
        renotify: true,
        icon: "/static/img/icon-192.png",
        /* A badge is drawn as a *mask* in the status bar -- the device keeps
           the alpha and throws the colour away. The full-colour 192 is opaque
           to its edges, so what Android rendered was a solid grey square. The
           leaf has transparency, so it survives being masked. */
        badge: "/static/img/badge-72.png",
        data: { url: payload.url || "/" },
      });
    })
  );
});

/*
  A browser may replace a subscription on its own -- a push service expiring a
  key, a browser upgrade. When it does, the endpoint this server holds stops
  working and nothing anywhere says so: notifications simply stop. The event
  fires exactly once, at the moment of the swap, and it is the only chance to
  hear about it.

  Re-subscribing needs the server's public key, which this worker does not hold,
  so it asks the same endpoint the page does.
*/
self.addEventListener("pushsubscriptionchange", function (event) {
  event.waitUntil(
    fetch("/api/push/key")
      .then(function (response) { return response.ok ? response.json() : null; })
      .then(function (data) {
        if (!data || !data.key) return null;
        return self.registration.pushManager.subscribe({
          userVisibleOnly: true,
          applicationServerKey: Uint8Array.from(
            atob(data.key.replace(/-/g, "+").replace(/_/g, "/")),
            function (c) { return c.charCodeAt(0); }
          ),
        });
      })
      .then(function (subscription) {
        if (!subscription) return null;
        return fetch("/api/push/subscribe", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(subscription.toJSON()),
        });
      })
      .catch(function () { /* Nothing here can ask a person for help. */ })
  );
});

/*
  Clicking one.

  Focus a window that is already open rather than opening a second: somebody
  with LLeMbas open in a tab wants that tab, and `openWindow` would give them
  two. `navigate` moves the one they have to whatever arrived.
*/
self.addEventListener("notificationclick", function (event) {
  event.notification.close();
  var target = (event.notification.data && event.notification.data.url) || "/";

  event.waitUntil(
    self.clients.matchAll({ type: "window", includeUncontrolled: true }).then(function (clients) {
      for (var i = 0; i < clients.length; i++) {
        var client = clients[i];
        if (new URL(client.url).origin !== self.location.origin) continue;
        return client.focus().then(function (focused) {
          return focused && focused.navigate ? focused.navigate(target) : focused;
        });
      }
      return self.clients.openWindow(target);
    })
  );
});
