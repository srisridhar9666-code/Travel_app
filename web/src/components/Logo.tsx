import { cn } from '@/lib/utils';

type LogoVariant = 'full' | 'mark';

interface LogoProps {
  variant?: LogoVariant;
  className?: string;
}

/**
 * The wordmark is baked into the raster, so the asset itself has to swap with
 * the theme - black lettering on light, white lettering on dark. `mark` is the
 * wordmark-free icon, which reads the same on both.
 *
 * Both full versions are in the page and CSS shows one. Swapping the `src`
 * from React state lagged the theme switch and was wrong until the stored
 * theme had loaded; `dark:` follows `data-theme` on <html> the instant it
 * changes, including before the app has started.
 */
export function Logo({ variant = 'full', className }: LogoProps) {
  const base = cn('select-none object-contain', className);

  if (variant === 'mark') {
    return <img src="/brand/mark.png" alt="DesignBoxed" draggable={false} className={base} />;
  }

  return (
    <>
      <img
        src="/brand/logo-light.png"
        alt="DesignBoxed"
        draggable={false}
        className={cn(base, 'dark:hidden')}
      />
      <img
        src="/brand/logo-dark.png"
        alt="DesignBoxed"
        draggable={false}
        className={cn(base, 'hidden dark:block')}
      />
    </>
  );
}

/**
 * Logo plus product name, for the sidebar header and the sign-in screens. The
 * company name follows the theme the way the full logo's lettering does:
 * black on light, white on dark.
 */
export function LogoLockup({ className }: { className?: string }) {
  return (
    <div className={cn('flex min-w-0 items-center gap-3', className)}>
      <Logo variant="mark" className="h-12 w-12 shrink-0" />
      <div className="flex min-w-0 flex-col">
        <span className="truncate text-xl font-bold leading-6 tracking-tight text-text">
          Travel Ops
        </span>
        <span className="truncate text-[0.8125rem] font-semibold uppercase leading-5 tracking-[0.18em] text-text">
          DesignBoxed
        </span>
      </div>
    </div>
  );
}
