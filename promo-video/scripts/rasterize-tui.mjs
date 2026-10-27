import { Resvg } from "@resvg/resvg-js";
import { readFileSync, writeFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

const directory = new URL("../public/tui/", import.meta.url);
const manifest = JSON.parse(readFileSync(new URL("manifest.json", directory), "utf8"));

// Terminals draw block characters to the cell edges. Font rasterizers instead
// use glyph ascent/descent, leaving a gap on every row of a thick border.
// Preserve the exported terminal geometry by drawing these cells as vectors.
function terminalBlocks(svg) {
  const blocks = {
    "█": [0, 0, 1, 1], "▀": [0, 0, 1, 0.5], "▄": [0, 0.5, 1, 0.5],
    "▔": [0, 0, 1, 0.125], "▁": [0, 0.875, 1, 0.125],
    "▂": [0, 0.75, 1, 0.25], "▃": [0, 0.625, 1, 0.375],
    "▅": [0, 0.375, 1, 0.625], "▆": [0, 0.25, 1, 0.75], "▇": [0, 0.125, 1, 0.875],
    "▏": [0, 0, 0.125, 1], "▎": [0, 0, 0.25, 1], "▍": [0, 0, 0.375, 1],
    "▌": [0, 0, 0.5, 1], "▋": [0, 0, 0.625, 1], "▊": [0, 0, 0.75, 1], "▉": [0, 0, 0.875, 1],
    "▐": [0.5, 0, 0.5, 1], "▕": [0.875, 0, 0.125, 1],
  };
  return svg.replace(/<text([^>]*)>([^<]*)<\/text>/g, (original, attrs, text) => {
    const chars = [...text];
    if (!chars.length || !chars.every(char => blocks[char])) return original;
    const attr = name => attrs.match(new RegExp(`(?:^|\\s)${name}="([^"]+)"`))?.[1];
    const x = Number(attr("x"));
    const y = Number(attr("y")) - 18.5;
    const cell = Number(attr("textLength")) / chars.length;
    const rectangles = chars.map((char, index) => {
      const [left, top, width, height] = blocks[char];
      return `<rect x="${x + (index + left) * cell}" y="${y + top * 24.4}" width="${cell * width + 0.03}" height="${height * 24.4 + 0.03}" />`;
    }).join("");
    return `<g class="${attr("class")}" shape-rendering="crispEdges">${rectangles}</g>`;
  });
}

for (const capture of manifest.captures) {
  const svg = readFileSync(new URL(`${capture}.svg`, directory), "utf8");
  // Export only the terminal cells. Rich's decorative title bar must not
  // duplicate the shared Mac-style Window rendered by Remotion.
  const terminal = svg.match(/<clipPath id="[^"]+-clip-terminal">\s*<rect x="0" y="0" width="([\d.]+)" height="([\d.]+)"/);
  const offset = svg.match(/<g transform="translate\(([\d.]+), ([\d.]+)\)" clip-path="url\(#[^)]+-clip-terminal\)"/);
  if (!terminal || !offset) throw new Error(`Missing terminal bounds in ${capture}`);
  const content = terminalBlocks(svg).replace(/viewBox="[^"]+"/, `viewBox="${offset[1]} ${offset[2]} ${terminal[1]} ${terminal[2]}"`);
  const renderer = new Resvg(content, {
    font: { loadSystemFonts: true, defaultFontFamily: "Menlo" },
    fitTo: { mode: "width", value: 2050 },
  });
  writeFileSync(new URL(`${capture}.png`, directory), renderer.render().asPng());
}
console.log(`Rasterized ${manifest.captures.length} real TUI captures in ${fileURLToPath(directory)}`);
