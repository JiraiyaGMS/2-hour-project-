/* planespotter as an installed app (Android's "Install app", or Add to Home Screen). Planes are always
 * live: nothing is kept offline except a small page that says so when there's no connection. */
self.addEventListener('install', () => self.skipWaiting());
self.addEventListener('activate', (event) => event.waitUntil(self.clients.claim()));
self.addEventListener('fetch', (event) => {
  if (event.request.mode !== 'navigate') return;  // everything else: straight to the network, as usual
  event.respondWith(fetch(event.request).catch(() => new Response(
    '<!doctype html><meta name="viewport" content="width=device-width,initial-scale=1">' +
    '<body style="margin:0;font:17px -apple-system,Roboto,sans-serif;background:#0b0f14;color:#e8eef5;display:grid;place-items:center;height:100vh;text-align:center;padding:24px">' +
    '<div><img src="icon-192.png" alt="" width="64" height="64" style="border-radius:16px"><h2>No connection</h2><p style="color:#9aa7b6">planespotter needs the internet for live planes.<br>It will work again as soon as you\'re back online.</p>' +
    '<button onclick="location.reload()" style="font:inherit;padding:10px 18px;border-radius:10px;border:0;background:#e8860c;color:#111;font-weight:700">Try again</button></div>',
    { headers: { 'Content-Type': 'text/html; charset=utf-8' } })));
});
