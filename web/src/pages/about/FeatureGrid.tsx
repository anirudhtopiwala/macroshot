const FEATURES: Array<{ icon: string; title: string; body: string }> = [
  { icon: '📸', title: 'Log any way you want', body: 'Snap a photo, type a sentence, or scan a barcode. AI handles portions.' },
  { icon: '✨', title: 'AI Coach', body: 'Uses MCP to access your real data - 23 tools for meals, targets, weight, workouts, and long-term memory (allergies, restrictions, preferences).' },
  { icon: '📈', title: 'Trends & Analytics', body: '7/30/90-day charts for calories, macros, weight, and adherence streaks.' },
  { icon: '🏃', title: 'Strava + Fitbit + Oura', body: 'Auto-sync workouts. Coach adjusts calorie targets based on what you burned.' },
  { icon: '⚖️', title: 'Weight Tracking', body: 'Log weight in seconds. Trend line shows progress against your goal, not just today’s number.' },
  { icon: '⭐', title: 'Saved Meals', body: 'One-tap quick-log for your regulars - "Usual breakfast," "Post-workout shake." No re-typing.' },
  { icon: '🏆', title: 'Streaks & Achievements', body: '24 badges, Bronze to Diamond tiers. Streak shields let you recover from a missed day.' },
  { icon: '📔', title: 'Journal + Gallery', body: 'Browse meals in a list or a photo wall. Search, filter, swipe-to-delete.' },
  { icon: '📤', title: 'Export & Share', body: 'Download your data as CSV, or generate a formatted PDF to share with your doctor or coach.' },
  { icon: '🖥️', title: 'Every device', body: 'Same app on phone, tablet, laptop, and desktop. Installable, offline-ready, pushes reminders.' },
];

export default function FeatureGrid() {
  return (
    <section className="py-12 md:py-20">
      <div className="text-center mb-10 md:mb-14">
        <p className="section-heading mb-3">What you get</p>
        <h2
          className="text-3xl md:text-4xl font-black tracking-tight"
          style={{ color: 'var(--text-primary)' }}
        >
          Everything you need. Nothing you don’t.
        </h2>
      </div>

      <div className="grid grid-cols-2 md:grid-cols-5 gap-3 md:gap-4">
        {FEATURES.map((feature) => (
          <div key={feature.title} className="glass-card p-4 md:p-5 flex flex-col gap-2">
            <span className="text-2xl md:text-3xl" aria-hidden>{feature.icon}</span>
            <h3
              className="text-sm md:text-base font-bold leading-tight"
              style={{ color: 'var(--text-primary)' }}
            >
              {feature.title}
            </h3>
            <p
              className="text-xs md:text-sm leading-relaxed"
              style={{ color: 'var(--text-muted)' }}
            >
              {feature.body}
            </p>
          </div>
        ))}
      </div>
    </section>
  );
}
