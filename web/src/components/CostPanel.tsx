import { useMutation, useQueryClient } from '@tanstack/react-query';
import { IndianRupee, Split } from 'lucide-react';
import { useState } from 'react';
import toast from 'react-hot-toast';

import { formatMoney } from '@/components/charts';
import { Button, Field, Input } from '@/components/ui';
import { previewSplit, setCosts, splitCost } from '@/lib/api';
import type { CostPreview, RequestTraveller } from '@/types';

/**
 * Recording what a trip cost (addendum C1).
 *
 * Two paths, because C1 asks for both: type a number per person, or share one
 * total evenly. The split is computed on the server and *previewed* before it is
 * saved - three people sharing a thousand rupees get 333.34, 333.33, 333.33, and
 * seeing that before committing is the difference between "the odd paisa is
 * handled" and "the odd paisa is a bug".
 */
export default function CostPanel({
  requestId,
  travellers,
  onChanged,
}: {
  requestId: number;
  travellers: RequestTraveller[];
  onChanged: () => void;
}) {
  const queryClient = useQueryClient();
  const [total, setTotal] = useState('');
  const [preview, setPreview] = useState<CostPreview | null>(null);
  const [amounts, setAmounts] = useState<Record<number, string>>(() =>
    Object.fromEntries(travellers.map((t) => [t.id, t.cost_amount ?? ''])),
  );

  // Only travellers who are actually going. Recording spend against a rejected
  // person would quietly inflate the campaign total with money nobody paid.
  const costable = travellers.filter(
    (t) => t.status === 'BOOKED' || t.status === 'APPROVED',
  );

  // Spend feeds the dashboard insights and the travel log's cost column as well
  // as the analytics page, so all three refetch rather than show the old total.
  const refresh = () => {
    queryClient.invalidateQueries({ queryKey: ['analytics'] });
    queryClient.invalidateQueries({ queryKey: ['insights'] });
    queryClient.invalidateQueries({ queryKey: ['travel-logs'] });
    onChanged();
  };

  const preview_ = useMutation({
    mutationFn: () =>
      previewSplit(requestId, {
        total_amount: total,
        traveller_ids: costable.map((t) => t.id),
      }),
    onSuccess: setPreview,
  });

  const save = useMutation({
    mutationFn: () =>
      splitCost(requestId, {
        total_amount: total,
        traveller_ids: costable.map((t) => t.id),
      }),
    onSuccess: () => {
      toast.success('Cost split and saved');
      setPreview(null);
      setTotal('');
      refresh();
    },
  });

  const saveExact = useMutation({
    mutationFn: () =>
      setCosts(
        requestId,
        costable.map((t) => ({
          traveller_id: t.id,
          amount: (amounts[t.id] ?? '').trim() === '' ? null : amounts[t.id].trim(),
        })),
      ),
    onSuccess: () => {
      toast.success('Cost saved');
      refresh();
    },
  });

  if (costable.length === 0) {
    return (
      <p className="text-2xs text-text-subtle">
        Approve a traveller before recording what their trip cost.
      </p>
    );
  }

  return (
    <div className="space-y-4">
      <div className="space-y-2">
        {costable.map((traveller) => (
          <div key={traveller.id} className="flex flex-wrap items-center gap-2">
            <span className="min-w-28 flex-1 truncate text-xs">{traveller.full_name}</span>
            <div className="relative">
              <IndianRupee
                size={12}
                className="pointer-events-none absolute left-2.5 top-1/2 -translate-y-1/2 text-text-subtle"
              />
              <Input
                value={amounts[traveller.id] ?? ''}
                onChange={(e) =>
                  setAmounts((prev) => ({ ...prev, [traveller.id]: e.target.value }))
                }
                inputMode="decimal"
                placeholder="0.00"
                aria-label={`Cost for ${traveller.full_name}`}
                className="h-8 w-32 pl-7 text-right tabular-nums"
              />
            </div>
            {traveller.cost_note && (
              <span className="w-full text-2xs text-text-subtle sm:w-auto">
                {traveller.cost_note}
              </span>
            )}
          </div>
        ))}
        <Button size="sm" loading={saveExact.isPending} onClick={() => saveExact.mutate()}>
          Save costs
        </Button>
      </div>

      <div className="border-t border-border pt-3">
        <Field
          label="Or share one total evenly"
          htmlFor={`split-${requestId}`}
          hint={`Split across ${costable.length} traveller${costable.length === 1 ? '' : 's'}. The first absorbs any odd paisa.`}
        >
          <div className="flex gap-2">
            <div className="relative flex-1">
              <IndianRupee
                size={12}
                className="pointer-events-none absolute left-2.5 top-1/2 -translate-y-1/2 text-text-subtle"
              />
              <Input
                id={`split-${requestId}`}
                value={total}
                onChange={(e) => {
                  setTotal(e.target.value);
                  setPreview(null);
                }}
                inputMode="decimal"
                placeholder="1000.00"
                className="pl-7 text-right tabular-nums"
              />
            </div>
            <Button
              variant="secondary"
              loading={preview_.isPending}
              disabled={!total.trim()}
              onClick={() => preview_.mutate()}
            >
              <Split size={13} />
              Preview
            </Button>
          </div>
        </Field>

        {preview && (
          <div className="mt-2 rounded-md border border-border bg-surface-sunken px-3 py-2.5">
            <ul className="space-y-1">
              {preview.rows.map((row) => (
                <li
                  key={row.traveller_id}
                  className="flex justify-between gap-3 text-2xs text-text-muted"
                >
                  <span>{row.traveller_name}</span>
                  <span className="tabular-nums text-text">{formatMoney(row.amount, true)}</span>
                </li>
              ))}
            </ul>
            <p className="mt-2 border-t border-border pt-2 text-2xs text-text-subtle">
              {preview.sums_to_total
                ? `Adds up to exactly ${formatMoney(preview.total_amount, true)}.`
                : 'These shares do not sum to the total — do not save this.'}
            </p>
            <Button
              size="sm"
              className="mt-2"
              loading={save.isPending}
              disabled={!preview.sums_to_total}
              onClick={() => save.mutate()}
            >
              Save this split
            </Button>
          </div>
        )}
      </div>
    </div>
  );
}
