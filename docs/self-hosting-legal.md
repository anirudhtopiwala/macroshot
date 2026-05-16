# Self-hosting legal responsibilities

> **Read this before exposing your instance to anyone other than yourself.**

The bundled `Privacy Policy` and `Terms of Service` (in `web/src/pages/Privacy.tsx`
and `web/src/pages/Terms.tsx`) **apply only to the official MacroShot service
operated by Anirudh Topiwala at `macro.anirudhtopiwala.com`**. They are
included in the source as a starting point - they are **not** automatically
applicable to your deployment.

If you operate a public instance of MacroShot - even a small one for friends
or family - you become the **data controller** under GDPR / CCPA / PIPEDA /
similar laws. You are responsible for:

- **Replacing the bundled Privacy Policy and Terms of Service** with documents
  that name *you* (or your entity) as the data controller, not Anirudh
  Topiwala. Failure to do this may misrepresent who is responsible for users'
  data - a legal exposure for both you and the original developer.
- **Complying with applicable privacy law** in every jurisdiction your users
  live in. The bundled documents are written for the US-hosted official
  service and may not satisfy requirements in your jurisdiction.
- **Securing the data you store**, responding to data subject requests
  (access / deletion / portability) within statutory windows, and notifying
  affected users in the event of a breach.
- **Disclosing your use of third-party services** (Google Gemini, Stripe,
  Strava, Fitbit, FatSecret, Resend, Open Food Facts, Cloudflare, Sentry -
  whichever ones you actually configure) in your own privacy policy.
- **Age-gating users appropriately** for your jurisdiction. The bundled flow
  enforces 16+, which is appropriate for the EU but stricter than the US
  COPPA threshold of 13.
- **Posting disclaimers** that AI-generated nutrition estimates are not
  medical advice, are not for use in insulin dosing or other medication
  decisions, and are not for use during pregnancy / lactation / by anyone
  with a history of disordered eating. The bundled disclaimers in the AI
  coach and meal-detail screens are minimum coverage - your jurisdiction
  may require more.
- **Charging money?** Stripe has its own merchant terms, sales tax / VAT
  obligations apply, and consumer protection laws (e.g. California's
  Auto-Renewal Law) require specific disclosures at the point of sale.
  Don't accept payments without consulting a lawyer in your jurisdiction.

If you are not comfortable with any of the above, **operate your instance
strictly for personal use only** (no public signups, no shared link). The
license under which this code is distributed (FSL-1.1-Apache-2.0) is provided
"AS IS" with no warranty, and the original developer accepts **no liability**
for instances operated by third parties.

When in doubt, consult a lawyer in your jurisdiction. The cost of an hour of
legal review is much smaller than the cost of a privacy regulator complaint
or a user lawsuit.
