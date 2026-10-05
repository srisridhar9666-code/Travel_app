import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { IndianRupee, Lock, Receipt, Split } from 'lucide-react';
import { useState } from 'react';
import { Link } from 'react-router-dom';
import toast from 'react-hot-toast';

import { formatMoney } from '@/components/charts';
import { Button, Field, Input, Skeleton } from '@/components/ui';
import { VendorSelect, sharedVendor } from '@/components/VendorSelect';
import { errorMessage, fetchRequest, previewSplit, setCosts, splitCost } from '@/lib/api';
import { INVOICE_STATUS_LABELS, type CostPreview, type RequestTraveller } from '@/types';

/**
 * Recording what a trip cost (addendum C1), and who was paid for it.
 *
 * Two paths, because C1 asks for both: type a number per person, or share one
 * total evenly. The split is computed on the server and *previewed* before it is
 * saved - three people sharing a thousand rupees get 333.34, 333.33, 333.33, and
 * seeing that before committing is the difference between "the odd paisa is
 * handled" and "the odd paisa is a bug".
 *
 * The vendor - travel agent, cab operator, hotel - is what a vendor's invoice
 * is reconciled against. A cost billed on an approved invoice is locked.
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
  // The queue list leaves cost out, so the panel reads the request itself:
  // otherwise it would open blank, and saving blanks would clear real costs.
  const detail = useQuery({ queryKey: ['request', requestId], queryFn: () => fetchRequest(requestId) });

  if (detail.isPending) {
    return (
      <div className="space-y-2">
        <Skeleton className="h-8 w-full" />
        <Skeleton className="h-8 w-2/3" />
      </div>
    );
  }
  if (detail.isError) {
    return (
      <p className="text-2xs text-danger">
        {errorMessage(detail.error, 'Could not load the costs for this request.')}
      </p>
    );
  }
  // Keyed by when it was read, so the form starts again from the saved
  // figures after every save.
  return (
    <CostForm
      key={detail.dataUpdatedAt}
      requestId={requestId}
      travellers={detail.data?.travellers ?? travellers}
      onChanged={onChanged}
    />
  );
}

const locked = (t: RequestTraveller) => t.invoice_status === 'APPROVED';

function CostForm({
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
  // A cost on an approved invoice is a record of what was paid; the server
  // refuses to change it, so the form does not offer to.
  const open = costable.filter((t) => !locked(t));
  const initialVendor = sharedVendor(open);
  const [vendor, setVendor] = useState<number | ''>(initialVendor);
  // Only sent when chosen: left alone, each traveller keeps the vendor they had.
  const vendorChoice = vendor === '' || vendor === initialVendor ? undefined : vendor;

  // Spend feeds the dashboard insights and the travel log's cost column as well
  // as the analytics page, so all three refetch rather than show the old total.
  // An invoice follows the costs on it until it is approved.
  const refresh = () => {
    queryClient.invalidateQueries({ queryKey: ['analytics'] });
    queryClient.invalidateQueries({ queryKey: ['insights'] });
    queryClient.invalidateQueries({ queryKey: ['travel-logs'] });
    queryClient.invalidateQueries({ queryKey: ['request', requestId] });
    queryClient.invalidateQueries({ queryKey: ['invoices'] });
    onChanged();
  };

  const preview_ = useMutation({
    mutationFn: () =>
      previewSplit(requestId, {
        total_amount: total,
        traveller_ids: open.map((t) => t.id),
      }),
    onSuccess: setPreview,
  });

  const save = useMutation({
    mutationFn: () =>
      splitCost(requestId, {
        total_amount: total,
        traveller_ids: open.map((t) => t.id),
        ...(vendor !== '' ? { vendor_id: vendor } : {}),
      }),
    meta: { errorFallback: 'Could not save the split.' },
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
        open.map((t) => ({
          traveller_id: t.id,
          amount: (amounts[t.id] ?? '').trim() === '' ? null : amounts[t.id].trim(),
        })),
        vendorChoice,
      ),
    meta: { errorFallback: 'Could not save the costs.' },
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

  const vendorNames = [...new Set(open.map((t) => t.vendor_name).filter(Boolean))];

  return (
    <div className="space-y-4">
      {open.length > 0 && (
        <Field
          label="Paid to"
          htmlFor={`vendor-${requestId}`}
          hint={
            initialVendor === '' && vendorNames.length > 1
              ? `Now: ${vendorNames.join(', ')}. Choosing one records it for everyone below.`
              : 'The travel agent, cab operator or hotel. Vendor invoices are matched against it.'
          }
        >
          <VendorSelect
            id={`vendor-${requestId}`}
            value={vendor}
            onChange={setVendor}
            emptyLabel={initialVendor === '' ? 'Not recorded — choose a vendor' : 'Choose a vendor'}
            className="sm:max-w-sm"
          />
        </Field>
      )}

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
                disabled={locked(traveller)}
                aria-label={`Cost for ${traveller.full_name}`}
                className="h-8 w-32 pl-7 text-right tabular-nums"
              />
            </div>
            {traveller.invoice_number && (
              <Link
                to={`/invoices/${traveller.invoice_id}`}
                className="inline-flex w-full items-center gap-1 text-2xs text-text-muted hover:text-text sm:w-auto"
                title={
                  locked(traveller)
                    ? 'Billed on an approved invoice, so this cost and its vendor can no longer change.'
                    : 'The invoice follows this cost until it is approved.'
                }
              >
                {locked(traveller) ? <Lock size={11} /> : <Receipt size={11} />}
                {traveller.invoice_number} · {INVOICE_STATUS_LABELS[traveller.invoice_status!].toLowerCase()}
              </Link>
            )}
            {traveller.cost_note && (
              <span className="w-full text-2xs text-text-subtle sm:w-auto">
                {traveller.cost_note}
                {traveller.vendor_name && ` · ${traveller.vendor_name}`}
              </span>
            )}
          </div>
        ))}
        {open.length > 0 ? (
          <Button size="sm" loading={saveExact.isPending} onClick={() => saveExact.mutate()}>
            Save costs
          </Button>
        ) : (
          <p className="text-2xs text-text-subtle">
            Every cost here is on an approved invoice, so none can change.
          </p>
        )}
      </div>

      {open.length > 0 && open.length === costable.length && (
        <div className="border-t border-border pt-3">
          <Field
            label="Or share one total evenly"
            htmlFor={`split-${requestId}`}
            hint={`Split across ${open.length} traveller${open.length === 1 ? '' : 's'}. The first absorbs any odd paisa.`}
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
      )}
    </div>
  );
}
