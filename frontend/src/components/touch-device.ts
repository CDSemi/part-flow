// Shared touch-device soft-keyboard handling (GUI_DESIGN §4.8): the
// ONE capability-detection module for every input that suppresses the
// native soft keyboard on touch-primary devices (the shared quantity
// input and the Scan Station main barcode field — scanner/physical-
// keyboard driven inputs, never ordinary text fields). It decides
// (a) whether the native soft keyboard should be suppressed
// (`inputMode="none"` on touch-primary devices) and (b) whether a soft
// keyboard is currently covering the viewport (the
// `window.visualViewport` fallback that collapses the on-screen
// NumPad). It never touches the shared cursor-aware quantity editing
// semantics in quantity-input.ts.

import { useEffect, useState } from 'react';

/**
 * True when the device is touch-primary: a coarse pointer without
 * hover plus real touch points. Capability detection only — never a
 * user-agent sniff and never a screen-width guess, so a desktop with a
 * touch screen (fine pointer + hover) keeps its physical-keyboard
 * behavior.
 */
export function isTouchPrimaryDevice(): boolean {
  if (typeof window === 'undefined' || typeof window.matchMedia !== 'function')
    return false;
  return (
    window.matchMedia('(pointer: coarse)').matches &&
    window.matchMedia('(hover: none)').matches &&
    (window.navigator.maxTouchPoints ?? 0) > 0
  );
}

/**
 * Minimum viewport-height reduction (px) treated as a soft keyboard.
 * Soft keyboards take well over this; browser chrome changes and
 * scroll-driven toolbar collapse stay well under it.
 */
export const SOFT_KEYBOARD_MIN_HEIGHT_LOSS = 140;

/**
 * True while the visual viewport is meaningfully shorter than when the
 * quantity dialog opened — the mobile-browser fallback for the cases
 * where `inputMode="none"` is not honored and the native soft keyboard
 * opens anyway. The baseline is the height at mount; the state clears
 * as soon as the viewport returns. Browsers without
 * `window.visualViewport` never collapse anything.
 */
export function useSoftKeyboardOpen(): boolean {
  const [open, setOpen] = useState(false);
  useEffect(() => {
    const viewport = window.visualViewport;
    if (!viewport) return;
    const baseline = viewport.height;
    const onResize = () => {
      setOpen(baseline - viewport.height >= SOFT_KEYBOARD_MIN_HEIGHT_LOSS);
    };
    onResize();
    viewport.addEventListener('resize', onResize);
    return () => viewport.removeEventListener('resize', onResize);
  }, []);
  return open;
}
