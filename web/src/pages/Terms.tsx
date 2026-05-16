import { Link } from 'react-router-dom';
import BackButton from '../components/BackButton';
import { useSubscription } from '../context/SubscriptionContext';
import { useSiteConfig, hostFromUrl } from '../api/siteConfig';

export default function Terms() {
  const { betaMode, appMode } = useSubscription();
  const isSelfHost = appMode === 'self';
  const { operator_name, contact_email, app_url, domain } = useSiteConfig();
  const operator = operator_name || 'the operator';
  const contact = contact_email || 'the operator';
  const host = hostFromUrl(app_url) || domain || 'this instance';

  return (
    <div className="space-y-6 pb-8">
      <BackButton label fallbackPath="/settings" />

      <div className="glass-card p-5 space-y-5">
        <h1 className="text-xl font-bold" style={{ color: 'var(--text-primary)' }}>
          Terms of Service
        </h1>
        <p className="text-xs" style={{ color: 'var(--text-muted)' }}>
          Last updated: May 16, 2026
        </p>

        {isSelfHost ? (
          <div className="text-xs p-3 rounded-lg" style={{ background: 'rgba(245,158,11,0.12)', color: 'var(--text-primary)', border: '1px solid rgba(245,158,11,0.45)' }}>
            <strong>⚠️ Operator notice.</strong> This is a self-hosted MacroShot instance. The terms below are a <em>starter template</em> covering universal user-facing disclaimers (AI accuracy, health, allergens). They do <strong>not</strong> cover operator-specific matters such as payment, refunds, dispute resolution, or governing law. Before exposing this instance to any user other than yourself, replace this page with terms that name you as the operator. See <code>docs/self-hosting-legal.md</code> in the source.
          </div>
        ) : (
          <>
            {betaMode && (
              <div className="text-xs p-3 rounded-lg" style={{ background: 'rgba(16,185,129,0.12)', color: 'var(--text-primary)', border: '1px solid rgba(16,185,129,0.35)' }}>
                <strong>Free beta:</strong> MacroShot is currently in free beta. All Pro features are available at no cost, and no paid subscription is offered. We will publish paid-tier terms and notify you by email at least 30 days before billing begins.
              </div>
            )}

            <div className="text-xs p-3 rounded-lg" style={{ background: 'var(--bg-elevated)', color: 'var(--text-secondary)', border: '1px solid var(--border-glass)' }}>
              <strong>Scope:</strong> These terms apply only to the official MacroShot service operated by {operator} at <strong>{host}</strong>. If you are using a self-hosted instance of MacroShot operated by a third party, these terms do not apply - the operator of that instance is solely responsible for establishing their own terms of service.
            </div>
          </>
        )}

        <Section title="1. Acceptance of Terms">
          By creating an account or using MacroShot, you agree to these Terms of Service and our{' '}
          <Link to="/privacy" className="underline" style={{ color: '#10b981' }}>Privacy Policy</Link>.
          If you do not agree, do not use the service. You must be at least 16 years old to use MacroShot.
        </Section>

        <Section title="2. Description of Service">
          MacroShot is a meal and nutrition tracking application that uses artificial intelligence (Google Gemini) to analyze food photos and text descriptions, estimate nutritional content, and provide general nutrition information. The service is available as a progressive web application (PWA).
        </Section>

        <Section title="3. Account Registration & Security">
          You must provide a valid email address or sign in with Google to create an account. You are responsible for maintaining the confidentiality of your account credentials and for all activity under your account. You agree to notify us promptly of any unauthorized use.
        </Section>

        <Section title="4. Subscription, Payments & Auto-Renewal">
          {isSelfHost ? (
            <p>This is a self-hosted instance. No paid subscription is offered by this deployment; all features are available at no cost from the operator. If the operator chooses to introduce billing later, they will publish their own subscription terms in place of this section.</p>
          ) : betaMode ? (
            <>
              <p>MacroShot is currently in free beta. No paid subscription is offered, no payment information is collected, and no charges will be made while beta is in effect.</p>
              <ul className="list-disc pl-5 mt-2 space-y-1">
                <li><strong>Beta access:</strong> Every account is auto-granted full access to all Pro features at no cost while the beta is active.</li>
                <li><strong>Tier limits:</strong> Daily limits apply to photo scans, text meal entries, AI chat sessions, and AI meal edits to protect service costs. The exact caps are surfaced in Settings → Subscription and may change without notice during beta.</li>
                <li><strong>Transition to paid:</strong> Before any paid tier launches, we will update these terms, publish the price and billing cadence, and notify you by email at least 30 days in advance. You will never be charged without explicitly opting in at that time.</li>
                <li><strong>Founding Member access:</strong> The developer may grant permanent complimentary Pro access at their discretion (e.g. to early supporters or beta testers). Complimentary access is provided as a courtesy and may be revoked at any time without notice.</li>
                <li><strong>Self-hosted mode:</strong> Self-hosted instances operate with all features unlocked at no cost, provided as-is without support or warranty.</li>
              </ul>
            </>
          ) : (
            <>
              <p>MacroShot offers a free tier and a paid Pro subscription.</p>
              <ul className="list-disc pl-5 mt-2 space-y-1">
                <li><strong>Price:</strong> Pro costs $4.99 per month, billed monthly via Stripe.</li>
                <li><strong>Auto-renewal:</strong> Your Pro subscription will automatically renew each month at $4.99 and your payment method will be charged on the renewal date, unless you cancel before the end of the current billing period. Stripe (our payment processor) will email you a receipt after each charge that includes your subscription details and cancellation instructions.</li>
                <li><strong>How to cancel:</strong> You can cancel at any time online from Settings &gt; Subscription &gt; Manage - the same interface you used to subscribe. No phone call or email is required. Cancellation takes effect at the end of the current billing period.</li>
                <li><strong>Price changes:</strong> If we increase the subscription price, we will notify you by email at least 30 days before the new price takes effect, along with instructions on how to cancel.</li>
                <li><strong>Free trial:</strong> A 7-day free trial is available once per account, no credit card required. At the end of the trial, you will not be charged unless you separately and voluntarily subscribe to Pro.</li>
                <li><strong>Tier limits:</strong> Daily limits apply to photo scans, text meal entries, AI chat sessions, and AI meal edits to protect service costs. The exact caps are surfaced in Settings → Subscription and may change with 30 days' notice.</li>
                <li><strong>Founding Member access:</strong> The developer may grant complimentary Pro access at their discretion (e.g. to early supporters or beta testers). Complimentary access is provided as a courtesy and may be revoked at any time without notice.</li>
                <li><strong>Consent records:</strong> We retain a record of your subscription consent for at least three years, or one year after your subscription ends, whichever is longer.</li>
                <li><strong>Self-hosted mode:</strong> Self-hosted instances operate with all features unlocked at no cost, provided as-is without support or warranty.</li>
              </ul>
              <p className="mt-2 font-semibold" style={{ color: 'var(--text-primary)' }}>
                BY SUBSCRIBING, YOU EXPRESSLY CONSENT TO AUTOMATIC MONTHLY RENEWAL AT $4.99/MONTH AND AUTHORIZE RECURRING CHARGES TO YOUR PAYMENT METHOD UNTIL YOU CANCEL.
              </p>
            </>
          )}
        </Section>

        {!isSelfHost && (
          <Section title="5. Cancellation and Refunds">
            <ul className="list-disc pl-5 space-y-1">
              <li>You may cancel your subscription at any time from Settings &gt; Subscription &gt; Manage.</li>
              <li>Upon cancellation, you retain Pro access until the end of your current billing period. No partial refunds are issued for unused time.</li>
              <li>If you cancel during a free trial, you will not be charged.</li>
              <li>Refund requests for the most recent charge may be honored at our discretion within 48 hours of the charge date. Email {contact} with your account email and reason.</li>
              <li>Chargebacks initiated through your bank may result in account suspension.</li>
            </ul>
          </Section>
        )}

        <Section title="6. Intellectual Property & Content License">
          <p>
            All intellectual property rights in MacroShot (including code, design, logos, and documentation) belong to the developer. You retain ownership of the content you submit (photos, text descriptions, personal data).
          </p>
          <p className="mt-2">
            By uploading content to MacroShot, you grant us a limited, non-exclusive, royalty-free license to use, process, transmit, and store that content solely for the purpose of providing and improving the service - including sending food photos to Google Gemini for AI analysis, storing meal logs, and displaying your data back to you.
          </p>
        </Section>

        <Section title="7. AI Disclaimer">
          <p>
            MacroShot uses artificial intelligence (Google Gemini) to analyze food photos and estimate nutritional content. <strong>These estimates are approximate and may contain significant errors.</strong> In typical use, AI-generated calorie and macronutrient estimates may differ from actual values by <strong>20% or more</strong>, and in some cases - particularly mixed dishes, restaurant meals, unfamiliar cuisines, or photos taken from unusual angles - the error may be substantially greater.
          </p>
          <ul className="list-disc pl-5 mt-2 space-y-1">
            <li>Actual nutritional values may vary based on portion sizes, preparation methods, specific brands, and ingredients not visible in photos.</li>
            <li>The AI may misidentify foods, especially from photos of mixed dishes or unfamiliar cuisines.</li>
            <li>Portion size estimation from photos is inherently imprecise.</li>
            <li><strong>The AI cannot reliably detect allergens.</strong> Never rely on AI estimates for allergen avoidance.</li>
            <li>The underlying AI model may change over time, which could affect the accuracy or format of estimates.</li>
            <li>The AI may generate fabricated, biased, or misleading information ("hallucinations") and should not be treated as an authoritative source.</li>
          </ul>
          <p className="mt-2">
            You should not rely solely on AI-generated nutritional estimates for medical dietary requirements, allergy management, or clinical nutrition plans. By using the service, you acknowledge that you understand the limitations of AI-generated nutrition estimates and accept the risk that they may be wrong.
          </p>
        </Section>

        <Section title="8. Health & Wellness Disclaimer">
          <p>
            This service is intended for general wellness and informational purposes only. It is <strong>not a substitute for professional medical advice, diagnosis, or treatment</strong>. The AI coaching feature provides general nutrition information, not personalized medical or dietary advice. Nothing in MacroShot constitutes the practice of medicine, dietetics, nursing, or any other licensed healthcare profession.
          </p>
          <p className="mt-2">
            Always consult a qualified healthcare provider or registered dietitian before making significant changes to your diet, especially if you have a medical condition, food allergies, or are pregnant or nursing.
          </p>
          <p className="mt-2 font-semibold" style={{ color: 'var(--text-primary)' }}>
            DO NOT USE FOR MEDICATION DECISIONS - INCLUDING INSULIN
          </p>
          <p className="mt-1">
            <strong>Diabetes / insulin / medication dosing:</strong> MacroShot is not designed for and must not be used to make medication decisions of any kind. This includes - but is not limited to - calculating insulin doses for Type 1 or Type 2 diabetes, adjusting medication based on carbohydrate counts, or making any treatment decision that depends on the accuracy of a nutrition estimate. AI-generated carbohydrate and calorie estimates are approximate and can be substantially wrong. Relying on them for insulin dosing or other medication decisions could cause serious harm, hospitalization, or death. If you have diabetes or any medical condition that requires precise nutrition tracking, use a clinical-grade tool prescribed by your healthcare provider - not this app.
          </p>
          <p className="mt-2">
            <strong>Pregnancy and nursing:</strong> Nutritional needs during pregnancy, nursing, and the postpartum period are individual and clinically important. Pregnant or nursing users should rely on guidance from their healthcare provider, not this app. MacroShot is not designed to support prenatal or perinatal nutrition planning.
          </p>
          <p className="mt-2">
            <strong>Eating disorders:</strong> Diet and calorie tracking may not be appropriate for everyone. If you have or are at risk for an eating disorder (including anorexia nervosa, bulimia nervosa, or binge eating disorder), this app may not be suitable for you. If using this app causes you to feel anxious, obsessive, or unhealthy about food or your body, we recommend you stop using it and consult a healthcare professional. If you need support, contact the NEDA helpline at 1-800-931-2237 or text "NEDA" to 741741.
          </p>
          <p className="mt-2">
            <strong>Children:</strong> MacroShot is not intended for users under the age of 16 and is not designed to support pediatric nutrition. Pediatric nutrition decisions should be made with a pediatrician.
          </p>
          <p className="mt-2">
            <strong>Allergies and food intolerances:</strong> The AI cannot reliably detect allergens, cross-contamination, or trace ingredients. If you have a food allergy or intolerance, do not rely on MacroShot to identify safe foods. Always verify ingredients yourself with packaging, restaurant staff, or a qualified professional.
          </p>
          <p className="mt-2">
            <strong>HIPAA:</strong> MacroShot is not a covered entity under the Health Insurance Portability and Accountability Act (HIPAA). We are not a healthcare provider, health plan, or healthcare clearinghouse. While we take your data security seriously, we do not provide HIPAA-compliant data storage or processing.
          </p>
        </Section>

        <Section title="9. User Data & Privacy">
          Your use of MacroShot is also governed by our{' '}
          <Link to="/privacy" className="underline" style={{ color: '#10b981' }}>Privacy Policy</Link>,
          which describes how we collect, use, and protect your data. By using the service, you consent to the practices described in the Privacy Policy.
        </Section>

        <Section title="10. Third-Party Services">
          <p>MacroShot relies on third-party services including Google Gemini (AI analysis), Stripe (payments), Strava, Fitbit, Oura, FatSecret, Resend (email), Open Food Facts, Cloudflare (CDN + analytics), and Sentry (error monitoring). For a full description of what data is shared with each service, see our <Link to="/privacy" className="underline" style={{ color: '#10b981' }}>Privacy Policy, Section 4</Link>. These services are subject to their own terms and privacy policies. We are not responsible for the availability, accuracy, or performance of third-party services. If a third-party service changes or becomes unavailable, affected features may be modified or discontinued.</p>
        </Section>

        <Section title="11. Account Deletion">
          <p>You may delete your account at any time from Settings &gt; Data &amp; Account &gt; Delete Account. Deletion is immediate and permanent. This removes your user record, meal logs, photos, chat sessions, preferences, fitness tokens, and subscription data from our systems.</p>
          <p className="mt-2"><strong>Data we cannot delete:</strong> Transaction records retained by Stripe (per their data retention policies), and data previously processed by Google Gemini (retained up to 55 days for safety monitoring per Google's API policies). See our Privacy Policy for full details.</p>
        </Section>

        <Section title="12. Acceptable Use">
          <p>You agree not to:</p>
          <ul className="list-disc pl-5 mt-1 space-y-1">
            <li>Upload illegal, harmful, or non-food content for analysis.</li>
            <li>Attempt to reverse-engineer, scrape, or abuse the application or its APIs.</li>
            <li>Circumvent subscription limits or create multiple accounts to abuse the free tier.</li>
            <li>Share your account credentials with others.</li>
            <li>Use the service to provide competing commercial nutrition analysis services.</li>
            <li>Interfere with the service's operation or other users' access.</li>
          </ul>
        </Section>

        <Section title="13. Termination & Suspension">
          We may suspend or terminate your account at our discretion if you violate these terms, with notice when practical. In cases of fraud, abuse, or illegal activity, immediate suspension may occur without notice. We also reserve the right to modify, suspend, or discontinue any feature of the service at any time. In the event of service discontinuation, we will provide at least 30 days' notice and maintain data export functionality during that period. You may export your meal log data at any time from Settings.
        </Section>

        <Section title="14. Disclaimer of Warranties">
          <p className="uppercase font-bold" style={{ color: 'var(--text-primary)' }}>
            THE SERVICE IS PROVIDED "AS IS" AND "AS AVAILABLE" WITHOUT WARRANTIES OF ANY KIND, EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO IMPLIED WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE, AND NON-INFRINGEMENT.
          </p>
          <p className="mt-2">
            We do not warrant that the service will be uninterrupted, error-free, or secure, or that AI-generated nutritional estimates will be accurate. You use the service at your own risk.
          </p>
        </Section>

        <Section title="15. Limitation of Liability">
          To the maximum extent permitted by law, MacroShot and its developer shall not be liable for any indirect, incidental, special, consequential, or punitive damages, or any loss of profits, data, use, or goodwill, or any adverse health outcomes, arising out of or related to your use of the service. Our total aggregate liability shall not exceed the amount you paid us in the 12 months preceding the claim.
        </Section>

        <Section title="16. Indemnification">
          You agree to indemnify, defend, and hold harmless the developer from any claims, liabilities, damages, losses, costs, and expenses (including reasonable attorney fees) arising from your use of the service, your violation of these terms, or your violation of any third-party rights.
        </Section>

        <Section title="17. Force Majeure">
          We are not liable for any failure or delay in providing the service due to events beyond our reasonable control, including but not limited to server outages, third-party API failures (Google Gemini, Stripe, etc.), natural disasters, or internet disruptions.
        </Section>

        <Section title="18. Dispute Resolution & Arbitration">
          {isSelfHost ? (
            <p>
              Dispute-resolution terms (arbitration provider, governing law, venue, opt-out, class-action waiver, small-claims exception) are set by the operator of this instance. The operator has not yet published them. For disputes, first contact <strong>{contact}</strong>; if a resolution cannot be reached, the operator's local jurisdiction applies.
            </p>
          ) : (
            <>
          <p>
            <strong>Informal resolution:</strong> Any dispute arising from these terms or your use of the service shall first be resolved through informal negotiation by contacting us at {contact}. If the dispute cannot be resolved informally within 30 days, either party may initiate binding arbitration as described below.
          </p>
          <p className="mt-2">
            <strong>Binding arbitration:</strong> Any dispute not resolved informally shall be resolved by binding individual arbitration administered by JAMS under its Streamlined Arbitration Rules, or by a mutually agreed arbitrator. The arbitration shall be conducted remotely (by videoconference or telephone) unless both parties agree otherwise. We will pay all JAMS filing, administration, and arbitrator fees beyond the initial consumer filing fee, consistent with JAMS Consumer Minimum Standards. The arbitrator's decision shall be final and binding and may be entered as a judgment in any court of competent jurisdiction. Any claim must be filed within one year after the cause of action arises, or the claim is permanently barred.
          </p>
          <p className="mt-2 font-semibold" style={{ color: 'var(--text-primary)' }}>
            CLASS ACTION WAIVER: YOU AND MACROSHOT AGREE THAT EACH MAY BRING CLAIMS AGAINST THE OTHER ONLY IN YOUR OR ITS INDIVIDUAL CAPACITY, AND NOT AS A PLAINTIFF OR CLASS MEMBER IN ANY PURPORTED CLASS, CONSOLIDATED, OR REPRESENTATIVE PROCEEDING.
          </p>
          <p className="mt-2">
            <strong>Small claims exception:</strong> Either party may bring an individual action in small claims court in lieu of arbitration.
          </p>
          <p className="mt-2">
            <strong>Opt-out right:</strong> You may opt out of this arbitration provision by sending written notice to {contact} within 30 days of first accepting these terms. If you opt out, disputes will be resolved in court as described below.
          </p>
          <p className="mt-2">
            <strong>Governing law:</strong> These terms are governed by the laws of the State of California, without regard to conflict of laws principles. Any litigation (if arbitration does not apply) shall be brought exclusively in the state or federal courts located in Santa Clara County, California, and you consent to the personal jurisdiction of such courts.
          </p>
            </>
          )}
        </Section>

        <Section title="19. Modifications to Service & Terms">
          We may update these terms from time to time. If we make material changes, we will notify you by email and/or through a prominent notice in the app at least 30 days before the changes take effect. Continued use of the service after changes constitutes acceptance of the updated terms. We also reserve the right to modify, add, or remove features of the service at any time.
        </Section>

        <Section title="20. General Provisions">
          <ul className="list-disc pl-5 space-y-1">
            <li><strong>Severability:</strong> If any provision of these terms is found unenforceable, the remaining provisions remain in full effect.</li>
            <li><strong>Entire agreement:</strong> These terms, together with the Privacy Policy, constitute the entire agreement between you and the developer regarding your use of the service.</li>
            <li><strong>Assignment:</strong> We may assign these terms (e.g., in connection with a merger or sale). You may not assign your account or rights under these terms.</li>
            <li><strong>No waiver:</strong> Our failure to enforce any provision does not constitute a waiver of the right to enforce it later.</li>
            <li><strong>Survival:</strong> Sections 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, and 20 survive termination of these terms or your account.</li>
            <li><strong>Electronic communications:</strong> By using MacroShot, you consent to receive communications from us electronically (email, push notifications, in-app notices). You agree that electronic communications satisfy any legal requirement that such communications be in writing.</li>
          </ul>
        </Section>

        <Section title="21. Contact">
          For questions about these terms: <strong>{contact}</strong>
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
