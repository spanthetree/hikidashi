import { fade, fly } from "svelte/transition";
import { cubicOut, quintOut } from "svelte/easing";
import { MediaQuery } from "svelte/reactivity";
import type { TransitionConfig } from "svelte/transition";

/* Every transition in the app goes through here, so prefers-reduced-motion is
   honoured in one place: movement becomes a short opacity change or nothing. */

const reduce = new MediaQuery("prefers-reduced-motion: reduce");
const hover = new MediaQuery("(hover: hover) and (pointer: fine)");
const narrow = new MediaQuery("max-width: 760px");

export const motion = {
  get reduced() { return reduce.current; },
  /** A real pointer that can hover, so hover-to-preview makes sense. */
  get canHover() { return hover.current; },
  /** Phone layout: the sheet slides up from the bottom. */
  get narrow() { return narrow.current; },
};

const quiet = (node: Element, duration = 120): TransitionConfig =>
  fade(node, { duration });

/* Shared-element motion between a card's picture and the sheet's media
   column: opening a reel grows the picture out of its card, closing shrinks
   it back in. Whichever element is arriving measures where its counterpart is
   on screen and starts from there; the one leaving just fades.

   Svelte's crossfade pairs the two lazily, and when the sheet closes the
   card's intro registers too late to find the outgoing media, so both sides
   fall back to a fade. Measuring the DOM has no such race: the card's link
   stays in the grid while its picture is away, and the sheet's media stays
   mounted until its outro ends. */

/** Where the counterpart of `node` is now, if it is on screen at all. */
export type Origin = () => Element | null | undefined;

function grow(node: Element, from: DOMRect): TransitionConfig {
  const to = node.getBoundingClientRect();
  if (!to.width || !to.height) return quiet(node);
  const dx = from.left - to.left, dy = from.top - to.top;
  const sx = from.width / to.width, sy = from.height / to.height;
  const distance = Math.hypot(dx, dy, from.width - to.width, from.height - to.height);
  return {
    duration: Math.min(460, Math.max(280, Math.sqrt(distance) * 11)),
    easing: quintOut,
    css: (t, u) => `
      transform-origin: 0 0;
      transform: translate(${u * dx}px, ${u * dy}px) scale(${t + u * sx}, ${t + u * sy});
      opacity: ${Math.min(1, t * 2.5)};`,
  };
}

/* At phone width the sheet slides up from the bottom instead, and a picture
   flying in from the grid at the same time would fight it. */
const plain = () => motion.reduced || motion.narrow;

export function send(node: Element) {
  return quiet(node, plain() ? 0 : 140);
}

export function receive(node: Element, { from }: { from: Origin }) {
  const origin = plain() ? null : from();
  return origin ? grow(node, origin.getBoundingClientRect()) : quiet(node, 180);
}

/* Cards arriving in a page fade up one after another. The delay is capped so
   the 60th card is not left waiting. */
export function cardIn(node: Element, { index }: { index: number }) {
  if (motion.reduced) return quiet(node);
  return fly(node, { y: 10, duration: 320, delay: Math.min(index, 18) * 22, easing: cubicOut });
}

export function backdrop(node: Element) {
  return fade(node, { duration: motion.reduced ? 120 : 220 });
}

/* Desktop: the panel rises a little and fades. Phone: it slides up from the
   bottom edge, the way a native sheet does. */
export function panel(node: Element) {
  if (motion.reduced) return quiet(node);
  return motion.narrow
    ? fly(node, { y: window.innerHeight, duration: 340, easing: quintOut, opacity: 1 })
    : fly(node, { y: 18, duration: 260, easing: cubicOut });
}

export const flipDuration = () => (motion.reduced ? 0 : 280);
