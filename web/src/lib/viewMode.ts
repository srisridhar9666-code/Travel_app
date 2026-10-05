/**
 * Desktop or phone layout on a phone. public/boot.js applies the choice before
 * the page lays out; this reads and changes it. Desktop is the default.
 */
const KEY = 'sriyatra.view';

/** A phone or tablet: a touch screen narrower than 1024px on its short side,
 *  where the responsive layout would drop the sidebar for a bottom bar. These
 *  open the desktop layout by default. Mirrors public/boot.js. A desktop or
 *  laptop browser ignores the viewport tag, so it is never affected. */
export function isPhone(): boolean {
  const touch =
    (typeof window.matchMedia === 'function' && window.matchMedia('(pointer: coarse)').matches) ||
    navigator.maxTouchPoints > 0;
  return touch && Math.min(window.screen.width, window.screen.height) < 1024;
}

export function viewMode(): 'desktop' | 'mobile' {
  try {
    return localStorage.getItem(KEY) === 'mobile' ? 'mobile' : 'desktop';
  } catch {
    return 'desktop';
  }
}

/** Saved per browser, then reloaded so the layout starts over at the new width. */
export function setViewMode(mode: 'desktop' | 'mobile') {
  try {
    localStorage.setItem(KEY, mode);
  } catch {
    // Without storage the choice cannot outlive the reload; nothing to do.
    return;
  }
  window.location.reload();
}
