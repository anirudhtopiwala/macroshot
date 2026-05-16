import BackButton from '../components/BackButton';

const TIPS = [
  {
    icon: '📸',
    title: 'Three ways to log',
    description: 'Snap a photo, type a description, or scan a barcode. Tap the + button on any screen.',
  },
  {
    icon: '💬',
    title: 'AI corrections',
    description: 'After logging a meal, type a correction like "remove the bread" or "that was a large serving" to fix the analysis.',
  },
  {
    icon: '👆',
    title: 'Tap the calorie ring',
    description: 'Toggle between calories eaten and calories remaining by tapping the big ring on the Dashboard.',
  },
  {
    icon: '👈',
    title: 'Swipe on meals',
    description: 'Swipe left on any meal card to delete it or re-log the same meal again.',
  },
  {
    icon: '⚡',
    title: 'Quick Log shortcuts',
    description: 'Save frequent meals as Quick Logs. Tap "Save as Quick Log" after accepting a meal, then log it with one tap next time.',
  },
  {
    icon: '🔥',
    title: 'Streak & achievements',
    description: 'Tap the flame icon in the header to see your streak, badges, challenges, and streak shields.',
  },
  {
    icon: '🛡️',
    title: 'Streak shields',
    description: 'Hit your calorie target 3 days in a row to earn a shield. Shields automatically protect your streak when you miss a day.',
  },
  {
    icon: '📊',
    title: 'Trends & insights',
    description: 'The Trends tab shows your 7-day, 30-day, and 90-day averages for calories, protein, carbs, fat, and weight.',
  },
  {
    icon: '🤖',
    title: 'AI coaching chat',
    description: 'Ask the AI coach anything about nutrition - meal suggestions, macro tips, or help hitting your targets. The coach reads your meals, targets, profile, and the things you ask it to remember. Find it in the + menu.',
  },
  {
    icon: '🧠',
    title: 'Coach memory',
    description: 'Tell the coach things to remember across chats - allergies, dietary restrictions ("I\'m vegetarian"), or preferences ("I hate cilantro"). It will use them every future chat. Manage your saved memories in Settings → Coach Memory.',
  },
  {
    icon: '📅',
    title: 'Backdate meals',
    description: 'Forgot to log yesterday? Tap the date strip on the Dashboard to select a past day, then log meals for that date.',
  },
  {
    icon: '🔔',
    title: 'Meal reminders',
    description: 'Get a gentle nudge at meal times. Customize reminder hours in Settings → Reminders.',
  },
  {
    icon: '⚙️',
    title: 'Personalized targets',
    description: 'Set your calorie and macro targets in Settings → Goals. The AI can calculate them based on your profile and goals.',
  },
];

export default function Tips() {
  return (
    <div className="space-y-4">
      <div className="flex items-center gap-3">
        <BackButton fallbackPath="/settings" />
        <h1 className="text-lg font-bold">Tips & Shortcuts</h1>
      </div>

      <p className="text-sm" style={{ color: 'var(--text-secondary)' }}>
        Get the most out of MacroShot with these tips.
      </p>

      <div className="space-y-2">
        {TIPS.map((tip) => (
          <div key={tip.title} className="glass-card p-4">
            <div className="flex items-start gap-3">
              <span className="text-xl shrink-0">{tip.icon}</span>
              <div>
                <p className="text-sm font-bold" style={{ color: 'var(--text-primary)' }}>{tip.title}</p>
                <p className="text-xs mt-0.5" style={{ color: 'var(--text-secondary)' }}>{tip.description}</p>
              </div>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
