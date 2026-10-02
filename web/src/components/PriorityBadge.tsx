import { Badge } from '@/components/ui';
import { PRIORITY_LABELS, type RequestPriority } from '@/types';

/** How soon the requester needs a decision. Only High is coloured: if every
 *  priority had its own colour, the one that matters would not stand out. */
export function PriorityBadge({ priority }: { priority: RequestPriority | null | undefined }) {
  const value = priority ?? 'MEDIUM';
  return (
    <Badge tone={value === 'HIGH' ? 'danger' : 'neutral'}>
      {PRIORITY_LABELS[value]} priority
    </Badge>
  );
}
