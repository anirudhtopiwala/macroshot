import DeepDiveRow from './DeepDiveRow';

export default function DeepDives() {
  return (
    <>
      <DeepDiveRow
        eyebrow="Logging"
        title="Just point and shoot."
        body={
          <>
            Most trackers make you hunt through databases for the right food. MacroShot lets you skip
            all that. Snap a photo of your plate, type a sentence like{' '}
            <em>“half a burrito and a side salad,”</em> or scan a barcode. The AI figures out portions
            and cross-references three food databases to pick the most accurate source per item.
          </>
        }
        bullets={[
          'Multiple photos per meal - angles, multiple items, or before and after',
          'Mention "before and after" in the text prompt and the AI estimates what was actually eaten',
          'Multi-turn corrections if it gets something wrong',
          'Three reference databases: Open Food Facts, FatSecret, and USDA FoodData Central',
          'Unlimited barcode scans, always free',
        ]}
        image={{
          src: '/macro_app/marketing/multi-image.png',
          alt: 'Multi-image meal logging with before/after and instant analysis',
          soft: true,
        }}
        accent="emerald"
      />

      <DeepDiveRow
        eyebrow="AI Coach"
        title={
          <>
            A coach that actually
            <br />
            reads your data.
          </>
        }
        body={
          <>
            Ask <em>“what should I eat for dinner to hit my protein?”</em> and it already knows what
            you ate today. Say <em>“raise my calories for marathon week”</em> and your target updates.
            Tell it <em>“I’m allergic to peanuts”</em> and every future chat respects that. Under the
            hood, it reads your meals, targets, weight history, workouts (Strava/Fitbit/Oura), and
            saved memories via{' '}
            <strong style={{ color: 'var(--text-primary)' }}>MCP (Model Context Protocol)</strong>.
            23 structured tools, not a glorified search box.
          </>
        }
        bullets={[
          'Reads: meals, targets, weight, workouts, aliases, activity totals, coach memory',
          'Writes: log weight, update targets, log saved meals, update profile, remember/forget facts',
          'Long-term memory across chats - allergies, restrictions, preferences. Saved only after you confirm.',
          'Web-search grounding for brand and product questions',
          'Built on FastMCP, an open protocol with no vendor lock-in',
        ]}
        image={{
          src: '/macro_app/marketing/chat-coach.png',
          alt: 'AI Coach chat conversation reasoning over real meal data',
          soft: true,
        }}
        reversed
        accent="purple"
      />

      <DeepDiveRow
        eyebrow="Trends"
        title="See the shape of your week."
        body={
          <>
            A spreadsheet tells you what you ate. Trends tell you whether it’s working. Switch between
            7, 30, and 90-day views for calories, protein, carbs, fat, activity, and weight. All in
            the same place, all charted against your targets.
          </>
        }
        bullets={[
          'Daily streak, calorie adherence, and protein hit rate at a glance',
          'Macro and weight trend charts for any window',
          'Week-over-week comparison against your base + exercise-adjusted targets',
        ]}
        image={{
          src: '/macro_app/marketing/trends.png',
          alt: 'Trends page showing weekly calorie chart, streak, and adherence stats',
        }}
        accent="emerald"
      />

      <DeepDiveRow
        eyebrow="Streaks & Achievements"
        title="Tracking that wants you to come back."
        body={
          <>
            Log a meal every day and watch your streak grow. Hit your targets and unlock badges across
            eight categories. Bronze, Silver, Gold, Platinum, Diamond. It’s the part of nutrition
            tracking that most open-source alternatives forget, because spreadsheets alone don’t
            change behavior.
          </>
        }
        bullets={[
          '24 badges across 8 categories, Bronze to Diamond tiers',
          'Streak shields let you recover from a missed day',
          'Progress rings for every active goal',
        ]}
        image={{
          src: '/macro_app/marketing/achievements.png',
          alt: 'Achievements page showing badges across Bronze, Silver, Gold, Platinum, Diamond tiers',
        }}
        reversed
        accent="orange"
      />
    </>
  );
}
