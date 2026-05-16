import BackButton from '../components/BackButton';
import { useSubscription } from '../context/SubscriptionContext';
import { useSiteConfig, hostFromUrl } from '../api/siteConfig';

export default function Privacy() {
  const { appMode } = useSubscription();
  const isSelfHost = appMode === 'self';
  const { operator_name, contact_email, app_url, domain } = useSiteConfig();
  const operator = operator_name || 'the operator';
  const contact = contact_email || 'the operator';
  // Prefer the app_url host (includes the subdomain the site actually
  // runs on) and only fall back to bare DOMAIN if app_url is unset.
  const host = hostFromUrl(app_url) || domain || 'this instance';

  return (
    <div className="space-y-6 pb-8">
      <BackButton label fallbackPath="/settings" />

      <div className="glass-card p-5 space-y-5">
        <h1 className="text-xl font-bold" style={{ color: 'var(--text-primary)' }}>
          Privacy Policy
        </h1>
        <p className="text-xs" style={{ color: 'var(--text-muted)' }}>
          Last updated: May 16, 2026
        </p>

        {isSelfHost ? (
          <div className="text-xs p-3 rounded-lg" style={{ background: 'rgba(245,158,11,0.12)', color: 'var(--text-primary)', border: '1px solid rgba(245,158,11,0.45)' }}>
            <strong>⚠️ Operator notice.</strong> This is a self-hosted MacroShot instance. The policy below is a <em>starter template</em> describing what the MacroShot software collects. The <strong>operator of this instance</strong> — not the upstream developer — is the data controller for your data, and is responsible for publishing storage location, retention periods, breach-notification procedure, and any region-specific rights (CCPA, GDPR). Before exposing this instance to any user other than yourself, replace this page. See <code>docs/self-hosting-legal.md</code> in the source.
          </div>
        ) : (
          <div className="text-xs p-3 rounded-lg" style={{ background: 'var(--bg-elevated)', color: 'var(--text-secondary)', border: '1px solid var(--border-glass)' }}>
            <strong>Scope:</strong> This privacy policy applies only to the official MacroShot service operated by {operator} at <strong>{host}</strong>. If you are using a self-hosted instance of MacroShot operated by a third party, that operator - not {operator} - is the data controller for your data, and their own privacy policy (if any) applies.
          </div>
        )}

        <Section title="1. Data Controller">
          <p>
            MacroShot is operated by {operator} (sole developer). For privacy questions or data requests, contact: <strong>{contact}</strong>
          </p>
        </Section>

        <Section title="2. Information We Collect">
          <p className="font-semibold mt-1">Account Information</p>
          <ul className="list-disc pl-5 mt-1 space-y-1">
            <li>Email address (required for account creation)</li>
            <li>Name (optional, provided by you or via Google sign-in)</li>
            <li>Google account ID (if you sign in with Google)</li>
            <li>Profile photo URL (from Google, if linked)</li>
          </ul>

          <p className="font-semibold mt-3">Health & Nutrition Data</p>
          <ul className="list-disc pl-5 mt-1 space-y-1">
            <li>Food photos you upload for analysis (EXIF metadata, including GPS coordinates, is stripped before storage)</li>
            <li>Meal descriptions and nutrition logs</li>
            <li>Weight, height, age, and sex (optional profile fields)</li>
            <li>Daily macro targets and dietary goals</li>
            <li>Weight log history</li>
            <li>Saved meals (quick-log shortcuts you create)</li>
            <li>Coach memory entries — durable facts you ask the AI coach to remember across chats (allergies, dietary restrictions, food preferences, free-text notes). The coach may suggest entries during conversations; you confirm before any save. You can view, edit, and delete entries at any time in Settings &gt; Coach Memory.</li>
          </ul>

          <p className="font-semibold mt-3">Fitness Data (if you connect Strava, Fitbit, or Oura)</p>
          <ul className="list-disc pl-5 mt-1 space-y-1">
            <li>Workout data: activity type, duration, calories burned, distance</li>
            <li>Activity summaries: steps, active minutes, resting heart rate</li>
            <li>OAuth tokens for accessing your fitness data</li>
          </ul>

          <p className="font-semibold mt-3">Technical & Usage Data</p>
          <ul className="list-disc pl-5 mt-1 space-y-1">
            <li>Push notification subscription tokens</li>
            <li>Usage counters (e.g., daily image analysis count)</li>
            <li>Chat session history with the AI coach</li>
            <li>Internal product telemetry - per-user timestamped event logs (e.g., "logged a meal," "opened chat") used for debugging and product analytics. Accessible only to the developer.</li>
            <li>Gamification data: badges earned, streak counts, and progress toward achievements</li>
            <li>Terms of Service acceptance timestamp and version</li>
          </ul>
        </Section>

        <Section title="3. How We Use Your Data">
          <table className="w-full text-xs mt-1" style={{ borderCollapse: 'collapse' }}>
            <thead>
              <tr style={{ borderBottom: '1px solid var(--border-glass)' }}>
                <th className="text-left py-1 pr-2 font-semibold">Purpose</th>
                <th className="text-left py-1 font-semibold">Legal Basis</th>
              </tr>
            </thead>
            <tbody>
              {[
                ['Meal analysis (AI processing of photos/text)', 'Your explicit consent (health data, GDPR Art. 9)'],
                ['Nutrition & weight tracking', 'Your explicit consent (health data, GDPR Art. 9)'],
                ['Fitness data (Strava/Fitbit/Oura)', 'Your explicit consent (health data, GDPR Art. 9)'],
                ['AI coaching chat', 'Your explicit consent'],
                ['Push notification reminders', 'Your consent (opt-in)'],
                ['Account authentication', 'Contractual necessity'],
                ['Payment processing via Stripe', 'Contractual necessity'],
                ['Error monitoring (Sentry)', 'Legitimate interest'],
                ['Analytics (Cloudflare Web Analytics)', 'Legitimate interest'],
                ['Improving nutrition estimation accuracy', 'Legitimate interest'],
              ].map(([purpose, basis], i) => (
                <tr key={i} style={{ borderBottom: '1px solid var(--border-glass)' }}>
                  <td className="py-1.5 pr-2">{purpose}</td>
                  <td className="py-1.5">{basis}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </Section>

        <Section title="4. Third-Party Services">
          <p>Your data is shared with the following third-party services as necessary to operate MacroShot:</p>
          <ul className="list-disc pl-5 mt-2 space-y-2">
            <li>
              <strong>Google Gemini (AI Analysis):</strong> Food photos, meal descriptions, chat messages, and coach memory text are transmitted to Google's servers for AI analysis. Coach memory entries (e.g. allergies, restrictions) are also sent to Gemini's text-embedding API to support future semantic recall. We use the paid Gemini API tier, under which Google does not use your prompts, photos, or responses for model training or product improvement. Google may retain API interactions for up to 55 days for abuse monitoring and safety purposes, after which they are deleted. When analyzing meals, Google Search may be used to improve accuracy for branded or regional foods - this means meal-related text may be processed through Google Search.
            </li>
            <li>
              <strong>Stripe (Payments):</strong> Email and payment information are shared for subscription billing. See{' '}
              <a href="https://stripe.com/privacy" target="_blank" rel="noopener noreferrer" className="underline" style={{ color: '#10b981' }}>Stripe's Privacy Policy</a>.
            </li>
            <li>
              <strong>FatSecret (Nutrition Reference):</strong> Food names from your meal analyses are sent to the FatSecret API to retrieve per-100g nutritional reference data. No personal information is shared with FatSecret.
            </li>
            <li>
              <strong>Strava / Fitbit / Oura (Fitness):</strong> If you connect these services, we exchange OAuth tokens and retrieve your activity data (workouts, steps, heart rate, active calories). We only access data you explicitly authorize. You can disconnect at any time. See{' '}
              <a href="https://ouraring.com/privacy-policy" target="_blank" rel="noopener noreferrer" className="underline" style={{ color: '#10b981' }}>Oura's Privacy Policy</a>.
            </li>
            <li>
              <strong>Resend (Email):</strong> Your email address is shared to deliver login PIN codes.
            </li>
            <li>
              <strong>Open Food Facts (Barcode Lookup):</strong> When you scan a barcode, the barcode value is sent to Open Food Facts to retrieve product information. No personal data is shared.
            </li>
            <li>
              <strong>Cloudflare (CDN & Analytics):</strong> All traffic is routed through Cloudflare's content delivery network for performance and security. Cloudflare may collect IP addresses and request metadata. Cloudflare Web Analytics also collects basic, anonymized, cookie-free page-view metrics (page views, country, referrer, browser) - no personal information, no tracking cookies, no behavioral profiling. See{' '}
              <a href="https://www.cloudflare.com/privacypolicy/" target="_blank" rel="noopener noreferrer" className="underline" style={{ color: '#10b981' }}>Cloudflare's Privacy Policy</a>.
            </li>
            <li>
              <strong>Sentry (Error Monitoring):</strong> We use Sentry to detect and fix errors. When an error occurs, Sentry may collect error details, stack traces, browser information, and a session replay. <strong>All on-screen text is masked and all images, photos, and videos are blocked from session replays</strong> - Sentry receives the page layout and error context, but never your actual meal photos, chat messages, or other content. We also disable Sentry's default PII collection so IP addresses, cookies, and request headers are not sent. See{' '}
              <a href="https://sentry.io/privacy/" target="_blank" rel="noopener noreferrer" className="underline" style={{ color: '#10b981' }}>Sentry's Privacy Policy</a>.
            </li>
          </ul>
        </Section>

        <Section title="5. Data Storage & Security">
          <p className="font-semibold mt-1">Where your data lives</p>
          {isSelfHost ? (
            <ul className="list-disc pl-5 mt-1 space-y-1">
              <li>Account, meal, weight, and nutrition data is stored in a SQLite database on infrastructure operated by <strong>{operator}</strong>. Server location, hosting provider, and backup destinations are determined by the operator. Contact <strong>{contact}</strong> for specifics.</li>
              <li>Food photos are stored on the operator's server. EXIF metadata (including GPS coordinates) is stripped before storage regardless of where the instance is hosted.</li>
              <li>Database backup procedures (frequency, destination, encryption) are the operator's responsibility.</li>
            </ul>
          ) : (
            <ul className="list-disc pl-5 mt-1 space-y-1">
              <li>Account, meal, weight, and nutrition data is stored in a SQLite database on a server hosted on Google Cloud Platform in the <strong>us-central1</strong> region (United States).</li>
              <li>Food photos are stored on the server filesystem and may also be backed up to a private Google Cloud Storage bucket in the <strong>us-central1</strong> region. The bucket is not publicly accessible - it can only be read by the MacroShot server process via an authenticated service account.</li>
              <li>Database backups are uploaded to the same private GCS bucket on a recurring schedule.</li>
            </ul>
          )}

          <p className="font-semibold mt-3">Security measures</p>
          <ul className="list-disc pl-5 mt-1 space-y-1">
            <li><strong>Encryption in transit:</strong> All connections to MacroShot and to our third-party services use HTTPS/TLS.</li>
            <li><strong>Encryption at rest:</strong> Server disks and the GCS bucket are encrypted at rest by Google Cloud Platform using AES-256.</li>
            <li><strong>Sensitive credentials:</strong> OAuth tokens for connected fitness services (Strava, Fitbit, Oura) are encrypted at the application level before being written to the database.</li>
            <li><strong>Authentication:</strong> JWTs are stored in HTTP-only, Secure, SameSite cookies that expire after 7 days. Login PINs are cryptographically hashed and expire after 10 minutes.</li>
            <li><strong>Access control:</strong> The database is not exposed to the public internet. Only the MacroShot server process running on the application VM can read or write to it. No third party has direct database access.</li>
            <li><strong>Rate limiting:</strong> Authentication and other sensitive endpoints are rate-limited to mitigate brute-force and abuse.</li>
            <li><strong>Photo metadata:</strong> EXIF metadata (including GPS coordinates) is stripped from uploaded photos before storage.</li>
            <li><strong>Cookies & cross-site protection:</strong> Session cookies use the <code>SameSite</code> attribute and a custom request header is required for state-changing API calls to mitigate CSRF.</li>
          </ul>
          <p className="mt-3">
            No system is perfectly secure. We make a good-faith effort to follow industry best practices, but we cannot guarantee absolute security of your data. By using MacroShot you accept this residual risk.
          </p>
          {isSelfHost ? (
            <p className="mt-2">
              <strong>International transfers:</strong> Server location is determined by the operator. If you access this instance from a different country, your data may be transferred to and processed in the operator's jurisdiction. Transfers to the third-party service providers listed in Section 4 are governed by each provider's own transfer-mechanism terms (SCCs / DPF / equivalent).
            </p>
          ) : (
            <p className="mt-2">
              <strong>International transfers:</strong> If you access MacroShot from outside the United States, your data will be transferred to and processed in the United States. Transfers to our third-party service providers listed in Section 4 are covered by their participation in the EU-U.S. Data Privacy Framework (DPF), Standard Contractual Clauses (SCCs), and/or equivalent transfer mechanisms as required by applicable law. Our server infrastructure on Google Cloud Platform is covered by Google Cloud's data processing terms, which include SCCs for international transfers.
            </p>
          )}
        </Section>

        <Section title="6. Data Retention">
          <ul className="list-disc pl-5 space-y-1">
            <li><strong>Meal logs & photos:</strong> Retained until you delete them individually or delete your account.</li>
            <li><strong>Chat sessions:</strong> Retained until you delete them or your account.</li>
            <li><strong>Coach memory entries:</strong> Retained until you delete them individually (Settings &gt; Coach Memory) or delete your account.</li>
            <li><strong>Login PINs:</strong> Expire after 10 minutes, periodically cleaned up.</li>
            <li><strong>Usage counters:</strong> Reset daily.</li>
            <li><strong>OAuth tokens (Strava/Fitbit/Oura):</strong> Deleted when you disconnect the service or delete your account.</li>
            <li><strong>Push notification tokens:</strong> Deleted when you unsubscribe or delete your account.</li>
            <li><strong>Stripe records:</strong> Transaction history is retained by Stripe per their policies, independent of your MacroShot account.</li>
            <li><strong>Data sent to Google Gemini:</strong> Retained by Google for up to 55 days for safety monitoring, then deleted per Google's API data policies.</li>
          </ul>
        </Section>

        <Section title="7. Your Rights">
          <p>You have the right to:</p>
          <ul className="list-disc pl-5 mt-1 space-y-1">
            <li><strong>Access:</strong> View all your data through the app's dashboard, journal, and settings.</li>
            <li><strong>Export:</strong> Download your meal and weight data as CSV from Settings (free for all users).</li>
            <li><strong>Rectification:</strong> Edit or correct your meal data, profile, and targets through the app.</li>
            <li><strong>Delete:</strong> Delete individual meals from the journal, or delete your entire account and all associated data from Settings &gt; Data & Account &gt; Delete Account. Account deletion is self-service and immediate.</li>
            <li><strong>Disconnect:</strong> Revoke Strava, Fitbit, or Oura access at any time.</li>
            <li><strong>Withdraw consent:</strong> You may withdraw consent to data processing by deleting your account. For specific processing activities, contact us.</li>
            <li><strong>Restriction:</strong> Request that we temporarily stop processing your data while a complaint or dispute is being resolved (GDPR Article 18).</li>
            <li><strong>Object:</strong> Object to processing of your data that is based on legitimate interest (e.g., analytics, nutrition estimation accuracy improvements). Contact us and we will stop unless we have compelling legitimate grounds (GDPR Article 21).</li>
            <li><strong>Data portability:</strong> Request a copy of your data in a structured format by contacting us.</li>
          </ul>
          <p className="mt-2">
            We will respond to data requests within 30 days. For EU residents, you also have the right to lodge a complaint with your local data protection authority.
          </p>
        </Section>

        <Section title="8. Account Deletion Scope">
          <p>When you delete your account, the following data is permanently removed from our systems:</p>
          <ul className="list-disc pl-5 mt-1 space-y-1">
            <li>User record, profile, and preferences</li>
            <li>All meal logs and nutrition data</li>
            <li>Food photos stored on the server</li>
            <li>Chat sessions and conversation history</li>
            <li>Coach memory entries (allergies, restrictions, preferences, notes)</li>
            <li>Weight log history</li>
            <li>Saved meals (aliases)</li>
            <li>Push notification subscriptions</li>
            <li>Strava, Fitbit, and Oura OAuth tokens</li>
            <li>Subscription and usage records</li>
          </ul>
          <p className="mt-2">
            <strong>Data we cannot delete:</strong> Transaction records retained by Stripe, and data previously processed by Google Gemini (retained up to 55 days per Google's policies).
          </p>
        </Section>

        {!isSelfHost && (
        <Section title="9. California Privacy Rights (CCPA/CPRA)">
          <p>
            We do not sell, rent, or share your personal information with third parties for their marketing purposes. As defined under the California Consumer Privacy Act (CCPA/CPRA), we do not "sell" or "share" your personal information for cross-context behavioral advertising. We do not use tracking cookies or advertising pixels. We use privacy-focused analytics (Umami) that does not track individuals or use cookies - see Section 4 for details. Data is only shared with the third-party services listed in Section 4, solely for the purpose of operating the service.
          </p>

          <p className="font-semibold mt-3">Categories of Personal Information Collected</p>
          <div className="overflow-x-auto mt-2">
            <table className="w-full text-xs" style={{ borderCollapse: 'collapse' }}>
              <thead>
                <tr style={{ borderBottom: '1px solid var(--border-glass)' }}>
                  <th className="text-left py-1 pr-2 font-semibold">CCPA Category</th>
                  <th className="text-left py-1 pr-2 font-semibold">What We Collect</th>
                  <th className="text-left py-1 pr-2 font-semibold">Source</th>
                  <th className="text-left py-1 pr-2 font-semibold">Purpose</th>
                  <th className="text-left py-1 font-semibold">Sold?</th>
                </tr>
              </thead>
              <tbody>
                {[
                  ['A. Identifiers', 'Email, name, Google account ID', 'You, Google OAuth', 'Account & authentication', 'No'],
                  ['B. Protected classifications', 'Age, sex', 'You (optional)', 'AI target-setting', 'No'],
                  ['C. Commercial information', 'Subscription plan, Stripe customer ID', 'You (via Stripe)', 'Payment processing', 'No'],
                  ['D. Biometric information', 'None collected', '-', '-', 'No'],
                  ['E. Internet/electronic activity', 'Usage counters, analytics events, error logs, chat history', 'Automatic', 'Service operation, error detection', 'No'],
                  ['F. Geolocation', 'Timezone', 'You (settings)', 'Meal timestamps, reminders', 'No'],
                  ['G. Sensory data', 'Food photos', 'You (uploads)', 'AI meal analysis', 'No'],
                  ['H. Professional/education info', 'None collected', '-', '-', 'No'],
                  ['I. Inferences', 'AI nutrition estimates, meal type classification', 'Generated by AI', 'Nutrition tracking', 'No'],
                  ['J. Sensitive personal info', 'Weight, height, nutrition logs, heart rate, dietary goals', 'You, Strava, Fitbit, Oura', 'Nutrition & fitness tracking', 'No'],
                ].map(([cat, data, source, purpose, sold], i) => (
                  <tr key={i} style={{ borderBottom: '1px solid var(--border-glass)' }}>
                    <td className="py-1.5 pr-2 font-medium">{cat}</td>
                    <td className="py-1.5 pr-2">{data}</td>
                    <td className="py-1.5 pr-2">{source}</td>
                    <td className="py-1.5 pr-2">{purpose}</td>
                    <td className="py-1.5">{sold}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          <p className="font-semibold mt-3">Your California Privacy Rights</p>
          <ul className="list-disc pl-5 mt-1 space-y-1">
            <li><strong>Right to know:</strong> You may request what personal information we collect, use, and disclose about you.</li>
            <li><strong>Right to delete:</strong> You may request deletion of your personal information (self-service via Settings, or by contacting us).</li>
            <li><strong>Right to correct:</strong> You may request correction of inaccurate personal information (self-service via the app, or by contacting us).</li>
            <li><strong>Right to opt-out of sale:</strong> We do not sell your personal information, so no opt-out is necessary.</li>
            <li><strong>Right to limit use of sensitive personal info:</strong> You may request that we limit our use of sensitive personal information (health, nutrition, fitness data) to what is necessary to provide the service.</li>
            <li><strong>Right to non-discrimination:</strong> We will not discriminate against you for exercising any of your privacy rights.</li>
          </ul>
        </Section>
        )}

        <Section title="10. Children's Privacy">
          MacroShot is not intended for children under the age of 16. We do not knowingly collect personal information from children under 16. If you believe a child under 16 has provided us with personal information, please contact us so we can delete it.
        </Section>

        <Section title="11. Cookies & Local Storage">
          <ul className="list-disc pl-5 space-y-1">
            <li><strong>Session cookie:</strong> A single HTTP-only cookie (<code>macro_session</code>) stores your authentication JWT. It expires after 7 days.</li>
            <li><strong>Local storage:</strong> We cache user preferences, theme settings, subscription status, onboarding state, and other app settings in your browser's local storage for faster page loads. This data stays on your device.</li>
            <li><strong>IndexedDB:</strong> If you are offline, meal logs may be temporarily stored in your browser's IndexedDB and automatically synced when you reconnect.</li>
            <li>We use Cloudflare Web Analytics for privacy-focused, cookieless page-view metrics (see Section 4). We do not use tracking cookies or advertising pixels.</li>
          </ul>
        </Section>

        <Section title="12. Data Breach Notification">
          In the event of a data breach that affects your personal information, we will notify affected users via email without undue delay and, where feasible, no later than 72 hours after becoming aware of the breach, as required by applicable law (including GDPR Article 33). For breaches affecting 500 or more individuals, we will notify the Federal Trade Commission contemporaneously with individual notice. We will notify relevant state, federal, and supervisory authorities as required by applicable law.
        </Section>

        <Section title="13. Changes to This Policy">
          We may update this Privacy Policy from time to time. If we make material changes, we will notify you through the app. The "Last updated" date at the top indicates when the policy was last revised.
        </Section>

        <Section title="14. Contact & Data Requests">
          For privacy questions, data access requests, or to exercise any of your rights: <strong>{contact}</strong>
        </Section>
      </div>
    </div>
  );
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="space-y-2">
      <h2 className="text-sm font-semibold" style={{ color: 'var(--text-primary)' }}>{title}</h2>
      <div className="text-xs leading-relaxed" style={{ color: 'var(--text-secondary)' }}>
        {children}
      </div>
    </div>
  );
}
