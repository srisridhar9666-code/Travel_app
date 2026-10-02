import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { XCircle } from 'lucide-react';
import toast from 'react-hot-toast';

import { Combobox } from '@/components/Combobox';
import { createDepartment, fetchDepartments } from '@/lib/api';

interface DepartmentPickerProps {
  id: string;
  value: number | null;
  onChange: (id: number | null) => void;
}

/**
 * Pick a department, or type a new one and add it on the spot.
 *
 * Admins asked to "create more roles" while adding a person. Departments are
 * that: an open list grown from this field, with no separate settings screen
 * to visit first. The server answers an existing name (in any case) with the
 * existing department, so a typo in case never makes a second one.
 */
export function DepartmentPicker({ id, value, onChange }: DepartmentPickerProps) {
  const queryClient = useQueryClient();
  const departments = useQuery({ queryKey: ['departments'], queryFn: fetchDepartments });
  const list = departments.data ?? [];
  const current = list.find((d) => d.id === value)?.name ?? '';

  const add = useMutation({
    mutationFn: createDepartment,
    meta: { errorFallback: 'Could not add that department.' },
    onSuccess: (created) => {
      queryClient.invalidateQueries({ queryKey: ['departments'] });
      toast.success(`Department “${created.name}” added`);
      onChange(created.id);
    },
  });

  return (
    <Combobox
      id={id}
      value={add.isPending ? add.variables ?? '' : current}
      options={list.map((d) => d.name)}
      loading={departments.isPending || add.isPending}
      disabled={add.isPending}
      placeholder={departments.isPending ? 'Loading…' : 'Choose or type a new one'}
      emptyText="No departments yet. Type a name to add one."
      allowCustom
      customLabel={(typed) => (
        <>
          Add new department “<span className="font-medium text-text">{typed}</span>”
        </>
      )}
      action={
        value !== null
          ? {
              label: 'No department',
              icon: <XCircle size={15} className="mt-0.5 shrink-0" />,
              onSelect: () => onChange(null),
            }
          : undefined
      }
      onChange={(name) => {
        if (!name) {
          onChange(null);
          return;
        }
        const known = list.find((d) => d.name.toLowerCase() === name.toLowerCase());
        if (known) onChange(known.id);
        else add.mutate(name.trim());
      }}
    />
  );
}
