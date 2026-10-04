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
 * Icon plus the Sriyatra wordmark, for the sidebar header and the sign-in
 * screens. The wordmark is cut from the supplied logo rather than typed, so it
 * is the brand's own lettering (with the pin over the I); like the full logo it
 * swaps with the theme - navy on light, white on dark.
 */
export function LogoLockup({
  className,
  markOnly = false,
}: {
  className?: string;
  /** Just the icon, for the collapsed sidebar rail. */
  markOnly?: boolean;
}) {
  const wordmark = 'h-11 w-auto max-w-[9.5rem] select-none object-contain object-left';
  return (
    <div className={cn('flex min-w-0 items-center gap-2.5', className)}>
      <Logo variant="mark" className={cn('shrink-0', markOnly ? 'h-10 w-10' : 'h-11 w-11')} />
      {!markOnly && (
        <>
          <img
            src="/brand/wordmark-light.png"
            alt="Sriyatra - Your Travel Desk"
            draggable={false}
            className={cn(wordmark, 'dark:hidden')}
          />
          <img
            src="/brand/wordmark-dark.png"
            alt="Sriyatra - Your Travel Desk"
            draggable={false}
            className={cn(wordmark, 'hidden dark:block')}
          />
        </>
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
  short = false,
}: {
  className?: string;
  /** "DesignBoxed" rather than the legal name, where a line is short. */
  short?: boolean;
}) {
  return (
    <div className={cn('flex items-center gap-2 text-2xs text-text-subtle', className)}>
      <img
        src="/brand/company-mark.png"
        alt=""
        draggable={false}
        className="h-6 w-6 shrink-0 select-none object-contain"
      />
      <span>
        A product of{' '}
        <span className="font-medium text-text-muted">{short ? 'DesignBoxed' : COMPANY_NAME}</span>
      </span>
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
