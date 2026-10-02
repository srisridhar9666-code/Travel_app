import { todayInIndia } from '@/lib/time';
import type { Project } from '@/types';

/** Same list as the server's services/projects.py: "Survey of the Godavari
 *  Districts" is SGD, not SOTGD. */
const STOPWORDS = new Set([
  'A', 'AN', 'AND', 'THE', 'OF', 'FOR', 'IN', 'ON', 'AT', 'TO', 'BY', 'WITH', 'FROM',
]);

/**
 * The code the server will make for a blank code field, shown as the field's
 * placeholder so the admin sees what "leave it blank" gives them.
 *
 * A preview only. The server makes the real one, adding "-2" when this is
 * already taken. Mirrors code_base() there.
 */
export function suggestCampaignCode(name: string, startDate?: string | null): string {
  if (!name.trim()) return '';
  const folded = name
    .normalize('NFKD')
    .replace(/[̀-ͯ]/g, '')
    .toUpperCase();
  const words = (folded.match(/[A-Z0-9]+/g) ?? []).filter((w) => !/^\d+$/.test(w));
  const meaningful = words.filter((w) => !STOPWORDS.has(w));
  const picked = meaningful.length ? meaningful : words;

  let initials: string;
  if (picked.length >= 2) initials = picked.slice(0, 4).map((w) => w[0]).join('');
  else if (picked.length === 1) initials = picked[0].slice(0, 4);
  else initials = 'CMP';

  const year = (startDate || todayInIndia()).slice(2, 4);
  return `${initials}-${year}`;
}

/** "Pune, Maharashtra", or the free text typed before state and city existed. */
export function campaignPlace(project: Pick<Project, 'state' | 'city' | 'location'>): string {
  return [project.city, project.state].filter(Boolean).join(', ') || project.location || '';
}
