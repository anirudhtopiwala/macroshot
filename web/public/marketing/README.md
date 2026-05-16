# Marketing Screenshots

This directory holds the phone-screenshot PNGs shown on the public About / landing page (`/macro_app/about`).

**While this directory is empty, the About page falls back to styled gradient placeholders automatically** - the page is fully functional without real images. Drop PNGs in here later (no code change needed) and they'll light up.

## Expected filenames

The `About.tsx` page references these paths. Add any (or all) of them:

### Feature screenshots (used in hero + deep-dive rows)

| File | Captures | Suggested state |
|---|---|---|
| `hero-log.png` | LogMeal page, mid-capture | A plate of food in the camera preview with the "Analyze" button visible |
| `multi-image.png` | LogMeal page with multiple image thumbnails | 2–3 meal photo thumbnails stacked in the image carousel, text prompt filled in |
| `chat-coach.png` | Chat page mid-conversation | A question like "am I on track for protein today?" with the coach's tool-call response visible |
| `trends.png` | Trends page | Full calorie line chart + macro donut + 30-day period selected |
| `journal-gallery.png` | Journal page in gallery view | Photo grid of meals |
| `barcode.png` | Barcode scanner active | Camera overlay with a barcode framed in the viewfinder |
| `achievements.png` | Achievements page | Badge grid with a few unlocked tiers visible |

### PWA install instructions (used in the "Install it" section)

| File | Captures |
|---|---|
| `pwa-ios-1.png` | iPhone Safari with MacroShot loaded - Share button visible at the bottom |
| `pwa-ios-2.png` | iOS Share sheet open with "Add to Home Screen" highlighted |
| `pwa-ios-3.png` | iOS home screen with the MacroShot app icon installed |
| `pwa-android-1.png` | Android Chrome with MacroShot loaded - install banner OR ⋮ menu visible |
| `pwa-android-2.png` | Android Chrome menu open with "Install app" highlighted |
| `pwa-android-3.png` | Android home screen with the MacroShot app icon installed |

## Dimensions

- **Portrait phone screenshots**: ~1080 × 2340 px (typical iPhone/Android resolution). Will be displayed inside a phone frame roughly 280–340 px wide.
- Any aspect ratio close to 9:19.5 works - the `PhoneMockup` component crops overflow.

## How to capture

1. Run the dev server: `cd web && npm run dev`
2. Open Chrome DevTools → Device Mode → iPhone 14 Pro (or similar)
3. Take screenshots via DevTools (`Cmd+Shift+P` → "Capture node screenshot" or "Capture full size screenshot")
4. Save as PNG into this directory with the filename above

The page will automatically swap in any filename that lands here - no code change, no deploy needed for frontend-only, just a rebuild.
