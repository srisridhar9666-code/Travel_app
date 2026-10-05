import { useQuery } from '@tanstack/react-query';

import { Select } from '@/components/ui';
import { fetchVendors } from '@/lib/api';
import { VENDOR_KIND_LABELS, type RequestTraveller, type Vendor } from '@/types';

/** The vendor everyone shares, or '' when they differ or none is recorded. */
export function sharedVendor(rows: RequestTraveller[]): number | '' {
  const ids = new Set(rows.map((t) => t.vendor_id ?? null));
  const [only] = [...ids];
  return ids.size === 1 && only != null ? only : '';
}

export function useVendors() {
  return useQuery({ queryKey: ['vendors'], queryFn: () => fetchVendors() });
}

/**
 * Who was paid: the vendor picker on the cost panel and the cab details.
 *
 * Only active vendors are offered for a new choice. One already recorded but
 * since switched off stays in the list as the current value, so re-saving a
 * cost does not silently drop who it was paid to.
 */
export function VendorSelect({
  id,
  value,
  onChange,
  emptyLabel = 'Not recorded',
  className,
  disabled,
}: {
  id?: string;
  value: number | '';
  onChange: (value: number | '') => void;
  emptyLabel?: string;
  className?: string;
  disabled?: boolean;
}) {
  const vendors = useVendors();
  const rows: Vendor[] = (vendors.data ?? []).filter((v) => v.is_active || v.id === value);
  return (
    <Select
      id={id}
      value={value === '' ? '' : String(value)}
      onChange={(e) => onChange(e.target.value === '' ? '' : Number(e.target.value))}
      disabled={disabled || vendors.isPending}
      className={className}
    >
      <option value="">{vendors.isPending ? 'Loading vendors…' : emptyLabel}</option>
      {rows.map((vendor) => (
        <option key={vendor.id} value={vendor.id}>
          {vendor.name} · {VENDOR_KIND_LABELS[vendor.kind]}
          {vendor.is_active ? '' : ' (switched off)'}
        </option>
      ))}
    </Select>
  );
}
