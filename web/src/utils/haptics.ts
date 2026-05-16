import { haptic } from 'ios-haptics';

/** Subtle tap - nav buttons, toggles, filter pills */
export function hapticLight() {
  haptic();
  try { navigator.vibrate?.(10); } catch { /* not supported */ }
}

/** Success pattern - meal logged, relog, quick log */
export function hapticSuccess() {
  haptic.confirm();
  try { navigator.vibrate?.([10, 30, 10]); } catch { /* not supported */ }
}

/** Warning/error - delete, discard */
export function hapticWarning() {
  haptic.error();
  try { navigator.vibrate?.([20, 40, 20]); } catch { /* not supported */ }
}
