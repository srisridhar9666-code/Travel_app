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
export function LogoLockup({
  className,
  onDark = false,
  markOnly = false,
}: {
  className?: string;
  /** White lettering, for the navy sidebar in either theme. */
  onDark?: boolean;
  /** Just the icon, for the collapsed sidebar rail. */
  markOnly?: boolean;
}) {
  return (
    <div className={cn('flex min-w-0 items-center gap-3', className)}>
      <Logo variant="mark" className={cn('shrink-0', markOnly ? 'h-10 w-10' : 'h-11 w-11')} />
      {!markOnly && (
        <div className="flex min-w-0 flex-col">
          <span
            className={cn(
              'truncate font-serif text-xl font-bold uppercase leading-6 tracking-[0.04em]',
              onDark ? 'text-white' : 'text-brand-strong dark:text-text',
            )}
          >
            Sriyatra
          </span>
          <span
            className={cn(
              'truncate text-[0.8125rem] font-medium leading-5 tracking-wide',
              onDark ? 'text-white/70' : 'text-text-muted',
            )}
          >
            Your Travel Desk
          </span>
        </div>
      )}
    </div>
  );
}

/** The company behind the app. */
export const COMPANY_NAME = 'DesignBoxed Innovations Pvt. Ltd.';

/**
 * The DesignBoxed logo. Its wordmark is in the raster too, so like the app's
 * logo it swaps with the theme: black lettering on light, white on dark.
 */
export function CompanyLogo({ className }: { className?: string }) {
  const base = cn('select-none object-contain', className);
  return (
    <>
      <img
        src="/brand/company-logo-light.png"
        alt="DesignBoxed"
        draggable={false}
        className={cn(base, 'dark:hidden')}
      />
      <img
        src="/brand/company-logo-dark.png"
        alt="DesignBoxed"
        draggable={false}
        className={cn(base, 'hidden dark:block')}
      />
    </>
  );
}

/** "A product of DesignBoxed", with its mark: the sidebar and form footers. */
export function CompanyCredit({
  className,
  onDark = false,
  short = false,
}: {
  className?: string;
  onDark?: boolean;
  /** "DesignBoxed" rather than the legal name, where a line is short. */
  short?: boolean;
}) {
  return (
    <div
      className={cn(
        'flex items-center gap-2 text-2xs',
        onDark ? 'text-white/60' : 'text-text-subtle',
        className,
      )}
    >
      <img
        src="/brand/company-mark.png"
        alt=""
        draggable={false}
        className="h-6 w-6 shrink-0 select-none object-contain"
      />
      <span>
        A product of{' '}
        <span className={cn('font-medium', onDark ? 'text-white/80' : 'text-text-muted')}>
          {short ? 'DesignBoxed' : COMPANY_NAME}
        </span>
      </span>
    </div>
  );
}

/** Whose work this is, shown under the sidebar and on the sign-in screens. */
export const CREATED_BY = 'Sridhar';

export function CreatorCredit({ className, onDark = false }: { className?: string; onDark?: boolean }) {
  return (
    <p className={cn('text-2xs', onDark ? 'text-white/60' : 'text-text-subtle', className)}>
      Created by{' '}
      <span className={cn('font-medium', onDark ? 'text-white/80' : 'text-text-muted')}>
        {CREATED_BY}
      </span>
    </p>
  );
}
