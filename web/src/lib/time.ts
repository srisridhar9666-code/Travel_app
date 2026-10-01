/**
 * Times, shown as India time whatever the device's clock says.
 *
 * Two kinds of time come from the API and they must not be mixed up:
 *
 * - **Recorded moments** - when a request was submitted, decided, an email
 *   sent, someone signed in. The server stores these in UTC and sends them
 *   with a trailing `Z`. They are converted to India time here.
 * - **Trip times** - a 06:00 departure, a check-in date. These are what the
 *   traveller typed, with no zone, and are shown exactly as typed. Converting
 *   them would move someone's flight.
 *
 * The bug this replaced: recorded moments were sent without the `Z`, so the
 * browser read UTC as local time and every log entry was 5h30m early.
 */

export const APP_TIME_ZONE = 'Asia/Kolkata';
const LOCALE = 'en-IN';

const HAS_ZONE = /(?:[zZ]|[+-]\d{2}:?\d{2})$/;

/** A recorded moment from the API. One without a zone is UTC - that is what
 *  the server's clock is, and what older responses left implicit. */
export function parseInstant(iso: string): Date {
  return new Date(HAS_ZONE.test(iso) ? iso : `${iso}Z`);
}

const formatters = new Map<string, Intl.DateTimeFormat>();

function formatter(options: Intl.DateTimeFormatOptions): Intl.DateTimeFormat {
  const key = JSON.stringify(options);
  let found = formatters.get(key);
  if (!found) {
    found = new Intl.DateTimeFormat(LOCALE, { timeZone: APP_TIME_ZONE, ...options });
    formatters.set(key, found);
  }
  return found;
}

const DATE_TIME: Intl.DateTimeFormatOptions = {
  day: '2-digit',
  month: 'short',
  year: 'numeric',
  hour: '2-digit',
  minute: '2-digit',
};

/** "02 Oct 2026, 12:35 am" in India time. Pass options to show less. */
export function formatInstant(
  iso: string | null | undefined,
  options: Intl.DateTimeFormatOptions = DATE_TIME,
): string {
  if (!iso) return '';
  const date = parseInstant(iso);
  return Number.isNaN(date.getTime()) ? iso : formatter(options).format(date);
}

/** "02 Oct 2026" - the India date of a recorded moment. */
export function formatInstantDate(iso: string | null | undefined): string {
  return formatInstant(iso, { day: '2-digit', month: 'short', year: 'numeric' });
}

/** "just now", "5m ago", "3h ago", "2d ago", then the India date. */
export function timeAgo(iso: string, now: number = Date.now()): string {
  const seconds = Math.round((now - parseInstant(iso).getTime()) / 1000);
  if (seconds < 60) return 'just now';
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m ago`;
  if (seconds < 86_400) return `${Math.floor(seconds / 3600)}h ago`;
  if (seconds < 7 * 86_400) return `${Math.floor(seconds / 86_400)}d ago`;
  return formatInstant(iso, { day: '2-digit', month: 'short' });
}

/** Today's date in India as YYYY-MM-DD - "last month" and "this week" mean the
 *  same days for everyone, whatever their laptop's clock is set to. */
export function todayInIndia(now: Date = new Date()): string {
  // en-CA formats a date as YYYY-MM-DD.
  return new Intl.DateTimeFormat('en-CA', { timeZone: APP_TIME_ZONE }).format(now);
}

/** A file-name stamp in India time: 2026-10-02-0035. */
export function fileStamp(now: Date = new Date()): string {
  const parts = Object.fromEntries(
    new Intl.DateTimeFormat('en-GB', {
      timeZone: APP_TIME_ZONE,
      year: 'numeric',
      month: '2-digit',
      day: '2-digit',
      hour: '2-digit',
      minute: '2-digit',
      hourCycle: 'h23',
    })
      .formatToParts(now)
      .map((part) => [part.type, part.value]),
  );
  return `${parts.year}-${parts.month}-${parts.day}-${parts.hour}${parts.minute}`;
}
