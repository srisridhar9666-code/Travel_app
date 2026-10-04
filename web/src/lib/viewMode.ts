/**
 * Desktop or phone layout on a phone. public/boot.js applies the choice before
 * the page lays out; this reads and changes it. Desktop is the default.
 */
const KEY = 'sriyatra.view';

/** A phone, by its smaller screen side. Tablets keep the responsive layout. */
export function isPhone(): boolean {
  return Math.min(window.screen.width, window.screen.height) < 768;
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
