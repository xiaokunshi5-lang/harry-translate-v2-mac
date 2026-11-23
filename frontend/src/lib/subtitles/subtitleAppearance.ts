export const IMMERSIVE_TEXT_SHADOW = [
  "0 2px 5px rgba(0, 0, 0, 0.98)",
  "0 0 2px rgba(0, 0, 0, 0.95)",
  "0 0 12px rgba(0, 0, 0, 0.72)",
].join(", ");

export function subtitleBackgroundColor(opacityPercent: number): string {
  const parsed = Number.isFinite(opacityPercent) ? opacityPercent : 100;
  const clamped = Math.min(100, Math.max(1, parsed));
  const alpha = Number((clamped / 100).toFixed(2));
  return `rgba(0, 0, 0, ${alpha})`;
}
