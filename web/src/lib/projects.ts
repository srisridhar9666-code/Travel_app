import type { Project } from '@/types';

/** "Pune, Maharashtra", or the free text typed before state and city existed. */
export function campaignPlace(project: Pick<Project, 'state' | 'city' | 'location'>): string {
  return [project.city, project.state].filter(Boolean).join(', ') || project.location || '';
}
