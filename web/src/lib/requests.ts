/** Small wordings shared by My requests and Approvals, so the two screens
 *  describe the same request the same way. */

import { routeLabel } from '@/lib/places';
import { PRIORITY_LABELS, type RequestPriority, type TravelRequest } from '@/types';

const dayMonth = (iso: string) =>
  new Date(iso.length <= 10 ? `${iso}T00:00:00` : iso).toLocaleDateString(undefined, {
    day: '2-digit',
    month: 'short',
  });

const dayTime = (iso: string) =>
  new Date(iso).toLocaleString(undefined, {
    day: '2-digit',
    month: 'short',
    hour: '2-digit',
    minute: '2-digit',
  });

/** Where and when, in one line: "Pune · 14 Oct – 16 Oct", or a route with its
 *  departure. */
export function itinerary(request: TravelRequest): string {
  if (request.request_type === 'HOTEL') {
    const nights = request.check_out
      ? `${dayMonth(request.check_in!)} – ${dayMonth(request.check_out)}`
      : dayMonth(request.check_in!);
    return `${request.hotel_city} · ${nights}`;
  }
  return `${routeLabel(request)} · ${request.start_at ? dayTime(request.start_at) : ''}`;
}

/** The campaign as a person would name it. A request filed under the fallback
 *  "Other" campaign shows what the requester typed, not the fallback's name. */
export function campaignLabel(
  request: Pick<TravelRequest, 'other_project_name' | 'project_name' | 'project_code'>,
): string {
  return request.other_project_name
    ? `Other: ${request.other_project_name}`
    : request.project_name || request.project_code;
}

/** A value in the revision diff as the form showed it. Priority is stored as
 *  HIGH/MEDIUM/LOW; the person changing it picked High/Medium/Low. */
export function revisionValue(field: string, value: unknown): string {
  if (value === null || value === undefined || value === '') return '—';
  if (field === 'priority' && typeof value === 'string' && value in PRIORITY_LABELS) {
    return PRIORITY_LABELS[value as RequestPriority];
  }
  return String(value);
}
