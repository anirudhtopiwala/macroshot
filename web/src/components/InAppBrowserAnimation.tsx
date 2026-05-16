/**
 * Shown when the user opened MacroShot inside an in-app browser
 * (Instagram, Facebook, TikTok, Gmail, LinkedIn etc.) - installation is
 * impossible from a webview. Tells them to open the page in Safari.
 *
 * Animation: finger pulses on the "⋯" menu, then the "Open in Safari"
 * item highlights.
 */

export default function InAppBrowserAnimation() {
  return (
    <div className="inapp-anim" role="img" aria-label="Open this page in Safari to install">
      <style>{CSS}</style>
      <svg viewBox="0 0 220 360" width="100%" height="100%" preserveAspectRatio="xMidYMid meet">
        <defs>
          <linearGradient id="inappScreenBg" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor="var(--anim-screen-top)" />
            <stop offset="100%" stopColor="var(--anim-screen-bottom)" />
          </linearGradient>
          <clipPath id="inappIconClip"><rect x="74" y="130" width="72" height="72" rx="18" /></clipPath>
        </defs>

        {/* Phone body */}
        <rect x="6" y="4" width="208" height="352" rx="34" ry="34" fill="var(--anim-phone-body)" stroke="var(--anim-phone-bezel)" strokeWidth="1.5" />
        <rect x="12" y="10" width="196" height="340" rx="28" fill="url(#inappScreenBg)" />

        {/* Status bar */}
        <text x="28" y="26" className="ia-status">9:41</text>
        <rect x="84" y="18" width="52" height="14" rx="7" fill="#000" />

        {/* In-app browser chrome */}
        <rect x="12" y="36" width="196" height="36" fill="var(--anim-inapp-chrome)" />
        {/* Back arrow */}
        <path d="M 28 54 L 22 48 L 22 60 Z" fill="var(--anim-inapp-fg)" />
        {/* URL pill */}
        <rect x="40" y="44" width="138" height="20" rx="10" fill="var(--anim-inapp-url)" />
        <text x="109" y="58" className="ia-url" textAnchor="middle">macroshot.app</text>
        {/* Three-dot menu */}
        <g>
          <circle cx="190" cy="50" r="1.5" fill="var(--anim-inapp-fg)" />
          <circle cx="190" cy="54" r="1.5" fill="var(--anim-inapp-fg)" />
          <circle cx="190" cy="58" r="1.5" fill="var(--anim-inapp-fg)" />
        </g>

        {/* Phase A: finger on the 3-dot menu */}
        <g className="ia-phase ia-phase-a">
          <circle className="ia-pulse" cx="190" cy="54" r="12" />
          {/* Hint arrow pointing up-right to the menu */}
          <g className="ia-hint">
            <path d="M 170 90 L 186 60 M 180 62 L 186 60 L 187 66" stroke="#10b981" strokeWidth="2" fill="none" strokeLinecap="round" strokeLinejoin="round" />
          </g>
          {/* Logo app icon preview - real MacroShot logo */}
          <image
            href="/macro_app/icons/icon-192.png"
            x="74"
            y="130"
            width="72"
            height="72"
            preserveAspectRatio="xMidYMid slice"
            clipPath="url(#inappIconClip)"
          />
          <text x="110" y="228" className="ia-title" textAnchor="middle">MacroShot</text>
          <text x="110" y="246" className="ia-sub" textAnchor="middle">To install, open this page</text>
          <text x="110" y="260" className="ia-sub" textAnchor="middle">in Safari first.</text>
        </g>

        {/* Phase B: dropdown menu appears with "Open in Safari" highlighted */}
        <g className="ia-phase ia-phase-b">
          <rect x="104" y="78" width="104" height="142" rx="10" fill="var(--anim-menu-bg)" stroke="var(--anim-menu-border)" strokeWidth="1" />
          {/* Menu rows */}
          <g opacity="0.55">
            <text x="116" y="100" className="ia-menu-row">Copy Link</text>
            <line x1="110" y1="110" x2="202" y2="110" stroke="var(--anim-menu-divider)" strokeWidth="0.5" />
          </g>
          <g opacity="0.55">
            <text x="116" y="126" className="ia-menu-row">Share…</text>
            <line x1="110" y1="136" x2="202" y2="136" stroke="var(--anim-menu-divider)" strokeWidth="0.5" />
          </g>
          {/* Highlighted row: Open in Safari / External */}
          <rect className="ia-row-highlight" x="108" y="142" width="96" height="28" rx="6" />
          <g transform="translate(116, 152)">
            {/* Safari compass-ish glyph */}
            <circle cx="6" cy="6" r="6" fill="none" stroke="#10b981" strokeWidth="1.5" />
            <path d="M 6 2 L 8 6 L 6 10 L 4 6 Z" fill="#10b981" />
          </g>
          <text x="134" y="161" className="ia-menu-row-strong">Open in Safari</text>
          <g opacity="0.55">
            <line x1="110" y1="178" x2="202" y2="178" stroke="var(--anim-menu-divider)" strokeWidth="0.5" />
            <text x="116" y="194" className="ia-menu-row">Report</text>
          </g>
          {/* Finger pulse on the row */}
          <circle className="ia-pulse delayed" cx="190" cy="156" r="11" />
        </g>

        {/* Home indicator */}
        <rect x="84" y="346" width="52" height="3" rx="1.5" fill="var(--anim-text-dim)" opacity="0.6" />
      </svg>

      <div className="ia-caption">
        <span className="ia-cap ia-cap-a">Tap the ⋯ menu</span>
        <span className="ia-cap ia-cap-b">Choose "Open in Safari"</span>
      </div>
    </div>
  );
}

