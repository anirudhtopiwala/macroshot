/**
 * Animated iPhone/iPad showing the "Add to Home Screen" flow.
 * Pure SVG + CSS - no video, theme-aware, respects prefers-reduced-motion.
 *
 * iPhone (14s loop, 5 phases) - reflects iOS 18+ Compact Safari default,
 * where Share is nested inside the ··· menu on the floating URL pill:
 *  0  tap ··· on the bottom pill
 *  A  mini-menu opens, tap Share
 *  B  share sheet slides up, tap Add to Home Screen
 *  C  preview, tap Add
 *  D  home screen lands
 *
 * iPad (12s loop, 4 phases) - unchanged: Share icon sits top-right next
 * to the URL bar, no ··· menu.
 */

export default function IOSInstallAnimation({ variant = 'ios' }: { variant?: 'ios' | 'ipad' }) {
  const isIpad = variant === 'ipad';
  const containerClass = `ios-install-anim ${isIpad ? 'variant-ipad' : 'variant-iphone'}`;

  return (
    <div className={containerClass} role="img" aria-label="Animated guide: tap menu, then Share, then Add to Home Screen">
      <style>{CSS}</style>
      <svg viewBox="0 0 220 360" width="100%" height="100%" preserveAspectRatio="xMidYMid meet">
        <defs>
          <linearGradient id="iosScreenBg" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor="var(--anim-screen-top)" />
            <stop offset="100%" stopColor="var(--anim-screen-bottom)" />
          </linearGradient>
          <linearGradient id="iosHomeBg" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor="#1e3a8a" />
            <stop offset="100%" stopColor="#0f172a" />
          </linearGradient>
          <clipPath id="iosIconClipSm"><rect x="36" y="168" width="28" height="28" rx="6" /></clipPath>
          <clipPath id="iosIconClipMd"><rect x="36" y="88" width="46" height="46" rx="10" /></clipPath>
          <clipPath id="iosIconClipHome"><rect x="74" y="118" width="32" height="32" rx="8" /></clipPath>
        </defs>

        {/* Phone body */}
        <rect x="6" y="4" width="208" height="352" rx="34" ry="34"
          fill="var(--anim-phone-body)" stroke="var(--anim-phone-bezel)" strokeWidth="1.5" />
        <rect x="4" y="80" width="3" height="28" rx="1.5" fill="var(--anim-phone-bezel)" />
        <rect x="4" y="118" width="3" height="44" rx="1.5" fill="var(--anim-phone-bezel)" />
        <rect x="213" y="94" width="3" height="60" rx="1.5" fill="var(--anim-phone-bezel)" />
        <rect x="12" y="10" width="196" height="340" rx="28" ry="28" fill="url(#iosScreenBg)" />

        {/* iPhone-only: Phase 0 - tap ··· on the bottom compact pill */}
        {!isIpad && (
          <g className="phase phase-0">
            <SafariChrome isIpad={false} />
            <PageContent />
            <CompactPill />
            <circle className="finger-pulse" cx="166" cy="332" r="11" />
            <g className="hint-arrow">
              <path d="M 166 300 L 166 320 M 160 314 L 166 320 L 172 314"
                stroke="#10b981" strokeWidth="2" fill="none" strokeLinecap="round" strokeLinejoin="round" />
            </g>
          </g>
        )}

        {/* Phase A - iPhone: mini-menu from ··· with Share highlighted.
                     iPad: tap Share button directly (top-right). */}
        <g className="phase phase-a">
          <SafariChrome isIpad={isIpad} />
          <PageContent />
          {isIpad ? (
            <>
              <g transform="translate(178, 52)">
                <ShareIconGlyph highlight />
              </g>
              <circle className="finger-pulse" cx="178" cy="54" r="10" />
            </>
          ) : (
            <>
              {/* Keep the compact pill visible while the menu is open */}
              <CompactPill />
              {/* Mini menu popping up above the ··· button */}
              <g className="menu-popup">
                <rect x="78" y="212" width="122" height="96" rx="12"
                  fill="var(--anim-sheet-bg)" stroke="var(--anim-sheet-border)" strokeWidth="1" />
                {/* Arrow/tail pointing to the ··· */}
                <path d="M 160 308 L 166 316 L 172 308 Z" fill="var(--anim-sheet-bg)" stroke="var(--anim-sheet-border)" strokeWidth="1" />
                <rect x="161" y="307" width="10" height="3" fill="var(--anim-sheet-bg)" />
                {/* Row: Share (highlighted) */}
                <rect className="row-highlight" x="82" y="218" width="114" height="22" rx="6" />
                <g transform="translate(95, 230)">
                  <ShareIconGlyph highlight />
                </g>
                <text x="112" y="233" className="anim-menu-label">Share</text>
                {/* Decoys */}
                <g opacity="0.55">
                  <text x="95" y="257" className="anim-menu-label-dim">Copy</text>
                  <text x="95" y="277" className="anim-menu-label-dim">Find on Page</text>
                  <text x="95" y="297" className="anim-menu-label-dim">Request Desktop</text>
                </g>
              </g>
              <circle className="finger-pulse delayed" cx="112" cy="229" r="12" />
            </>
          )}
        </g>

        {/* Phase B - share sheet slides up, tap Add to Home Screen */}
        <g className="phase phase-b">
          <rect x="12" y="10" width="196" height="340" rx="28" fill="rgba(0,0,0,0.35)" />
          <g className="sheet-slide">
            <rect x="16" y="140" width="188" height="210" rx="18"
              fill="var(--anim-sheet-bg)" stroke="var(--anim-sheet-border)" strokeWidth="1" />
            <rect x="100" y="148" width="20" height="3" rx="1.5" fill="var(--anim-text-dim)" opacity="0.5" />
            <rect x="28" y="160" width="164" height="44" rx="10" fill="var(--anim-sheet-card)" />
            <image href="/macro_app/icons/icon-192.png" x="36" y="168" width="28" height="28"
              preserveAspectRatio="xMidYMid slice" clipPath="url(#iosIconClipSm)" />
            <rect x="72" y="170" width="90" height="6" rx="3" fill="var(--anim-text-strong)" />
            <rect x="72" y="182" width="70" height="5" rx="2.5" fill="var(--anim-text-dim)" />
            <rect className="row-highlight" x="24" y="234" width="172" height="36" rx="10" />
            <g transform="translate(36, 244)">
              <rect x="0" y="0" width="16" height="16" rx="3" fill="none" stroke="var(--anim-text-strong)" strokeWidth="1.5" />
              <path d="M 8 4 V 12 M 4 8 H 12" stroke="var(--anim-text-strong)" strokeWidth="1.5" strokeLinecap="round" />
            </g>
            <text x="62" y="256" className="anim-row-label">Add to Home Screen</text>
            <g opacity="0.55">
              <g transform="translate(36, 214)">
                <rect x="0" y="0" width="16" height="16" rx="3" fill="none" stroke="var(--anim-text-dim)" strokeWidth="1.5" />
                <path d="M 8 4 L 8 10 M 4 8 L 8 4 L 12 8 M 4 14 H 12" stroke="var(--anim-text-dim)" strokeWidth="1.5" strokeLinecap="round" fill="none" />
              </g>
              <text x="62" y="226" className="anim-row-label-dim">Copy</text>
            </g>
            <g opacity="0.55">
              <g transform="translate(36, 280)">
                <rect x="0" y="0" width="16" height="16" rx="3" fill="none" stroke="var(--anim-text-dim)" strokeWidth="1.5" />
                <path d="M 4 8 L 12 8" stroke="var(--anim-text-dim)" strokeWidth="1.5" strokeLinecap="round" />
              </g>
              <text x="62" y="292" className="anim-row-label-dim">Add to Reading List</text>
            </g>
            <circle className="finger-pulse delayed" cx="110" cy="252" r="13" />
          </g>
        </g>

        {/* Phase C - preview, tap Add */}
        <g className="phase phase-c">
          <rect x="12" y="10" width="196" height="340" rx="28" fill="var(--anim-sheet-bg)" />
          <text x="20" y="52" className="anim-topbar-dim">Cancel</text>
          <text x="110" y="52" className="anim-topbar" textAnchor="middle">Add to Home Screen</text>
          <text x="200" y="52" className="anim-topbar-add" textAnchor="end">Add</text>
          <circle className="finger-pulse" cx="190" cy="48" r="11" />
          <rect x="24" y="76" width="172" height="70" rx="14" fill="var(--anim-sheet-card)" />
          <image href="/macro_app/icons/icon-192.png" x="36" y="88" width="46" height="46"
            preserveAspectRatio="xMidYMid slice" clipPath="url(#iosIconClipMd)" />
          <rect x="92" y="92" width="70" height="8" rx="4" fill="var(--anim-text-strong)" />
          <rect x="92" y="108" width="90" height="6" rx="3" fill="var(--anim-text-dim)" />
          <rect x="92" y="120" width="50" height="6" rx="3" fill="var(--anim-text-dim)" opacity="0.6" />
          <rect x="24" y="162" width="172" height="36" rx="10" fill="var(--anim-sheet-card)" />
          <text x="36" y="184" className="anim-row-label">MacroShot</text>
        </g>

        {/* Phase D - home screen lands */}
        <g className="phase phase-d">
          <rect x="12" y="10" width="196" height="340" rx="28" fill="url(#iosHomeBg)" />
          <text x="28" y="26" className="anim-status" fill="#fff">9:41</text>
          <rect x="84" y="18" width="52" height="14" rx="7" fill="#000" />
          {[0, 1, 2, 3].map((row) =>
            [0, 1, 2, 3].map((col) => {
              const isTarget = row === 1 && col === 1;
              const x = 32 + col * 42;
              const y = 60 + row * 58;
              return (
                <g key={`${row}-${col}`}>
                  {isTarget ? (
                    <g className="app-drop">
                      <image
                        href="/macro_app/icons/icon-192.png"
                        x={x} y={y} width="32" height="32"
                        preserveAspectRatio="xMidYMid slice"
                        clipPath="url(#iosIconClipHome)"
                      />
                      <text x={x + 16} y={y + 46} className="anim-app-label" textAnchor="middle">MacroShot</text>
                      <rect className="app-glow" x={x - 4} y={y - 4} width="40" height="40" rx="11"
                        fill="none" stroke="#10b981" strokeWidth="2" />
                    </g>
                  ) : (
                    <>
                      <rect x={x} y={y} width="32" height="32" rx="8" fill="rgba(255,255,255,0.1)" />
                      <rect x={x + 4} y={y + 44} width="24" height="3" rx="1.5" fill="rgba(255,255,255,0.25)" />
                    </>
                  )}
                </g>
              );
            })
          )}
          <rect x="24" y="298" width="172" height="44" rx="20" fill="rgba(255,255,255,0.08)" />
          {[0, 1, 2, 3].map((i) => (
            <rect key={i} x={36 + i * 38} y="308" width="24" height="24" rx="6" fill="rgba(255,255,255,0.15)" />
          ))}
        </g>

        <rect x="84" y="346" width="52" height="3" rx="1.5" fill="var(--anim-text-dim)" opacity="0.6" />
      </svg>

      <div className="anim-caption">
        {!isIpad && <span className="cap cap-0">Step 1 - Tap the menu (···)</span>}
        <span className="cap cap-a">{isIpad ? 'Step 1' : 'Step 2'} - Tap the Share button</span>
        <span className="cap cap-b">{isIpad ? 'Step 2' : 'Step 3'} - Tap "Add to Home Screen"</span>
        <span className="cap cap-c">{isIpad ? 'Step 3' : 'Step 4'} - Tap "Add"</span>
        <span className="cap cap-d">Done - MacroShot is on your home screen</span>
      </div>

      {!isIpad && (
        <p className="anim-hint">Don't see the ··· button? Tap the bottom URL bar first to bring the toolbar back.</p>
      )}
    </div>
  );
}

