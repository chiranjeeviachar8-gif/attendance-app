// Minimal service worker - required for "Add to Home Screen" install prompts.
// This app needs live data, so we don't cache pages -- just pass requests through.

self.addEventListener("install", (event) => {
  self.skipWaiting();
});

self.addEventListener("activate", (event) => {
  self.clients.claim();
});

self.addEventListener("fetch", (event) => {
  event.respondWith(fetch(event.request));
});
