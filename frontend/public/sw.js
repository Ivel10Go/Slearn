// v6: der Service Worker kann jetzt Push annehmen und Benachrichtigungen
// anzeigen. Ohne Bump liefert der Cache die alte Fassung aus, in der es nur
// Caching gab - die Lernenden wuerden eine Erinnerung bekommen, auf deren Klick
// nichts passiert. Der `activate`-Handler raeumt die alte Fassung dann ab.
//
// Vier Dinge, die hier bewusst so und nicht anders gebaut sind:
//
// 1. **Push und Caching sind zwei Nachrichtenarten.** Der `push`-Handler unten
//    fasst den Cache nicht an. Wer eine Push-Nachricht bekommt, muss nicht
//    damit rechnen, dass sein App-Shell veraltet ist - und umgekehrt darf ein
//    Cache-Fehler keine Erinnerung verschlucken.
// 2. **Kein `clients.openWindow()` bei jedem Push.** Der Worker kann keinen Link
//    anklicken; er nutzt `clients.matchAll()` und `client.focus()`. Erst wenn
//    wirklich kein Fenster offen ist, wird eines geoeffnet - sonst haette die
//    Lernende pro Erinnerung einen neuen Tab.
// 3. **Kein Schluessel in dieser Datei.** Der oeffentliche Application Server Key
//    kommt aus `GET /v1/notifications/vapid` und wird der Seite per
//    `postMessage` gereicht. Er ist kein Geheimnis, aber er gehoert zum
//    Deployment und nicht in eine versionierte Datei. `PUSH_KEY` gibt ihn an
//    weitere Tabs weiter, damit nicht jeder Tab die Umgebung neu abfragt.
// 4. **`showNotification` wird nur aufgerufen, wenn der Browser gefragt hat.**
//    Ein `Notification` ohne Erlaubnis ist ein Versuch, der mit einer Ausnahme
//    endet - im Worker endet sie im Nirgends.
const CACHE_NAME = "lernraum-static-v12";
const APP_SHELL = ["/offline/", "/manifest.webmanifest", "/icons/icon-192.png", "/icons/icon-512.png"];

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches
      .open(CACHE_NAME)
      .then((cache) => cache.addAll(APP_SHELL))
      .then(() => self.skipWaiting()),
  );
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches
      .keys()
      .then((keys) => Promise.all(keys.filter((key) => key !== CACHE_NAME).map((key) => caches.delete(key))))
      .then(() => self.clients.claim()),
  );
});

function isStaticAsset(url) {
  return url.pathname.startsWith("/_next/static/") || url.pathname.startsWith("/icons/") || url.pathname === "/manifest.webmanifest";
}

self.addEventListener("fetch", (event) => {
  const request = event.request;
  const url = new URL(request.url);

  if (request.method !== "GET" || url.origin !== self.location.origin || url.pathname.startsWith("/api/backend")) {
    return;
  }

  if (request.mode === "navigate") {
    event.respondWith(fetch(request).catch(() => caches.match("/offline/")));
    return;
  }

  if (isStaticAsset(url)) {
    event.respondWith(
      caches.open(CACHE_NAME).then(async (cache) => {
        const cached = await cache.match(request);
        if (cached) {
          return cached;
        }
        const response = await fetch(request);
        if (response.ok && response.type !== "opaque") {
          await cache.put(request, response.clone());
        }
        return response;
      }),
    );
  }
});

// --- Benachrichtigungen -----------------------------------------------------

/** Was der Worker an die Seite meldet. `PUSH` heisst: eine Erinnerung kam an. */
const NACHRICHT_PUSH = "LERNRAUM_PUSH";
const NACHRICHT_SCHLUESSEL = "LERNRAUM_PUSH_KEY";
const FRAGE_SCHLUESSEL = "LERNRAUM_FRAGE_PUSH_KEY";

/**
 * Der Application Server Key, so wie ihn die Seite gemeldet hat.
 *
 * `null` heisst: die Seite hat noch keinen geschickt. Dann weiss der Worker
 * auch nichts - und ratet nicht.
 */
let publicKey = null;

/** Der Pfad, den ein Klick ansteuert. Gehoert zur Nutzlast, nicht zum Worker. */
function zielVon(nutzlast) {
  if (nutzlast && typeof nutzlast.url === "string" && nutzlast.url.startsWith("/")) {
    return nutzlast.url;
  }
  return "/";
}