function SafariChrome({ isIpad }: { isIpad: boolean }) {
  return (
    <>
      <text x="28" y="26" className="anim-status">9:41</text>
      <g transform="translate(172, 18)">
        <rect x="0" y="2" width="14" height="8" rx="1.5" fill="none" stroke="var(--anim-chrome-fg)" strokeWidth="1" />
        <rect x="1.5" y="3.5" width="9" height="5" rx="0.5" fill="var(--anim-chrome-fg)" />
      </g>
      <rect x="84" y="18" width="52" height="14" rx="7" fill="#000" />
      {isIpad && (
        <g>
          <rect x="24" y="40" width="140" height="24" rx="12" fill="var(--anim-chrome-bg)" />
          <text x="94" y="56" className="anim-url" textAnchor="middle">macroshot.app</text>
        </g>
      )}
    </>
  );
}

function PageContent() {
  return (
    <g opacity="0.95">
      <rect x="24" y="76" width="172" height="40" rx="10" fill="var(--anim-card)" />
      <rect x="32" y="84" width="24" height="24" rx="6" fill="var(--color-protein)" opacity="0.35" />
      <rect x="62" y="88" width="80" height="6" rx="3" fill="var(--anim-text-dim)" />
      <rect x="62" y="100" width="50" height="5" rx="2.5" fill="var(--anim-text-dim)" opacity="0.6" />

      <rect x="24" y="122" width="172" height="40" rx="10" fill="var(--anim-card)" />
      <rect x="32" y="130" width="24" height="24" rx="6" fill="var(--color-carbs)" opacity="0.35" />
      <rect x="62" y="134" width="70" height="6" rx="3" fill="var(--anim-text-dim)" />
      <rect x="62" y="146" width="60" height="5" rx="2.5" fill="var(--anim-text-dim)" opacity="0.6" />

      <rect x="24" y="168" width="172" height="40" rx="10" fill="var(--anim-card)" />
      <rect x="32" y="176" width="24" height="24" rx="6" fill="var(--color-fat)" opacity="0.35" />
      <rect x="62" y="180" width="88" height="6" rx="3" fill="var(--anim-text-dim)" />
      <rect x="62" y="192" width="44" height="5" rx="2.5" fill="var(--anim-text-dim)" opacity="0.6" />
    </g>
  );
}

