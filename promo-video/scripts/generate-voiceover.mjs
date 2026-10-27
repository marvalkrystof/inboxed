import { KokoroTTS } from "kokoro-js";
import { env } from "@huggingface/transformers";
import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { spawnSync } from "node:child_process";

const root = new URL("../", import.meta.url);
const output = new URL("public/voiceover/", root);
const cache = new URL(".cache/kokoro/", root);
const config = JSON.parse(readFileSync(new URL("narration.json", import.meta.url), "utf8"));
mkdirSync(output, { recursive: true });
mkdirSync(cache, { recursive: true });
env.cacheDir = fileURLToPath(cache);

console.log("Loading Kokoro locally on CPU (downloads model weights on first run)…");
const tts = await KokoroTTS.from_pretrained("onnx-community/Kokoro-82M-v1.0-ONNX", {
  dtype: "q8",
  device: "cpu",
});

const sampleRate = 24000;
const leadSeconds = 0.35;
const tailSeconds = 0.3;
const combined = new Float32Array(Math.round(config.totalFrames / config.fps * sampleRate));
const report = [];

function wav(samples) {
  const buffer = Buffer.alloc(44 + samples.length * 2);
  buffer.write("RIFF", 0);
  buffer.writeUInt32LE(buffer.length - 8, 4);
  buffer.write("WAVEfmt ", 8);
  buffer.writeUInt32LE(16, 16);
  buffer.writeUInt16LE(1, 20);
  buffer.writeUInt16LE(1, 22);
  buffer.writeUInt32LE(sampleRate, 24);
  buffer.writeUInt32LE(sampleRate * 2, 28);
  buffer.writeUInt16LE(2, 32);
  buffer.writeUInt16LE(16, 34);
  buffer.write("data", 36);
  buffer.writeUInt32LE(samples.length * 2, 40);
  for (let index = 0; index < samples.length; index++) {
    const value = Math.max(-1, Math.min(1, samples[index]));
    buffer.writeInt16LE(Math.round(value * 32767), 44 + index * 2);
  }
  return buffer;
}

for (const scene of config.scenes) {
  console.log(`Generating ${scene.id}: ${scene.text}`);
  const audio = await tts.generate(scene.text, { voice: config.voice, speed: config.speed });
  if (audio.sampling_rate !== sampleRate) throw new Error("Unexpected sample rate");
  let samples = audio.audio;
  const rawSeconds = samples.length / sampleRate;
  const available = scene.durationInFrames / config.fps - leadSeconds - tailSeconds;
  const rawPath = new URL(`${scene.id}-raw.wav`, cache);
  writeFileSync(rawPath, wav(samples));
  let tempo = 1;
  if (rawSeconds > available) {
    tempo = rawSeconds / available;
    if (tempo > 1.18) throw new Error(`${scene.id} needs shorter text (${rawSeconds.toFixed(2)} seconds for ${available.toFixed(2)} seconds available)`);
    const adjustedPath = new URL(`${scene.id}-timed.wav`, cache);
    const result = spawnSync("ffmpeg", ["-v", "error", "-y", "-i", fileURLToPath(rawPath), "-af", `atempo=${tempo}`, "-f", "f32le", "-ac", "1", "-ar", String(sampleRate), "pipe:1"], { maxBuffer: 16 * 1024 * 1024 });
    if (result.status !== 0) throw new Error(result.stderr.toString());
    const bytes = result.stdout;
    samples = new Float32Array(bytes.length / 4);
    for (let index = 0; index < samples.length; index++) samples[index] = bytes.readFloatLE(index * 4);
    // Retain the corrected raw take in the ignored local cache.
    writeFileSync(adjustedPath, wav(samples));
  }
  const take = new Float32Array(Math.round(scene.durationInFrames / config.fps * sampleRate));
  const lead = Math.round(leadSeconds * sampleRate);
  if (samples.length + lead > take.length) throw new Error(`${scene.id} overruns its scene`);
  take.set(samples, lead);
  writeFileSync(new URL(`${scene.id}.wav`, output), wav(take));
  combined.set(take, Math.round(scene.from / config.fps * sampleRate));
  report.push({ id: scene.id, from: scene.from, durationInFrames: scene.durationInFrames, rawSeconds, spokenSeconds: samples.length / sampleRate, tempo });
  console.log(`Saved ${scene.id}: ${(samples.length / sampleRate).toFixed(2)} seconds of speech in ${(take.length / sampleRate).toFixed(2)} seconds`);
}

writeFileSync(new URL("inboxed-english.wav", output), wav(combined));
const mp3 = spawnSync("ffmpeg", ["-v", "error", "-y", "-i", fileURLToPath(new URL("inboxed-english.wav", output)), "-codec:a", "libmp3lame", "-q:a", "2", fileURLToPath(new URL("inboxed-english.mp3", output))]);
if (mp3.status !== 0) throw new Error(mp3.stderr.toString());
writeFileSync(new URL("timing.json", output), JSON.stringify({ engine: "Kokoro-82M-v1.0", dtype: "q8", device: "cpu", voice: config.voice, speed: config.speed, sampleRate, seconds: combined.length / sampleRate, scenes: report }, null, 2) + "\n");
const script = "# Inboxed English voiceover\n\nVoice: Kokoro `af_heart` (American English). Calm delivery, one take per scene.\n\n" + config.scenes.map(scene => `## ${scene.title} (${scene.from / config.fps}–${(scene.from + scene.durationInFrames) / config.fps}s)\n\n${scene.text}\n`).join("\n");
writeFileSync(new URL("VOICEOVER.md", root), script);
console.log("Done. Saved scene takes, the combined narration, timing report, and VOICEOVER.md.");
