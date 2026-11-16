/* Service worker: offline shell, photo cache, push notifications. */
const SHELL = "speerpunt-shell-v4";
const PHOTOS = "speerpunt-photos-v2";

self.addEventListener("install", (event) => {
  event.waitUntil(caches.open(SHELL).then((c) => c.addAll(["/static/icons/icon-192.png"])).then(() => self.skipWaiting()));
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(keys.filter((k) => ![SHELL, PHOTOS].includes(k)).map((k) => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener("fetch", (event) => {
  const req = event.request;
  if (req.method !== "GET") return;
  const url = new URL(req.url);
  if (url.origin !== location.origin) return;
  if (url.pathname.startsWith("/api/")) return; // always live

  // Photos never change: cache first, saves roaming data.
  if (url.pathname.startsWith("/uploads/")) {
    event.respondWith(
      caches.open(PHOTOS).then(async (cache) => {
        const hit = await cache.match(req);
        if (hit) return hit;
        const res = await fetch(req);
        if (res.ok) cache.put(req, res.clone());
        return res;
      })
    );
    return;
  }

  // Static files: stale while revalidate.
  if (url.pathname.startsWith("/static/")) {
    event.respondWith(
      caches.open(SHELL).then(async (cache) => {
        const hit = await cache.match(req);
        const fresh = fetch(req).then((res) => { if (res.ok) cache.put(req, res.clone()); return res; }).catch(() => hit);
        return hit || fresh;
      })
    );
    return;
  }

  // The app page: network first, fall back to the last copy when offline.
  if (req.mode === "navigate" && url.pathname === "/") {
    event.respondWith(
      fetch(req)
        .then(async (res) => {
          // alleen de spel-shell zelf cacheen; voor ingelogde bezoekers is / de login/claim-pagina
          if (res.ok && !res.redirected) {
            const copy = res.clone();
            const html = await copy.text();
            if (html.includes('<body class="app')) caches.open(SHELL).then((c) => c.put("/", copy));
          }
          return res;
        })
        .catch(() => caches.match("/"))
    );
  }
});

self.addEventListener("push", (event) => {
  let data = { title: "Bingo", body: "", url: "/" };
  try { data = { ...data, ...event.data.json() }; } catch { /* plain text */ }
  event.waitUntil(
    self.registration.showNotification(data.title, {
      body: data.body,
      icon: "/static/icons/icon-192.png",
      badge: "/static/icons/badge-96.png",
      data: { url: data.url },
      vibrate: [80, 40, 80],
    })
  );
});

self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  const target = (event.notification.data && event.notification.data.url) || "/";
  event.waitUntil(
    self.clients.matchAll({ type: "window", includeUncontrolled: true }).then((list) => {
      for (const c of list) { if ("focus" in c) return c.focus(); }
      return self.clients.openWindow(target);
    })
  );
});
