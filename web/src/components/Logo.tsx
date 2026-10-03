import { cn } from '@/lib/utils';

type LogoVariant = 'full' | 'mark';

interface LogoProps {
  variant?: LogoVariant;
  className?: string;
}

/**
 * The Sriyatra wordmark is baked into the raster, so the asset itself has to
 * swap with the theme - navy lettering on light, white lettering on dark.
 * `mark` is the app-icon tile alone, which reads the same on both.
 *
 * Both full versions are in the page and CSS shows one. Swapping the `src`
 * from React state lagged the theme switch and was wrong until the stored
 * theme had loaded; `dark:` follows `data-theme` on <html> the instant it
 * changes, including before the app has started.
 */
export function Logo({ variant = 'full', className }: LogoProps) {
  const base = cn('select-none object-contain', className);

  if (variant === 'mark') {
    return <img src="/brand/mark.png" alt="Sriyatra" draggable={false} className={base} />;
  }

  return (
    <>
      <img
        src="/brand/logo-light.png"
        alt="Sriyatra - Your Travel Desk"
        draggable={false}
        className={cn(base, 'dark:hidden')}
      />
      <img
        src="/brand/logo-dark.png"
        alt="Sriyatra - Your Travel Desk"
        draggable={false}
        className={cn(base, 'hidden dark:block')}
      />
    </>
  );
}

/**
 * Icon plus product name, for the sidebar header and the sign-in screens. The
 * name follows the theme the way the full logo's lettering does: navy on
 * light, white on dark, in capitals and a serif like the wordmark.
 */
export function LogoLockup({ className }: { className?: string }) {
  return (
    <div className={cn('flex min-w-0 items-center gap-3', className)}>
      <Logo variant="mark" className="h-12 w-12 shrink-0" />
      <div className="flex min-w-0 flex-col">
        <span className="truncate font-serif text-xl font-bold uppercase leading-6 tracking-[0.04em] text-brand-strong dark:text-text">
          Sriyatra
        </span>
        <span className="truncate text-[0.8125rem] font-medium leading-5 tracking-wide text-text-muted">
          Your Travel Desk
        </span>
      </div>
    </div>
  );
}

/** Whose work this is, shown under the sidebar and on the sign-in screens. */
export const CREATED_BY = 'Sridhar';

export function CreatorCredit({ className }: { className?: string }) {
  return (
    <p className={cn('text-2xs text-text-subtle', className)}>
      Created by <span className="font-medium text-text-muted">{CREATED_BY}</span>
    </p>
  );
}
