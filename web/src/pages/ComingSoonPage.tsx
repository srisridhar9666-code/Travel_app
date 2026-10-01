import { Check, Hammer } from 'lucide-react';

import { Badge, Card } from '@/components/ui';

interface ComingSoonPageProps {
  phase: number;
  title: string;
  description: string;
  bullets: string[];
}

/**
 * Placeholder for a route whose phase has not been built yet.
 *
 * It states exactly what will live here rather than showing a blank panel, so
 * the navigation is honest about what is and is not finished.
 */
export default function ComingSoonPage({
  phase,
  title,
  description,
  bullets,
}: ComingSoonPageProps) {
  return (
    <div className="mx-auto max-w-2xl">
      <Card>
        <div className="p-8">
          <div className="flex items-center gap-2.5">
            <Hammer size={18} className="text-text-subtle" />
            <Badge tone="brand">Phase {phase}</Badge>
          </div>

          <h1 className="mt-4 text-xl font-semibold tracking-tight">{title}</h1>
          <p className="mt-2 text-sm leading-relaxed text-text-muted">{description}</p>

          <ul className="mt-6 space-y-2.5 border-t border-border pt-6">
            {bullets.map((bullet) => (
              <li key={bullet} className="flex items-start gap-2.5 text-sm text-text-muted">
                <Check size={15} className="mt-0.5 shrink-0 text-text-subtle" />
                <span>{bullet}</span>
              </li>
            ))}
          </ul>
        </div>
      </Card>
    </div>
  );
}
