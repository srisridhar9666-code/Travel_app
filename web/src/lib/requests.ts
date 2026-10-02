/** Small wordings shared by My requests and Approvals, so the two screens
 *  describe the same request the same way. */

import { PRIORITY_LABELS, type RequestPriority, type TravelRequest } from '@/types';

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
