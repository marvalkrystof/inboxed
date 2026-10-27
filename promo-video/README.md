# Inboxed showcase video

An editable 77-second Remotion composition introducing Inboxed’s TUI and CLI. It uses the website’s warm ivory, charcoal, and terracotta palette, with the same terracotta accent in the actual TUI, plus semantic green, yellow, and red accents.

## Scenes

| Time | Scene |
| --- | --- |
| 0:00–0:08 | Motivation: important messages get buried in inbox clutter |
| 0:08–0:14 | Pitch: clear suggestions, with the user deciding what stays |
| 0:14–0:23 | Scan and inspect messages |
| 0:23–0:33 | Review, approve, and preview Apply |
| 0:33–0:42 | Prevent future clutter with unsubscribe review |
| 0:42–0:50 | Classes and optional automation |
| 0:50–0:59 | CLI scan, dry-run, and settings |
| 0:59–1:10 | Local history, activity logs, encryption, and model selection |
| 1:10–1:17 | Benefit-led close and website call to action |

The TUI scenes use captures from the current `InboxedApp`, including its real widgets, styles, tables, dialogs, and action handlers. A disposable mailbox and deterministic classifier supply fictional email data. Remotion shows the captured states at a fixed size inside the exact same Mac-style `Window` component as the CLI. There is no TUI zooming or panning. These are scripted state captures, not continuous desktop recordings. The CLI section retains its animated command presentation.

## Refresh the real TUI captures

From the repository root, with the project's TUI dependencies installed:

```console
.venv/bin/python promo-video/scripts/capture_tui.py
cd promo-video
node scripts/rasterize-tui.mjs
```

The capture script creates a temporary configuration and database. It uses an in-memory demo provider, fixtures for model discovery, and the app's real scan, review, execution, encryption, and editing flows. It never opens your normal configuration or mailbox. It stops at the unsubscribe confirmation without sending requests. Model choices and classification results are fixtures, not measured model output.

SVG source captures and portable PNG images live in `public/tui/`; `manifest.json` lists all captured states. Intermediate SVGs are ignored; the PNGs and per-scene audio are committed so Studio works immediately after cloning. The PNGs keep fonts and color output consistent without network font loading. Every capture uses the same 168-column, 34-row terminal. Long forms scroll inside that terminal. Rasterization removes the capture export's decorative title bar, so only Remotion's shared window chrome appears. Terminal block glyphs are drawn as filled cell geometry rather than font glyphs, eliminating the row gaps in captured thick borders.

## Design references

TUI product demonstrations exist: [Lazygit](https://github.com/jesseduffield/lazygit#features) publishes feature-specific videos, while [Charm's VHS examples](https://github.com/charmbracelet/vhs/tree/main/examples) demonstrate Gum, Glow, and Bubble Tea using scripted key presses and pauses. [VHS](https://github.com/charmbracelet/vhs) supports fixed terminal dimensions, padding, a colorful window bar, and video output. [asciinema](https://docs.asciinema.org/manual/cli/quick-start/) records terminal sessions for replay.

For Inboxed, the adopted direction is a fixed terminal, one shared Mac-style frame, readable pauses, and real application state changes. Only the title cards and captions provide editorial animation. This project currently uses Textual state captures; it does not claim to be a VHS/asciinema recording.

## Preview and checks

Install dependencies with `npm ci`, then run `npm run dev` and choose `InboxedShowcase` in Remotion Studio. Open the URL printed by Studio; its port may vary if another local service occupies port 3000. This Remotion Studio version binds beyond localhost; use a network environment where that exposure is acceptable.

Run `npm run lint` to check ESLint and TypeScript. The composition is 1920×1080 at 30 fps. Master renders stay in the ignored out/ directory; the optimized website delivery is stored under docs/assets/video/.

## Website export

The website uses `docs/assets/video/inboxed-demo.mp4`: a 1920×1080 H.264/AAC MP4 with its index at the front for progressive playback. The 77-second export is approximately 3.8 MiB. It is served directly with the GitHub Pages site, alongside its English WebVTT captions and poster image. Playback starts when the visitor presses Play.

To regenerate after editing the composition:

```console
npm run render:video
npm run export:web
```

`render:video` writes the master into the ignored `out/` directory. `export:web` remuxes it without re-encoding, creates the poster, copies the real unsubscribe capture, writes captions from the narration timings. Include the generated files under `docs/assets/` when committing the website. The published site needs no Node server or external video host.

## English voiceover

The composition includes local Kokoro narration using the `af_heart` American English voice. [VOICEOVER.md](VOICEOVER.md) contains the complete script. `public/voiceover/inboxed-english.wav` and `.mp3` contain the full 77-second narration, with pauses aligned to the scenes. Individual WAV takes are editable in the Remotion narration track. The combined WAV and MP3 exports are ignored and can be regenerated with `npm run voiceover`.

Edit `scripts/narration.json`, then run `npm run voiceover` from this directory to regenerate. This uses Kokoro's official JavaScript library and an ONNX model on the CPU. The first run downloads model weights; the narration text is processed locally. Model weights and intermediate takes are cached in the ignored `.cache/kokoro/` directory. FFmpeg must be installed. The generator checks that each take fits its scene and only applies a small pitch-preserving tempo adjustment when needed. `public/voiceover/timing.json` records the actual durations and adjustments.

## Source files

- `src/Composition.tsx` contains the scenes and timeline.
- `src/Root.tsx` registers the `InboxedShowcase` composition.
- `public/` contains the brand logo and inbox sketch asset.
- `scripts/capture_tui.py` regenerates captures from the current TUI.
- `scripts/rasterize-tui.mjs` creates portable PNG assets from those captures.
