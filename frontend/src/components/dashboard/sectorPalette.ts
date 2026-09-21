// File: frontend/src/components/dashboard/sectorPalette.ts
// One fixed color per sector, keyed by sector NAME (color follows the entity,
// never its rank -- selecting a sector or the daily return ranking changing
// must never repaint anything).
//
// The 8 hues are the validated categorical palette from the dataviz skill
// (`validate_palette.js --mode light --surface #ffffff`: lightness band, chroma
// floor, adjacent CVD dE 9.1 and normal-vision dE 19.6 all PASS). There are 11
// GICS sectors, and a categorical palette must never be cycled or extended with
// generated hues past 8, so the last three sectors use *composite* encoding:
// they reuse the hue of a sector that is unlikely to be confused with them and
// add a dashed stroke. Line style, not just hue, therefore carries identity for
// those three -- the legend swatch draws the same dash so the mapping is visible.
//
// Aqua, yellow and magenta sit below 3:1 contrast on white (validator WARN). The
// obligation that creates is met by SectorTrendChart's always-visible legend,
// which prints every sector's name and value -- nothing is color-alone.
//
// The app has no dark theme, so only the light steps are defined.

export interface SectorStyle {
  color: string;
  /** SVG stroke-dasharray; undefined = solid. */
  dash?: string;
}

const DASH = '6 4';

const SECTOR_STYLES: Record<string, SectorStyle> = {
  // Solid: slots 1-8, in validated order.
  Technology: { color: '#2a78d6' }, // blue
  Energy: { color: '#eb6834' }, // orange
  Healthcare: { color: '#1baf7a' }, // aqua
  'Financial Services': { color: '#eda100' }, // yellow
  'Consumer Cyclical': { color: '#e87ba4' }, // magenta
  Industrials: { color: '#008300' }, // green
  'Communication Services': { color: '#4a3aa7' }, // violet
  'Basic Materials': { color: '#e34948' }, // red
  // Dashed: hue shared with a solid sector above, dash carries the difference.
  Utilities: { color: '#2a78d6', dash: DASH }, // blue, vs Technology
  'Consumer Defensive': { color: '#eb6834', dash: DASH }, // orange, vs Energy
  'Real Estate': { color: '#1baf7a', dash: DASH }, // aqua, vs Healthcare
  // Not a real sector -- a data-coverage bucket (symbols with no fundamentals
  // row yet). Deliberately neutral so it reads as "not a sector".
  Unknown: { color: '#898781', dash: '2 3' },
};

const FALLBACK: SectorStyle = { color: '#898781', dash: '2 3' };

export function getSectorStyle(sector: string): SectorStyle {
  return SECTOR_STYLES[sector] ?? FALLBACK;
}