const CSS = `
.inapp-anim {
  --anim-phone-body: #1a1f2e;
  --anim-phone-bezel: #2a3142;
  --anim-screen-top: #f8fafc;
  --anim-screen-bottom: #e2e8f0;
  --anim-inapp-chrome: #ffffff;
  --anim-inapp-fg: #334155;
  --anim-inapp-url: rgba(0,0,0,0.06);
  --anim-menu-bg: #ffffff;
  --anim-menu-border: rgba(0,0,0,0.1);
  --anim-menu-divider: rgba(0,0,0,0.08);
  --anim-text-dim: #64748b;
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 10px;
  width: 100%;
  max-width: 240px;
  margin: 0 auto;
}
[data-theme="light"] .inapp-anim {
  --anim-phone-body: #475569;
  --anim-phone-bezel: #64748b;
}
.inapp-anim svg { width: 100%; height: auto; filter: drop-shadow(0 10px 20px rgba(0,0,0,0.18)); }
.inapp-anim text { font-family: 'Inter', system-ui, sans-serif; }
.inapp-anim .ia-status { font-size: 9px; font-weight: 600; fill: #0f172a; }
.inapp-anim .ia-url { font-size: 9px; font-weight: 500; fill: var(--anim-inapp-fg); }
.inapp-anim .ia-title { font-size: 14px; font-weight: 700; fill: #0f172a; }
.inapp-anim .ia-sub { font-size: 10px; font-weight: 500; fill: var(--anim-text-dim); }
.inapp-anim .ia-menu-row { font-size: 9px; font-weight: 500; fill: #475569; }
.inapp-anim .ia-menu-row-strong { font-size: 9px; font-weight: 700; fill: #10b981; }

.inapp-anim .ia-phase { opacity: 0; }
@keyframes iaPhaseA { 0%, 38% { opacity: 1; } 44%, 100% { opacity: 0; } }
@keyframes iaPhaseB { 0%, 38% { opacity: 0; } 44%, 96% { opacity: 1; } 100% { opacity: 0; } }
.inapp-anim .ia-phase-a { animation: iaPhaseA 8s infinite; }
.inapp-anim .ia-phase-b { animation: iaPhaseB 8s infinite; }

@keyframes iaPulse { 0% { r: 6; opacity: 0.9; } 70% { r: 16; opacity: 0; } 100% { r: 16; opacity: 0; } }
.inapp-anim .ia-pulse { fill: none; stroke: #10b981; stroke-width: 2; animation: iaPulse 1.5s ease-out infinite; }
.inapp-anim .ia-pulse.delayed { animation-delay: 0.4s; }

@keyframes iaHintBob { 0%,100% { transform: translateY(0); opacity: 0.85; } 50% { transform: translateY(-4px); opacity: 1; } }
.inapp-anim .ia-hint { animation: iaHintBob 1.5s ease-in-out infinite; }

@keyframes iaRowFlash {
  0%, 20% { fill: rgba(16,185,129,0); }
  40%, 80% { fill: rgba(16,185,129,0.22); }
  100% { fill: rgba(16,185,129,0); }
}
.inapp-anim .ia-row-highlight { animation: iaRowFlash 2.5s ease-in-out infinite; }

.inapp-anim .ia-caption { position: relative; height: 18px; width: 100%; text-align: center; font-size: 11px; font-weight: 600; color: var(--text-secondary); }
.inapp-anim .ia-cap { position: absolute; inset: 0; opacity: 0; }
.inapp-anim .ia-cap-a { animation: iaPhaseA 8s infinite; }
.inapp-anim .ia-cap-b { animation: iaPhaseB 8s infinite; color: #10b981; }

@media (prefers-reduced-motion: reduce) {
  .inapp-anim .ia-phase-a { opacity: 1; animation: none; }
  .inapp-anim .ia-phase-b { display: none; }
  .inapp-anim .ia-pulse, .inapp-anim .ia-hint, .inapp-anim .ia-row-highlight { animation: none; }
  .inapp-anim .ia-cap-a { opacity: 1; }
  .inapp-anim .ia-cap-b { display: none; }
}
`;
