/* The light behind the sign-in and setup screens. It is the mark's own marigold rather than the theme's
   accent: an accent can be near-black, and a light made of black is a smudge. */
const BACKGROUND = [
  'radial-gradient(44% 36% at 50% 30%, rgb(255 159 28 / 0.12), transparent 70%)',
  'radial-gradient(30% 26% at 58% 62%, rgb(255 196 92 / 0.06), transparent 70%)',
].join(', ');

export function SignalGlow() {
  return <div className="pointer-events-none absolute inset-0" style={{ background: BACKGROUND }} aria-hidden />;
}
