/**
 * Small wordings and sums shared by the invoice screens and the print view,
 * so a period or a total reads the same everywhere.
 */

import { todayInIndia } from '@/lib/time';

const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];

/** "12 Sep 2026" from a calendar date ("2026-09-12"). Calendar dates carry no
 *  time zone, so this never goes through Date - it cannot move a day. */
export function dayLabel(iso: string | null | undefined): string {
  if (!iso) return '—';
  const [year, month, day] = iso.slice(0, 10).split('-');
  const name = MONTHS[Number(month) - 1];
  return name ? `${day} ${name} ${year}` : iso;
}

export const periodText = (start: string, end: string) => `${dayLabel(start)} – ${dayLabel(end)}`;

/** Rupee strings added in whole paise, so 0.1 + 0.2 is 0.30 and not
 *  0.30000000000000004. Returns "1234.50". */
export function sumAmounts(amounts: string[]): string {
  const paise = amounts.reduce((total, amount) => total + Math.round(Number(amount) * 100), 0);
  return (paise / 100).toFixed(2);
}

/** The first and last day of last month in India, YYYY-MM-DD: what a vendor
 *  usually bills for, so it is where a new invoice starts. */
export function lastMonth(today: string = todayInIndia()): { start: string; end: string } {
  const [year, month] = today.split('-').map(Number);
  const y = month === 1 ? year - 1 : year;
  const m = month === 1 ? 12 : month - 1;
  const days = new Date(Date.UTC(y, m, 0)).getUTCDate();
  const mm = String(m).padStart(2, '0');
  return { start: `${y}-${mm}-01`, end: `${y}-${mm}-${String(days).padStart(2, '0')}` };
}

/** Action names from the activity log, as the invoice timeline says them. */
export const INVOICE_EVENT_LABELS: Record<string, string> = {
  CREATE: 'Created',
  UPDATE: 'Edited',
  SUBMIT: 'Submitted for approval',
  APPROVE: 'Approved',
  REJECT: 'Rejected',
  DELETE: 'Deleted',
  EXPORT: 'Downloaded',
};
