import { Check, ChevronDown, Search } from 'lucide-react';
import { useEffect, useId, useMemo, useRef, useState, type KeyboardEvent, type ReactNode } from 'react';

import { cn } from '@/lib/utils';

/** How many matches are drawn at once. Uttar Pradesh alone has over 400 places;
 *  drawing them all on every keystroke makes a phone stutter, and nobody
 *  scrolls past the first screen anyway - they type more. */
const MAX_SHOWN = 80;

export interface ComboboxAction {
  label: string;
  hint?: string;
  icon?: ReactNode;
  onSelect: () => void;
}

interface ComboboxProps {
  id: string;
  value: string;
  onChange: (value: string) => void;
  options: string[];
  placeholder?: string;
  disabled?: boolean;
  required?: boolean;
  loading?: boolean;
  /** Offer "Use “what you typed”" when it matches nothing on the list. */
  allowCustom?: boolean;
  /** A fixed row at the foot of the list, such as "Other — type it in". */
  action?: ComboboxAction;
  emptyText?: string;
  className?: string;
}

/** Prefix matches first, then matches anywhere - "pune" should find Pune
 *  before it finds Dhule-Pune Road. */
function rank(options: string[], query: string): string[] {
  const q = query.trim().toLowerCase();
  if (!q) return options;
  const starts: string[] = [];
  const contains: string[] = [];
  for (const option of options) {
    const lower = option.toLowerCase();
    if (lower.startsWith(q)) starts.push(option);
    else if (lower.includes(q)) contains.push(option);
  }
  return [...starts, ...contains];
}

/**
 * A select you can type into.
 *
 * Built for long lists - every constituency in a state - where a native
 * `<select>` means scrolling through hundreds of rows on a phone. Typing
 * narrows the list; arrow keys and Enter pick; Escape backs out.
 */
