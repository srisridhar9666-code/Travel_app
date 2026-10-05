import { clsx, type ClassValue } from 'clsx';
import { twMerge } from 'tailwind-merge';

/** Merge conditional class names, with later Tailwind utilities winning. */
export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

/** An employee's mobile as they type it: digits only, at most ten. A pasted
 *  "+91 98765 43210" or "098765 43210" becomes 9876543210 - the server stores
 *  every staff number as its bare ten digits, starting 6-9. */
export function mobileDigits(value: string): string {
  let digits = value.replace(/\D/g, '');
  if (digits.length === 12 && digits.startsWith('91')) digits = digits.slice(2);
  else if (digits.length === 11 && digits.startsWith('0')) digits = digits.slice(1);
  return digits.slice(0, 10);
}

export const MOBILE_HINT = '10 digits, starting with 6, 7, 8 or 9. No country code.';
