import { useState } from 'react';

interface PhoneMockupProps {
  src: string;
  alt: string;
  caption?: string;
  glow?: boolean;
  size?: 'sm' | 'md';
  className?: string;
}

const SIZE_MAX_WIDTH: Record<NonNullable<PhoneMockupProps['size']>, string> = {
  sm: '200px',
  md: '320px',
};

/**
 * Phone-frame mockup for marketing/landing screenshots.
 *
 * Attempts to load `src` as an image. If the file doesn't exist (e.g., a
 * marketing PNG that hasn't been added yet), falls back to a styled gradient
 * placeholder with the MacroShot wordmark. This lets the About page ship with
 * real screenshot slots wired up before the PNGs actually exist.
 */
export default function PhoneMockup({ src, alt, caption, glow = false, size = 'md', className = '' }: PhoneMockupProps) {
  const [errored, setErrored] = useState(false);
  const maxWidth = SIZE_MAX_WIDTH[size];
  const borderWidth = size === 'sm' ? '6px' : '8px';
  const borderRadius = size === 'sm' ? '2rem' : '2.5rem';

  return (
    <div className={`relative flex flex-col items-center ${className}`}>
      {glow && (
        <div
          aria-hidden
          className="absolute inset-0 -z-10 pointer-events-none"
          style={{
            background:
              'radial-gradient(circle at 50% 50%, rgba(16,185,129,0.18) 0%, rgba(168,85,247,0.10) 45%, transparent 70%)',
            filter: 'blur(40px)',
          }}
        />
      )}

      <div
        className="relative overflow-hidden"
        style={{
          width: '100%',
          maxWidth,
          aspectRatio: '9 / 19.5',
          borderRadius,
          border: `${borderWidth} solid rgba(15, 23, 42, 0.85)`,
          boxShadow:
            '0 25px 60px -15px rgba(0, 0, 0, 0.6), 0 0 0 2px rgba(255, 255, 255, 0.06), inset 0 0 0 1px rgba(255, 255, 255, 0.05)',
          background: '#060a14',
        }}
      >
        {/* Notch */}
        <div
          aria-hidden
          className="absolute top-0 left-1/2 -translate-x-1/2 z-20"
          style={{
            width: '38%',
            height: '22px',
            background: '#000',
            borderBottomLeftRadius: '14px',
            borderBottomRightRadius: '14px',
          }}
        />

        {errored ? (
          <div
            className="absolute inset-0 flex flex-col items-center justify-center"
            style={{
              background:
                'linear-gradient(145deg, #0f172a 0%, #0a1220 40%, #0b2a22 100%)',
            }}
          >
            <div
              aria-hidden
              className="absolute inset-0 opacity-40"
              style={{
                background:
                  'radial-gradient(circle at 30% 20%, rgba(16,185,129,0.2) 0%, transparent 50%), radial-gradient(circle at 70% 80%, rgba(168,85,247,0.15) 0%, transparent 50%)',
              }}
            />
            <div className="relative z-10 flex flex-col items-center gap-3 px-4 text-center">
              <img
                src="/macro_app/logo-login.png"
                alt=""
                className="w-16 h-16 opacity-90"
              />
              <span
                className="text-base font-black italic tracking-wider"
                style={{ color: 'rgba(241, 245, 249, 0.85)' }}
              >
                MacroShot
              </span>
              <span
                className="text-[11px] uppercase tracking-widest"
                style={{ color: 'rgba(148, 163, 184, 0.6)' }}
              >
                Screenshot coming soon
              </span>
            </div>
          </div>
        ) : (
          <img
            src={src}
            alt={alt}
            onError={() => setErrored(true)}
            className="absolute inset-0 w-full h-full object-cover"
            loading="lazy"
          />
        )}
      </div>

      {caption && (
        <p
          className="mt-4 text-xs text-center max-w-[280px]"
          style={{ color: 'var(--text-muted)' }}
        >
          {caption}
        </p>
      )}
    </div>
  );
}