export function Combobox({
  id,
  value,
  onChange,
  options,
  placeholder,
  disabled,
  required,
  loading,
  allowCustom,
  action,
  emptyText = 'Nothing matches.',
  className,
}: ComboboxProps) {
  const listId = useId();
  const inputRef = useRef<HTMLInputElement>(null);
  const listRef = useRef<HTMLUListElement>(null);
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState('');
  const [active, setActive] = useState(0);

  const matches = useMemo(() => rank(options, query), [options, query]);
  const shown = matches.slice(0, MAX_SHOWN);
  const typed = query.trim();
  const exact = options.some((o) => o.toLowerCase() === typed.toLowerCase());
  const offerCustom = Boolean(allowCustom && typed && !exact);

  // Rows in the order the keyboard walks them.
  const rows: { key: string; select: () => void }[] = [
    ...shown.map((option) => ({ key: option, select: () => choose(option) })),
    ...(offerCustom ? [{ key: '__custom', select: () => choose(typed) }] : []),
    ...(action ? [{ key: '__action', select: () => runAction() }] : []),
  ];

  useEffect(() => setActive(0), [query, open]);

  // Keep the highlighted row in view while arrowing through a long list.
  useEffect(() => {
    if (!open) return;
    const el = listRef.current?.querySelector<HTMLElement>(`[data-index="${active}"]`);
    el?.scrollIntoView({ block: 'nearest' });
  }, [active, open]);

  function choose(next: string) {
    onChange(next);
    setQuery('');
    setOpen(false);
  }

  function runAction() {
    setQuery('');
    setOpen(false);
    action?.onSelect();
  }

  function onKeyDown(event: KeyboardEvent<HTMLInputElement>) {
    if (event.key === 'ArrowDown') {
      event.preventDefault();
      if (!open) setOpen(true);
      else setActive((i) => Math.min(i + 1, rows.length - 1));
    } else if (event.key === 'ArrowUp') {
      event.preventDefault();
      setActive((i) => Math.max(i - 1, 0));
    } else if (event.key === 'Enter') {
      if (open && rows[active]) {
        event.preventDefault();
        rows[active].select();
      }
    } else if (event.key === 'Escape') {
      if (open) {
        // Close the list, not the dialog the field may be sitting in.
        event.preventDefault();
        event.stopPropagation();
        setOpen(false);
        setQuery('');
      }
    }
  }

  return (
    <div className={cn('relative', className)}>
      <div className="relative">
        <Search
          size={15}
          className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-text-subtle"
        />
        <input
          ref={inputRef}
          id={id}
          role="combobox"
          aria-expanded={open}
          aria-controls={listId}
          aria-autocomplete="list"
          aria-activedescendant={open && rows[active] ? `${listId}-${active}` : undefined}
          autoComplete="off"
          disabled={disabled}
          // Native validation only sees the visible text, which is the query
          // while open. Required is satisfied by a chosen value.
          required={required && !value}
          value={open ? query : value}
          placeholder={open && value ? value : placeholder}
          onChange={(e) => {
            setQuery(e.target.value);
            if (!open) setOpen(true);
          }}
          onFocus={() => setOpen(true)}
          onClick={() => setOpen(true)}
          onBlur={() => {
            setOpen(false);
            setQuery('');
          }}
          onKeyDown={onKeyDown}
          className={cn(
            'h-10 w-full truncate rounded-md border border-border bg-surface pl-9 pr-9 text-base text-text shadow-sm transition-colors sm:text-sm',
            'placeholder:text-text-subtle hover:border-border-strong focus:border-border-strong',
            'disabled:cursor-not-allowed disabled:opacity-60',
            open && value && 'placeholder:text-text-muted',
          )}
        />
        <ChevronDown
          size={16}
          className={cn(
            'pointer-events-none absolute right-3 top-1/2 -translate-y-1/2 text-text-subtle transition-transform',
            open && 'rotate-180',
          )}
        />
      </div>

      {open && !disabled && (
        <ul
          ref={listRef}
          id={listId}
          role="listbox"
          // mousedown, not click: the input's blur would close the list first.
          onMouseDown={(e) => e.preventDefault()}
          className="absolute z-50 mt-1.5 max-h-72 w-full overflow-y-auto overscroll-contain rounded-lg border border-border bg-overlay p-1 shadow-lg animate-fade-in"
        >
          {loading && <li className="px-3 py-2.5 text-sm text-text-subtle">Loading…</li>}

          {!loading && shown.length === 0 && !offerCustom && (
            <li className="px-3 py-2.5 text-sm text-text-subtle">{emptyText}</li>
          )}

          {shown.map((option, index) => (
            <li
              key={option}
              id={`${listId}-${index}`}
              data-index={index}
              role="option"
              aria-selected={option === value}
              onClick={() => choose(option)}
              onMouseMove={() => setActive(index)}
              className={cn(
                'flex cursor-pointer items-center justify-between gap-2 rounded-md px-3 py-2 text-sm',
                index === active ? 'bg-surface-sunken text-text' : 'text-text-muted',
              )}
            >
              <span className="truncate">{option}</span>
              {option === value && <Check size={15} className="shrink-0 text-text" />}
            </li>
          ))}

          {matches.length > shown.length && (
            <li className="px-3 py-2 text-xs text-text-subtle">
              {matches.length - shown.length} more — keep typing to narrow it down.
            </li>
          )}

          {offerCustom && (
            <li
              id={`${listId}-${shown.length}`}
              data-index={shown.length}
              role="option"
              aria-selected={false}
              onClick={() => choose(typed)}
              onMouseMove={() => setActive(shown.length)}
              className={cn(
                'cursor-pointer rounded-md border-t border-border px-3 py-2 text-sm',
                active === shown.length ? 'bg-surface-sunken text-text' : 'text-text-muted',
              )}
            >
              Use “<span className="font-medium text-text">{typed}</span>” — not on the list
            </li>
          )}

          {action && (
            <li
              id={`${listId}-${rows.length - 1}`}
              data-index={rows.length - 1}
              role="option"
              aria-selected={false}
              onClick={runAction}
              onMouseMove={() => setActive(rows.length - 1)}
              className={cn(
                'mt-1 flex cursor-pointer items-start gap-2 rounded-md border-t border-border px-3 py-2.5 text-sm',
                active === rows.length - 1 ? 'bg-surface-sunken text-text' : 'text-text-muted',
              )}
            >
              {action.icon}
              <span>
                <span className="font-medium text-text">{action.label}</span>
                {action.hint && <span className="block text-xs text-text-subtle">{action.hint}</span>}
              </span>
            </li>
          )}
        </ul>
      )}
    </div>
  );
}
