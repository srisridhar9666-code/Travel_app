/**
 * Showing a private file - a ticket, an ID scan - in a new tab.
 *
 * These come through the API with the bearer token, so they cannot be plain
 * links. A browser lets a click open a tab, but blocks one opened after a
 * network wait, and does it silently: the button seemed to do nothing. So the
 * tab is opened inside the click (`openFileTab`) and pointed at the file once
 * it has arrived (`showFile`). If the tab was blocked anyway, the file is
 * downloaded instead.
 */

/** Call synchronously from the click handler. */
export function openFileTab(): Window | null {
  const tab = window.open('', '_blank');
  // The document must not be able to reach back into the app that opened it.
  if (tab) tab.opener = null;
  return tab;
}

export async function showFile(
  tab: Window | null,
  load: () => Promise<Blob>,
  fileName: string,
): Promise<void> {
  let blob: Blob;
  try {
    blob = await load();
  } catch (error) {
    tab?.close();
    throw error;
  }
  const url = URL.createObjectURL(blob);
  if (tab) {
    // Closed while loading: they changed their mind.
    if (!tab.closed) tab.location.href = url;
  } else {
    const link = document.createElement('a');
    link.href = url;
    link.download = fileName;
    link.click();
  }
  window.setTimeout(() => URL.revokeObjectURL(url), 60_000);
}
