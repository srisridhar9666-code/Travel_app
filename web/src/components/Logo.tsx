import { cn } from '@/lib/utils';
import { useTheme } from '@/store/theme';

type LogoVariant = 'full' | 'mark';

interface LogoProps {
  variant?: LogoVariant;
  className?: string;
}

/**
 * The wordmark is baked into the raster, so the asset itself has to swap with
 * the theme - black lettering on light, white lettering on dark. `mark` is the
 * wordmark-free icon for tight spaces such as a collapsed nav rail.
 */
export function Logo({ variant = 'full', className }: LogoProps) {
  const resolved = useTheme((state) => state.resolved);

  const src =
    variant === 'mark'
      ? '/brand/mark.png'
      : resolved === 'dark'
        ? '/brand/logo-dark.png'
        : '/brand/logo-light.png';

  return (
    <img
      src={src}
      alt="DesignBoxed"
      draggable={false}
      className={cn('select-none object-contain', className)}
    />
  );
}

/** Logo plus product name, for the sidebar header and the login screen. */
export function LogoLockup({ className }: { className?: string }) {
  return (
    <div className={cn('flex items-center gap-3', className)}>
      <Logo variant="mark" className="h-9 w-9 shrink-0" />
      <div className="flex min-w-0 flex-col leading-tight">
        <span className="truncate text-[0.9375rem] font-semibold tracking-tight">Travel Ops</span>
        <span className="truncate text-2xs font-medium uppercase tracking-widest text-text-subtle">
          DesignBoxed
        </span>
      </div>
    </div>
  );
}