/** iPhone compact pill at the bottom: URL text + ··· button. */
function CompactPill() {
  return (
    <>
      <rect x="32" y="318" width="156" height="28" rx="14" fill="var(--anim-chrome-bg)" />
      <text x="96" y="336" className="anim-url" textAnchor="middle">macroshot.app</text>
      <g transform="translate(160, 332)">
        <circle cx="0" cy="0" r="1.6" fill="var(--anim-chrome-fg)" />
        <circle cx="6" cy="0" r="1.6" fill="var(--anim-chrome-fg)" />
        <circle cx="12" cy="0" r="1.6" fill="var(--anim-chrome-fg)" />
      </g>
    </>
  );
}

function ShareIconGlyph({ highlight = false }: { highlight?: boolean }) {
  const color = highlight ? '#10b981' : 'var(--anim-chrome-fg)';
  return (
    <g>
      <rect x="-8" y="-1" width="16" height="14" rx="2" fill="none" stroke={color} strokeWidth="1.5" />
      <path d="M 0 -9 V 6 M -4 -5 L 0 -9 L 4 -5" stroke={color} strokeWidth="1.5" fill="none" strokeLinecap="round" strokeLinejoin="round" />
    </g>
  );
}

const CSS = `
.ios-install-anim {
  --anim-phone-body: #1a1f2e;
  --anim-phone-bezel: #2a3142;
  --anim-screen-top: #f8fafc;
  --anim-screen-bottom: #e2e8f0;
  --anim-chrome-bg: rgba(255,255,255,0.85);
  --anim-chrome-fg: #334155;
  --anim-card: rgba(255,255,255,0.9);
  --anim-sheet-bg: #f1f5f9;
  --anim-sheet-border: rgba(0,0,0,0.08);
  --anim-sheet-card: #ffffff;
  --anim-text-strong: #0f172a;
  --anim-text-dim: #64748b;
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 10px;
  width: 100%;
  max-width: 240px;
  margin: 0 auto;
}
[data-theme="light"] .ios-install-anim {
  --anim-phone-body: #475569;
  --anim-phone-bezel: #64748b;
}
.ios-install-anim svg {
  width: 100%;
  height: auto;
  filter: drop-shadow(0 10px 20px rgba(0,0,0,0.18));
}
.ios-install-anim .phase { opacity: 0; }
.ios-install-anim text { font-family: 'Inter', system-ui, sans-serif; }
.ios-install-anim .anim-status { font-size: 9px; font-weight: 600; fill: var(--anim-text-strong); }
.ios-install-anim .anim-url { font-size: 9px; font-weight: 500; fill: var(--anim-chrome-fg); }
.ios-install-anim .anim-row-label { font-size: 10px; font-weight: 600; fill: var(--anim-text-strong); }
.ios-install-anim .anim-row-label-dim { font-size: 10px; font-weight: 500; fill: var(--anim-text-dim); }
.ios-install-anim .anim-menu-label { font-size: 9px; font-weight: 600; fill: var(--anim-text-strong); }
.ios-install-anim .anim-menu-label-dim { font-size: 9px; font-weight: 500; fill: var(--anim-text-dim); }
.ios-install-anim .anim-topbar { font-size: 9px; font-weight: 700; fill: var(--anim-text-strong); }
.ios-install-anim .anim-topbar-dim { font-size: 9px; fill: #3b82f6; font-weight: 500; }
.ios-install-anim .anim-topbar-add { font-size: 10px; font-weight: 700; fill: #10b981; }
.ios-install-anim .anim-app-label { font-size: 8px; font-weight: 500; fill: #fff; }

/* iPad: classic 4-phase, 12s loop */
@keyframes iPadPhaseA { 0%, 22% { opacity: 1; } 28%, 100% { opacity: 0; } }
@keyframes iPadPhaseB { 0%, 22% { opacity: 0; } 28%, 50% { opacity: 1; } 56%, 100% { opacity: 0; } }
@keyframes iPadPhaseC { 0%, 50% { opacity: 0; } 56%, 75% { opacity: 1; } 81%, 100% { opacity: 0; } }
@keyframes iPadPhaseD { 0%, 75% { opacity: 0; } 81%, 98% { opacity: 1; } 100% { opacity: 0; } }
.variant-ipad .phase-a { animation: iPadPhaseA 12s infinite; }
.variant-ipad .phase-b { animation: iPadPhaseB 12s infinite; }
.variant-ipad .phase-c { animation: iPadPhaseC 12s infinite; }
.variant-ipad .phase-d { animation: iPadPhaseD 12s infinite; }
.variant-ipad .cap-a { animation: iPadPhaseA 12s infinite; }
.variant-ipad .cap-b { animation: iPadPhaseB 12s infinite; }
.variant-ipad .cap-c { animation: iPadPhaseC 12s infinite; }
.variant-ipad .cap-d { animation: iPadPhaseD 12s infinite; color: #10b981; }

/* iPhone: 5-phase, 14s loop.
   Timeline (% of 14s):
     0–14%   phase-0    tap ···
     17–31%  phase-a    menu popup, tap Share
     34–54%  phase-b    share sheet, tap Add to Home Screen
     57–74%  phase-c    preview, tap Add
     77–97%  phase-d    home screen lands */
@keyframes iPhonePhase0 { 0%, 14% { opacity: 1; } 17%, 100% { opacity: 0; } }
@keyframes iPhonePhaseA { 0%, 14% { opacity: 0; } 17%, 31% { opacity: 1; } 34%, 100% { opacity: 0; } }
@keyframes iPhonePhaseB { 0%, 31% { opacity: 0; } 34%, 54% { opacity: 1; } 57%, 100% { opacity: 0; } }
@keyframes iPhonePhaseC { 0%, 54% { opacity: 0; } 57%, 74% { opacity: 1; } 77%, 100% { opacity: 0; } }
@keyframes iPhonePhaseD { 0%, 74% { opacity: 0; } 77%, 97% { opacity: 1; } 100% { opacity: 0; } }
.variant-iphone .phase-0 { animation: iPhonePhase0 14s infinite; }
.variant-iphone .phase-a { animation: iPhonePhaseA 14s infinite; }
.variant-iphone .phase-b { animation: iPhonePhaseB 14s infinite; }
.variant-iphone .phase-c { animation: iPhonePhaseC 14s infinite; }
.variant-iphone .phase-d { animation: iPhonePhaseD 14s infinite; }
.variant-iphone .cap-0 { animation: iPhonePhase0 14s infinite; }
.variant-iphone .cap-a { animation: iPhonePhaseA 14s infinite; }
.variant-iphone .cap-b { animation: iPhonePhaseB 14s infinite; }
.variant-iphone .cap-c { animation: iPhonePhaseC 14s infinite; }
.variant-iphone .cap-d { animation: iPhonePhaseD 14s infinite; color: #10b981; }

/* Menu popup appears with a subtle scale/fade - iPhone Phase A */
@keyframes menuPopIn {
  0%, 14% { opacity: 0; transform: scale(0.92) translateY(6px); }
  19%, 31% { opacity: 1; transform: scale(1) translateY(0); }
  100% { opacity: 1; transform: scale(1) translateY(0); }
}
.variant-iphone .menu-popup {
  animation: menuPopIn 14s cubic-bezier(0.22, 1, 0.36, 1) infinite;
  transform-origin: 166px 316px;
  transform-box: fill-box;
}

/* Finger pulse */
@keyframes fingerPulse {
  0% { r: 6; opacity: 0.9; }
  70% { r: 16; opacity: 0; }
  100% { r: 16; opacity: 0; }
}
.ios-install-anim .finger-pulse {
  fill: none;
  stroke: #10b981;
  stroke-width: 2;
  animation: fingerPulse 1.6s ease-out infinite;
  transform-origin: center;
}
.ios-install-anim .finger-pulse.delayed { animation-delay: 0.5s; }

@keyframes hintBob { 0%,100% { transform: translateY(0); opacity: 0.85; } 50% { transform: translateY(-4px); opacity: 1; } }
.ios-install-anim .hint-arrow { animation: hintBob 1.6s ease-in-out infinite; transform-origin: center; }

/* Share sheet slides up - iPad (12s) */
@keyframes sheetSlideIpad {
  0%, 22% { transform: translateY(180px); }
  30% { transform: translateY(0); }
  100% { transform: translateY(0); }
}
.variant-ipad .sheet-slide {
  animation: sheetSlideIpad 12s cubic-bezier(0.22, 1, 0.36, 1) infinite;
  transform-box: fill-box;
}

/* Share sheet slides up - iPhone (14s) */
@keyframes sheetSlideIphone {
  0%, 31% { transform: translateY(180px); }
  37% { transform: translateY(0); }
  100% { transform: translateY(0); }
}
.variant-iphone .sheet-slide {
  animation: sheetSlideIphone 14s cubic-bezier(0.22, 1, 0.36, 1) infinite;
  transform-box: fill-box;
}

/* Row-flash highlight */
@keyframes rowFlash {
  0%, 30% { fill: rgba(16,185,129,0); }
  50%, 85% { fill: rgba(16,185,129,0.22); }
  100% { fill: rgba(16,185,129,0); }
}
.ios-install-anim .row-highlight { animation: rowFlash 3s ease-in-out infinite; }

/* App drop onto home screen - iPad (12s) */
@keyframes appDropIpad {
  0%, 75% { transform: scale(0) translateY(-40px); opacity: 0; }
  82% { transform: scale(1.2) translateY(0); opacity: 1; }
  86% { transform: scale(0.95) translateY(0); }
  90%, 100% { transform: scale(1) translateY(0); opacity: 1; }
}
.variant-ipad .app-drop {
  animation: appDropIpad 12s cubic-bezier(0.34, 1.56, 0.64, 1) infinite;
  transform-origin: center;
  transform-box: fill-box;
}
@keyframes appGlowIpad {
  0%, 82% { opacity: 0; }
  88% { opacity: 1; }
  94% { opacity: 0.3; }
  98% { opacity: 1; }
  100% { opacity: 0; }
}
.variant-ipad .app-glow { animation: appGlowIpad 12s ease-in-out infinite; }

/* App drop onto home screen - iPhone (14s) */
@keyframes appDropIphone {
  0%, 74% { transform: scale(0) translateY(-40px); opacity: 0; }
  81% { transform: scale(1.2) translateY(0); opacity: 1; }
  85% { transform: scale(0.95) translateY(0); }
  89%, 100% { transform: scale(1) translateY(0); opacity: 1; }
}
.variant-iphone .app-drop {
  animation: appDropIphone 14s cubic-bezier(0.34, 1.56, 0.64, 1) infinite;
  transform-origin: center;
  transform-box: fill-box;
}
@keyframes appGlowIphone {
  0%, 81% { opacity: 0; }
  87% { opacity: 1; }
  93% { opacity: 0.3; }
  97% { opacity: 1; }
  100% { opacity: 0; }
}
.variant-iphone .app-glow { animation: appGlowIphone 14s ease-in-out infinite; }

/* Caption crossfade */
.ios-install-anim .anim-caption {
  position: relative;
  height: 18px;
  width: 100%;
  text-align: center;
  font-size: 11px;
  font-weight: 600;
  color: var(--text-secondary);
}
.ios-install-anim .cap {
  position: absolute;
  inset: 0;
  opacity: 0;
}

/* Toolbar-hidden hint below the animation */
.ios-install-anim .anim-hint {
  margin: 4px 0 0;
  font-size: 10px;
  line-height: 1.4;
  text-align: center;
  color: var(--text-muted);
  max-width: 260px;
}

@media (prefers-reduced-motion: reduce) {
  .variant-iphone .phase-0 { opacity: 1; animation: none; }
  .variant-ipad .phase-a { opacity: 1; animation: none; }
  .variant-iphone .phase-a,
  .ios-install-anim .phase-b,
  .ios-install-anim .phase-c,
  .ios-install-anim .phase-d { display: none; }
  .ios-install-anim .finger-pulse,
  .ios-install-anim .hint-arrow,
  .ios-install-anim .row-highlight,
  .ios-install-anim .app-drop,
  .ios-install-anim .app-glow,
  .ios-install-anim .sheet-slide,
  .ios-install-anim .menu-popup,
  .ios-install-anim .cap-a,
  .ios-install-anim .cap-b,
  .ios-install-anim .cap-c,
  .ios-install-anim .cap-d { animation: none; }
  .variant-iphone .cap-0 { opacity: 1; animation: none; }
  .variant-ipad .cap-a { opacity: 1; animation: none; }
}
`;
