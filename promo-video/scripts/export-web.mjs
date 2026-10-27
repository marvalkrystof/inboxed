import { existsSync, mkdirSync, readFileSync, writeFileSync, statSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { spawnSync } from "node:child_process";

const root = new URL("../", import.meta.url);
const site = new URL("../docs/", root);
const assets = new URL("assets/", site);
const video = new URL("video/", assets);
const master = new URL("out/inboxed-master.mp4", root);
if (!existsSync(master)) throw new Error("Render out/inboxed-master.mp4 first (npm run render:video).");
mkdirSync(video, { recursive: true });

function ffmpeg(args) {
  const result = spawnSync("ffmpeg", ["-hide_banner", "-v", "error", "-y", ...args], { encoding: "utf8" });
  if (result.status !== 0) throw new Error(result.stderr);
}

// Keep the full-resolution text sharp. Remux without another lossy encode;
// move the MP4 index before the media data for progressive web playback.
ffmpeg(["-i", fileURLToPath(master), "-map", "0:v:0", "-map", "0:a:0", "-c", "copy", "-movflags", "+faststart", fileURLToPath(new URL("inboxed-demo.mp4", video))]);
ffmpeg(["-ss", "19", "-i", fileURLToPath(master), "-frames:v", "1", "-q:v", "2", fileURLToPath(new URL("demo-poster.jpg", assets))]);

const narration = JSON.parse(readFileSync(new URL("scripts/narration.json", root), "utf8"));
const timing = JSON.parse(readFileSync(new URL("public/voiceover/timing.json", root), "utf8"));
const timestamp = seconds => {
  const total = Math.round(seconds * 1000);
  return `${String(Math.floor(total / 3600000)).padStart(2, "0")}:${String(Math.floor(total / 60000) % 60).padStart(2, "0")}:${String(Math.floor(total / 1000) % 60).padStart(2, "0")}.${String(total % 1000).padStart(3, "0")}`;
};
const cues = narration.scenes.map((scene, index) => {
  const take = timing.scenes.find(item => item.id === scene.id);
  if (!take) throw new Error(`Missing voiceover timing for ${scene.id}`);
  const start = scene.from / narration.fps + 0.35;
  const end = start + take.spokenSeconds;
  return `${index + 1}\n${timestamp(start)} --> ${timestamp(end)}\n${scene.text}\n`;
});
writeFileSync(new URL("inboxed-demo.en.vtt", video), "WEBVTT\n\n" + cues.join("\n"));
console.log(`Exported 1080p MP4 (${(statSync(new URL("inboxed-demo.mp4", video)).size / 1024 / 1024).toFixed(1)} MiB), poster, and English captions into docs/.`);
