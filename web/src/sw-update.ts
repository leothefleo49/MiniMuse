/**
 * A newer build waiting behind the page. The service worker's own update is the usual
 * signal (a rebuilt app regenerates the hashed assets it precaches), but a rebuild also
 * just changes the hashed script the page's index.html names — so when the server's
 * fresh index.html names a script the running page is not using, that is a new build
 * too, service worker or not. Checked once when the app opens and whenever it comes
 * back to the front.
 */
export function watchForUpdate(onReady: () => void): void {
  let announced = false;
  const ready = () => {
    if (announced) return;
    announced = true;
    onReady();
  };

  // a worker that already controls this page: a controllerchange later on is a new
  // build claiming it, not this visit's very first one, which is nothing to announce
  const hasSw = "serviceWorker" in navigator && window.isSecureContext;
  if (hasSw) {
    let owned = navigator.serviceWorker.controller !== null;
    navigator.serviceWorker.addEventListener("controllerchange", () => {
      if (owned) ready();
      owned = true;
    });
  }

  const scriptOf = (html: string): string | null =>
    /<script[^>]+type="module"[^>]+src="(\/assets\/[^"]+\.js)"/.exec(html)?.[1] ??
    /<script[^>]+src="(\/assets\/[^"]+\.js)"[^>]*type="module"/.exec(html)?.[1] ??
    null;

  const check = async (): Promise<void> => {
    if (hasSw) {
      try {
        const reg = (await navigator.serviceWorker.getRegistration()) ?? (await navigator.serviceWorker.register("/sw.js"));
        await reg.update();
        if (reg.waiting || reg.installing) {
          ready();
        }
      } catch {
        /* offline: the worker holds the app up and there is nothing newer to fetch */
      }
    }
    // the page's own script against the server's: a different one is a newer build
    try {
      const res = await fetch("/", { cache: "no-store" });
      const fresh = scriptOf(await res.text());
      const mine = document.querySelector('script[type="module"]')?.getAttribute("src");
      if (fresh && mine && fresh !== mine) {
        try {
          const reloadKey = "nanomuse_auto_reloaded_bundle";
          if (sessionStorage.getItem(reloadKey) !== fresh) {
            sessionStorage.setItem(reloadKey, fresh);
            onReady();
            return;
          }
        } catch {
          // ignore sessionStorage errors
        }
        ready();
      }
    } catch {
      /* offline: nothing to compare against */
    }
  };

  void check();
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "visible") void check();
  });
}
