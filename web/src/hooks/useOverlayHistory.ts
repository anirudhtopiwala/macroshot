import { useEffect, useRef } from 'react';

let popToIgnore = 0;

/**
 * Pushes a history entry when an overlay opens so that browser/OS back
 * gestures close the overlay instead of navigating away from the page.
 *
 * @param onClose  called when the overlay should close (via back gesture)
 * @param active   for open/close overlays like Modal, pass the `open` prop;
 *                 for mount/unmount overlays, omit (defaults to true)
 */
export default function useOverlayHistory(onClose: () => void, active = true) {
  const onCloseRef = useRef(onClose);
  onCloseRef.current = onClose;

  useEffect(() => {
    if (!active) return;

    let closedByPop = false;
    const urlAtPush = window.location.href;
    window.history.pushState({ overlay: true }, '');

    const handler = () => {
      if (popToIgnore > 0) { popToIgnore--; return; }
      closedByPop = true;
      onCloseRef.current();
    };
    window.addEventListener('popstate', handler);

    return () => {
      window.removeEventListener('popstate', handler);
      // Only pop our entry if no navigation happened (URL unchanged)
      // and we weren't already closed by a back gesture
      if (!closedByPop && window.location.href === urlAtPush) {
        popToIgnore++;
        window.history.back();
      }
    };
  }, [active]);
}