async function zeigeNutzlast(nutzlast) {
  if (typeof Notification === "undefined" || Notification.permission !== "granted") {
    // Ohne Erlaubnis wird **nichts** angezeigt. Die offene Seite holt dieselbe
    // Erinnerung ueber `GET /v1/notifications` und zeigt sie selbst - das ist
    // der Weg ohne Push.
    return false;
  }
  await self.registration.showNotification(nutzlast.title || "Slearn", {
    body: nutzlast.body || "",
    // Ohne `tag` ersetzt jede Nachricht die vorige; mit `tag` fasst der Browser
    // dieselbe Aufgabe zu einer Benachrichtigung zusammen.
    tag: nutzlast.tag || "lernraum",
    renotify: false,
    icon: "/icons/icon-192.png",
    badge: "/icons/icon-192.png",
    data: { url: zielVon(nutzlast) },
  });
  return true;
}

async function informiereSeiten(nutzlast) {
  const clients = await self.clients.matchAll({ type: "window", includeUncontrolled: true });
  for (const client of clients) {
    client.postMessage({ type: NACHRICHT_PUSH, payload: nutzlast });
  }
  return clients.length;
}

self.addEventListener("push", (event) => {
  // Die Nutzlast ist JSON, aber ein Push-Dienst darf auch ein leeres oder
  // kaputtes Data schicken. Deshalb wird nichts blind geparst.
  let nutzlast = {};
  try {
    nutzlast = event.data ? event.data.json() : {};
  } catch (error) {
    nutzlast = { title: "Slearn", body: event.data ? event.data.text() : "" };
  }
  event.waitUntil(
    (async () => {
      // Erst die offenen Tabs, dann das Systemfenster. Ein Tab im Vordergrund
      // zeigt die Erinnerung selbst an (er kann sie verlinken), also keine
      // zweite Systemmeldung obendrauf.
      const erreicht = await informiereSeiten(nutzlast);
      if (erreicht === 0) {
        await zeigeNutzlast(nutzlast);
      }
    })(),
  );
});

self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  const ziel = zielVon(event.notification.data);
  event.waitUntil(
    (async () => {
      const clients = await self.clients.matchAll({ type: "window", includeUncontrolled: true });
      for (const client of clients) {
        if ("focus" in client) {
          await client.focus();
          // Ein offener Tab wird benutzt. Der Anker wandert per `postMessage`,
          // damit die Seite ihn auch setzt, wenn sie im Hintergrund war.
          client.postMessage({ type: NACHRICHT_PUSH, payload: { url: ziel, navigate: true } });
          return;
        }
      }
      // Erst jetzt ein neues Fenster - ein Push darf nie selbst eines oeffnen.
      await self.clients.openWindow(ziel);
    })(),
  );
});

self.addEventListener("message", (event) => {
  const daten = event.data;
  if (!daten || typeof daten !== "object") {
    return;
  }
  if (daten.type === NACHRICHT_SCHLUESSEL) {
    publicKey = typeof daten.publicKey === "string" && daten.publicKey ? daten.publicKey : null;
    if (event.source) {
      event.source.postMessage({ type: NACHRICHT_SCHLUESSEL, publicKey });
    }
    return;
  }
  // Gleiche Leitung, andere Richtung: `LERNRAUM_PUSH_KEY` ist die **Bitte** der
  // Seite, den Schluessel zu bekommen, den sie selbst ueber
  // `NACHRICHT_SCHLUESSEL` gemeldet hat. Zwei Namen, damit ein Tippfehler nicht
  // still nichts tut.
  if (daten.type === FRAGE_SCHLUESSEL || daten.type === "LERNRAUM_PUSH_KEY") {
    if (event.source) {
      event.source.postMessage({ type: NACHRICHT_SCHLUESSEL, publicKey });
    }
    return;
  }
  if (daten.type === "LERNRAUM_ANZEIGEN") {
    // Die Seite uebergibt eine Erinnerung, die **sie** geholt hat. Das ist der
    // lokale Weg: der Worker zeigt sie als Systemmeldung, auch wenn der Tab
    // gerade im Hintergrund liegt und sein Timer gar nicht laeuft.
    event.waitUntil(zeigeNutzlast(daten.payload || {}));
  }
});
