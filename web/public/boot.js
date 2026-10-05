// Runs before the app, from a file rather than inline: the deployed page's
// Content-Security-Policy allows scripts from this origin only, so an inline
// script never ran there.
(function () {
  // Theme before first paint, otherwise a dark-mode user gets a white flash on
  // every load. The store saves {"state":{"preference":"dark"},...}.
  try {
    var raw = localStorage.getItem('travel-ops-theme');
    var preference = raw ? (JSON.parse(raw).state || {}).preference : null;
    var theme =
      preference === 'light' || preference === 'dark'
        ? preference
        : window.matchMedia('(prefers-color-scheme: dark)').matches
          ? 'dark'
          : 'light';
    document.documentElement.dataset.theme = theme;
  } catch (e) {
    document.documentElement.dataset.theme = 'light';
  }

  // Phones and tablets open the desktop layout, as a browser's "Desktop site"
  // does: the page is laid out 1280px wide and starts zoomed out to fit. Unless
  // this browser chose the phone layout (the switch is in the menu; see
  // src/lib/viewMode.ts, which owns the key and the same test). Any touch
  // screen under 1024px on its short side counts - that is where the
  // responsive layout would otherwise swap the sidebar for a bottom bar.
  try {
    var touch =
      (window.matchMedia && window.matchMedia('(pointer: coarse)').matches) ||
      navigator.maxTouchPoints > 0;
    var phone = touch && Math.min(screen.width, screen.height) < 1024;
    if (phone && localStorage.getItem('sriyatra.view') !== 'mobile') {
      document
        .querySelector('meta[name="viewport"]')
        .setAttribute('content', 'width=1280, viewport-fit=cover');
    }
  } catch (e) {
    // Storage blocked: the desktop layout, the default, still applies above.
  }
})();
