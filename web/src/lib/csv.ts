/**
 * Spreadsheet downloads, built in the browser from rows the API already sent.
 *
 * Free text people typed (a reason, a note) can start with "=", "+", "-" or
 * "@", which Excel and Sheets run as a formula. Those cells get a leading
 * apostrophe so they open as the text they are.
 */

export type CsvCell = string | number | null | undefined;

const FORMULA = /^[=+\-@\t\r]/;

function cell(value: CsvCell): string {
  if (value === null || value === undefined) return '';
  let text = String(value);
  if (typeof value === 'string' && FORMULA.test(text)) text = `'${text}`;
  return /[",\r\n]/.test(text) ? `"${text.replace(/"/g, '""')}"` : text;
}

function toCsv(header: string[], rows: CsvCell[][]): string {
  return [header, ...rows].map((row) => row.map(cell).join(',')).join('\r\n');
}

/** Save as a .csv. The byte-order mark makes Excel read ₹ and Indian names
 *  as UTF-8 instead of mangling them. */
export function downloadCsv(filename: string, header: string[], rows: CsvCell[][]) {
  const url = URL.createObjectURL(
    new Blob([`﻿${toCsv(header, rows)}`], { type: 'text/csv;charset=utf-8' }),
  );
  const link = document.createElement('a');
  link.href = url;
  link.download = filename;
  link.click();
  URL.revokeObjectURL(url);
}

/** "Partly approved" -> "partly-approved", for file names. */
export function slug(label: string): string {
  return label
    .replace(/[^a-z0-9]+/gi, '-')
    .replace(/^-|-$/g, '')
    .toLowerCase();
}
