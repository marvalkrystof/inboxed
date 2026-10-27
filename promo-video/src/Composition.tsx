import {
  AbsoluteFill,
  Composition,
  Easing,
  Img,
  Sequence,
  Track,
  interpolate,
  staticFile,
  useCurrentFrame,
} from "remotion";
import type React from "react";
import { Audio } from "@remotion/media";

const C = {
  paper: "#f8f6f0",
  ink: "#34332f",
  muted: "#6c6b64",
  accent: "#a74d36",
  line: "#dcd8ce",
  night: "#30352f",
  screen: "#20221f",
  screen2: "#272925",
  light: "#e9e6dd",
  green: "#a9d7b7",
  yellow: "#eed18e",
  blue: "#9ab6d2",
};

const fps = 30;
const durations = { problem: 240, solution: 180, scan: 270, review: 300, classes: 240, cli: 270, privacy: 330, unsubscribe: 270, end: 210 };
const totalFrames = 2310;

const serif = 'Georgia, "Times New Roman", serif';
const sans = '-apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif';
const mono = 'ui-monospace, SFMono-Regular, Menlo, Consolas, monospace';

const enterStyle = (frame: number, delay = 0) => ({
  opacity: interpolate(frame, [delay, delay + 20], [0, 1], {
    extrapolateLeft: "clamp" as const,
    extrapolateRight: "clamp" as const,
    easing: Easing.bezier(0.16, 1, 0.3, 1),
  }),
  translate: `0px ${interpolate(frame, [delay, delay + 20], [24, 0], {
    extrapolateLeft: "clamp" as const,
    extrapolateRight: "clamp" as const,
    easing: Easing.bezier(0.16, 1, 0.3, 1),
  })}px`,
});

const Shell: React.FC<{
  children: React.ReactNode;
  dark?: boolean;
  duration: number;
}> = ({ children, dark = false, duration }) => {
  const frame = useCurrentFrame();
  const fade = interpolate(frame, [0, 16, duration - 16, duration], [0, 1, 1, 0], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });
  return (
    <AbsoluteFill
      style={{
        backgroundColor: dark ? C.night : C.paper,
        color: dark ? C.paper : C.ink,
        fontFamily: sans,
        opacity: fade,
        padding: "72px 104px",
      }}
    >
      {children}
    </AbsoluteFill>
  );
};

const Wordmark: React.FC<{ color?: string; size?: number }> = ({
  color = C.ink,
  size = 34,
}) => (
  <div
    style={{
      color,
      fontFamily: sans,
      fontSize: size,
      fontWeight: 750,
      letterSpacing: -2.5,
      lineHeight: 1,
    }}
  >
    inboxed<span style={{ color: C.accent }}>.</span>
  </div>
);

const Eyebrow: React.FC<{ children: React.ReactNode }> = ({ children }) => (
  <div
    style={{
      color: C.accent,
      fontFamily: mono,
      fontSize: 18,
      fontWeight: 650,
      letterSpacing: 2.2,
      textTransform: "uppercase",
    }}
  >
    {children}
  </div>
);

const WordmarkFooter: React.FC<{ dark?: boolean }> = ({ dark = false }) => (
  <div
    style={{
      position: "absolute",
      left: 104,
      right: 104,
      bottom: 42,
      display: "flex",
      justifyContent: "space-between",
      alignItems: "center",
      color: dark ? "#b9b5aa" : C.muted,
      fontFamily: mono,
      fontSize: 16,
      letterSpacing: 0.5,
    }}
  >
    <span>Less sorting. More room.</span>
    <span>inboxed.fyi</span>
  </div>
);

const Window: React.FC<{
  children: React.ReactNode;
  title: string;
  width?: number | string;
}> = ({ children, title, width = "100%" }) => (
  <div
    style={{
      width,
      backgroundColor: C.screen,
      color: C.light,
      border: `1px solid #52564e`,
      borderRadius: 12,
      boxShadow: "0 25px 70px rgba(30, 32, 28, .18)",
      overflow: "hidden",
      fontFamily: mono,
    }}
  >
    <div
      style={{
        height: 56,
        padding: "0 24px",
        backgroundColor: C.night,
        display: "flex",
        alignItems: "center",
        borderBottom: `1px solid #52564e`,
        color: "#d2d0c8",
        fontSize: 19,
      }}
    >
      <div style={{ display: "flex", gap: 8, marginRight: 22 }}>
        {["#f48184", "#eed18e", "#88c8a0"].map((color) => (
          <span
            key={color}
            style={{ width: 11, height: 11, borderRadius: "50%", backgroundColor: color }}
          />
        ))}
      </div>
      <span>{title}</span>
      <span style={{ marginLeft: "auto", color: C.green, letterSpacing: 2, fontSize: 15 }}>
        LOCAL
      </span>
    </div>
    {children}
  </div>
);

const ProblemScene: React.FC = () => {
  const frame = useCurrentFrame();
  return (
    <Shell duration={durations.problem}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
        <Wordmark size={40} />
        <Eyebrow>AN INBOX THAT NEVER STOPS</Eyebrow>
      </div>
      <div style={{ display: "grid", gridTemplateColumns: "1.1fr .9fr", gap: 90, height: 790, alignItems: "center" }}>
        <div style={enterStyle(frame, 5)}>
          <h1 style={{ fontFamily: serif, fontSize: 104, lineHeight: 1.04, letterSpacing: -4, fontWeight: 400, margin: 0 }}>
            Too much mail.<br />Too little<br /><em style={{ color: C.accent }}>headspace.</em>
          </h1>
          <p style={{ color: C.muted, fontSize: 30, lineHeight: 1.5, maxWidth: 730, marginTop: 36 }}>
            Newsletters. Receipts. Sign-in codes.<br />The important messages get buried.
          </p>
        </div>
        <div style={{ display: "flex", flexDirection: "column", gap: 18 }}>
          {[
            ["NEWSLETTER", "Another weekly digest", false],
            ["RECEIPT", "Your order is confirmed", false],
            ["SIGN-IN CODE", "Your verification code", false],
            ["FROM YOUR TEAM", "The message you came here for", true],
          ].map(([label, subject, important], index) => (
            <div key={String(label)} style={{ ...enterStyle(frame, 16 + index * 12), padding: "25px 28px", border: `1px solid ${important ? C.accent : C.line}`, backgroundColor: important ? "#efe4d9" : "#f1eee6", borderRadius: 5 }}>
              <div style={{ color: important ? C.accent : C.muted, fontFamily: mono, fontSize: 15, letterSpacing: 1.6, marginBottom: 10 }}>{label}</div>
              <div style={{ fontFamily: serif, fontSize: 30, color: important ? C.ink : C.muted }}>{subject}</div>
            </div>
          ))}
        </div>
      </div>
    </Shell>
  );
};

const Opening: React.FC = () => {
  const frame = useCurrentFrame();
  return (
    <Shell duration={durations.solution}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
        <Wordmark size={40} />
        <Eyebrow>MEET A CALMER ROUTINE</Eyebrow>
      </div>
      <div style={{ display: "grid", gridTemplateColumns: "1.05fr .95fr", alignItems: "center", height: 790, gap: 40 }}>
        <div style={enterStyle(frame, 8)}>
          <h1 style={{ fontFamily: serif, fontSize: 104, lineHeight: 1.03, letterSpacing: -5, fontWeight: 400, margin: 0, color: C.ink }}>
            Make room<br />for what<br /><em style={{ color: C.accent }}>matters.</em>
          </h1>
          <p style={{ maxWidth: 720, marginTop: 38, color: C.muted, fontSize: 30, lineHeight: 1.55 }}>
            Inboxed sorts the noise into clear suggestions.<br />You decide what stays, and what goes.
          </p>
        </div>
        <div style={{ ...enterStyle(frame, 18), textAlign: "center" }}>
          <Img src={staticFile("inbox-sketch.svg")} style={{ width: 680, height: 540 }} />
          <div style={{ marginTop: -12, fontFamily: serif, fontSize: 24, color: C.muted, fontStyle: "italic" }}>
            A calmer way to make room.
          </div>
        </div>
      </div>
    </Shell>
  );
};

// Capture content has no decorative chrome. Both interfaces use the same
// Window component; the full terminal stays at a fixed size for every shot.
const TuiShot: React.FC<{
  capture: string;
  caption: string;
}> = ({ capture, caption }) => (
  <AbsoluteFill>
    <Window title="Terminal · inboxed tui · personal">
      <div style={{ height: 680, backgroundColor: "#1e1e1e" }}>
        <Img src={staticFile(`tui/${capture}.png`)} style={{
          display: "block", width: "100%", height: "100%", objectFit: "contain",
        }} />
      </div>
    </Window>
    <div style={{ position: "absolute", bottom: 0, left: 0, right: 0, padding: "17px 0", fontFamily: mono, fontSize: 21, color: C.muted }}>
      <span style={{ color: C.accent }}>↳ </span>{caption}
    </div>
  </AbsoluteFill>
);

const TuiScene: React.FC<{
  duration: number;
  eyebrow: string;
  title: string;
  children: React.ReactNode;
}> = ({ duration, eyebrow, title, children }) => (
  <Shell duration={duration}>
    <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 35 }}>
      <div>
        <Eyebrow>{eyebrow}</Eyebrow>
        <h2 style={{ fontFamily: serif, fontSize: 60, lineHeight: 1.05, fontWeight: 400, margin: "12px 0 0" }}>{title}</h2>
      </div>
      <Wordmark size={36} />
    </div>
    <div style={{ position: "absolute", left: 104, right: 104, top: 210, bottom: 46 }}>
      {children}
    </div>
  </Shell>
);

const WorkspaceScene: React.FC = () => (
  <TuiScene duration={durations.scan} eyebrow="01 / SEE THE INBOX CLEARLY" title="From inbox to a clear review queue.">
    <Track name="Scan captures" durationInFrames={durations.scan}>
      <Sequence name="Scan options" durationInFrames={65}>
        <TuiShot capture="scan-dialog" caption="Choose the account, scan limit, and whether to skip processed mail." />
      </Sequence>
      <Sequence name="Classified inbox" from={65} durationInFrames={105}>
        <TuiShot capture="workspace-scanned" caption="Local classification. Read-only scanning. Nothing moved." />
      </Sequence>
      <Sequence name="Read the actual message" from={170} durationInFrames={100}>
        <TuiShot capture="email-detail" caption="Open the email and read it before deciding." />
      </Sequence>
    </Track>
  </TuiScene>
);

const ApplyScene: React.FC = () => (
  <TuiScene duration={durations.review} eyebrow="02 / REVIEW & APPLY" title="Your call, before anything moves.">
    <Track name="Review captures" durationInFrames={durations.review}>
      <Sequence name="Edit a suggestion" durationInFrames={60}>
        <TuiShot capture="email-edit" caption="Adjust the suggested class before approving any action." />
      </Sequence>
      <Sequence name="Select messages" from={60} durationInFrames={45}>
        <TuiShot capture="messages-selected" caption="Select messages individually or select the filtered list." />
      </Sequence>
      <Sequence name="Approve saved proposals" from={105} durationInFrames={50}>
        <TuiShot capture="messages-approved" caption="Approve the suggestions. Review decisions are saved locally." />
      </Sequence>
      <Sequence name="Real Apply preview" from={155} durationInFrames={90}>
        <TuiShot capture="apply-preview" caption="Check every destination. Missing folders are created during Apply." />
      </Sequence>
      <Sequence name="Completed actions" from={245} durationInFrames={55}>
        <TuiShot capture="apply-complete" caption="Apply completes. Each message keeps its execution status." />
      </Sequence>
    </Track>
  </TuiScene>
);

const ClassesScene: React.FC = () => (
  <TuiScene duration={durations.classes} eyebrow="04 / CLASSES & AUTOMATION" title="Your classes. Your rules.">
    <Track name="Class captures" durationInFrames={durations.classes}>
      <Sequence name="Class table and automation" durationInFrames={90}>
        <TuiShot capture="classes" caption="Checkboxes opt individual classes into confidence-gated automation." />
      </Sequence>
      <Sequence name="Edit class guidance" from={90} durationInFrames={40}>
        <TuiShot capture="class-edit" caption="Edit the label, classification guidance, cleanup action, and threshold." />
      </Sequence>
      <Sequence name="Class cleanup and confidence" from={130} durationInFrames={35}>
        <TuiShot capture="class-edit-cleanup" caption="Set a cleanup action and the confidence required for automation." />
      </Sequence>
      <Sequence name="Add a new class" from={165} durationInFrames={75}>
        <TuiShot capture="class-create" caption="Create categories that fit the way you work." />
      </Sequence>
    </Track>
  </TuiScene>
);

const CliScene: React.FC = () => {
  const frame = useCurrentFrame();
  const commands = [
    { command: "inboxed scan --account personal --limit 50", output: "Scan complete · 50 messages saved · mailbox unchanged", from: 24 },
    { command: "inboxed apply 8f31b21a --dry-run", output: "Preview · 12 approved · 0 mailbox changes", from: 97 },
    { command: "inboxed settings", output: "Ollama · local endpoint · credentials redacted", from: 172 },
  ];
  return (
    <Shell duration={durations.cli} dark>
      <div style={{ ...enterStyle(frame, 0), display: "flex", justifyContent: "space-between", alignItems: "end", marginBottom: 38 }}>
        <div>
          <Eyebrow>05 / THE CLI</Eyebrow>
          <h2 style={{ fontFamily: serif, fontSize: 78, fontWeight: 400, margin: "16px 0 0", lineHeight: 1.08 }}>
            Comfortable in a terminal.
          </h2>
        </div>
        <div style={{ color: "#c6c3b8", fontSize: 23, lineHeight: 1.45, maxWidth: 520 }}>
          Scan, review, preview, and apply.<br />Readable output or machine-friendly JSON.
        </div>
      </div>
      <div style={{ ...enterStyle(frame, 7) }}>
        <Window title="Terminal · zsh · inboxed">
          <div style={{ padding: "36px 46px 40px", minHeight: 520, backgroundColor: "#292c27" }}>
            {commands.map((item) => {
              const typedCount = Math.max(0, Math.min(item.command.length, Math.floor((frame - item.from) * 1.3)));
              const done = frame >= item.from + item.command.length / 1.3 + 8;
              return (
                <div key={item.command} style={{ minHeight: 130, marginBottom: 12, opacity: interpolate(frame, [item.from, item.from + 8], [0, 1], { extrapolateLeft: "clamp", extrapolateRight: "clamp" }) }}>
                  <div style={{ color: C.light, fontFamily: mono, fontSize: 22, lineHeight: 1.5 }}>
                    <span style={{ color: C.green }}>$ </span>{item.command.slice(0, typedCount)}
                    {!done && frame >= item.from && <span style={{ color: C.accent }}>▍</span>}
                  </div>
                  <div style={{ opacity: done ? 1 : 0, color: "#c2c5b9", fontFamily: mono, fontSize: 19, marginTop: 14, paddingLeft: 24 }}>
                    <span style={{ color: C.green }}>✓ </span>{item.output}
                  </div>
                </div>
              );
            })}
          </div>
        </Window>
      </div>
      <WordmarkFooter dark />
    </Shell>
  );
};

const PrivacyScene: React.FC = () => (
  <TuiScene duration={durations.privacy} eyebrow="06 / LOCAL HISTORY & PRIVACY" title="A history you can keep to yourself.">
    <Track name="History captures" durationInFrames={durations.privacy}>
      <Sequence name="Actual Runs table" durationInFrames={65}>
        <TuiShot capture="runs" caption="Revisit saved runs and inspect their status from the Runs tab." />
      </Sequence>
      <Sequence name="Saved activity logs" from={65} durationInFrames={65}>
        <TuiShot capture="run-activity" caption="Inspect the activity saved with each run." />
      </Sequence>
      <Sequence name="Password protection dialog" from={130} durationInFrames={75}>
        <TuiShot capture="encryption-dialog" caption="Protect existing and future run history with a password." />
      </Sequence>
      <Sequence name="Encrypted settings" from={205} durationInFrames={60}>
        <TuiShot capture="settings-encrypted" caption="Encryption enabled for existing and future run history." />
      </Sequence>
      <Sequence name="Local model selection" from={265} durationInFrames={65}>
        <TuiShot capture="settings-models" caption="Choose a local model with the required capabilities." />
      </Sequence>
    </Track>
  </TuiScene>
);

const UnsubscribeScene: React.FC = () => (
  <TuiScene duration={durations.unsubscribe} eyebrow="03 / LESS MAIL TOMORROW" title="Keep the good mail. Lose the noise.">
    <Track name="Unsubscribe captures" durationInFrames={durations.unsubscribe}>
      <Sequence name="Subscription list" durationInFrames={75}>
        <TuiShot capture="unsubscribe" caption="Find subscriptions in saved runs. See which methods are supported." />
      </Sequence>
      <Sequence name="Inspect subscription email" from={75} durationInFrames={75}>
        <TuiShot capture="unsubscribe-email" caption="View the email so you know which subscription you are reviewing." />
      </Sequence>
      <Sequence name="Choose supported subscription" from={150} durationInFrames={45}>
        <TuiShot capture="unsubscribe-selected" caption="Select supported one-click subscriptions." />
      </Sequence>
      <Sequence name="Unsubscribe confirmation" from={195} durationInFrames={75}>
        <TuiShot capture="unsubscribe-confirm" caption="Review and confirm before sending an unsubscribe request." />
      </Sequence>
    </Track>
  </TuiScene>
);

const EndCard: React.FC = () => {
  const frame = useCurrentFrame();
  return (
    <Shell duration={durations.end}>
      <div style={{ ...enterStyle(frame, 0), display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center", height: "100%", textAlign: "center" }}>
        <Wordmark size={60} />
        <h2 style={{ fontFamily: serif, fontSize: 88, fontWeight: 400, lineHeight: 1.06, margin: "38px 0 22px" }}>
          Make room<br /><em style={{ color: C.accent }}>for what matters.</em>
        </h2>
        <div style={{ marginTop: 18, padding: "17px 25px", border: `1px solid ${C.line}`, borderRadius: 7, color: C.ink, fontFamily: mono, fontSize: 23 }}>
          Get started at inboxed.fyi
        </div>

      </div>
    </Shell>
  );
};

export const InboxedFilm: React.FC = () => {
  return (
    <AbsoluteFill style={{ backgroundColor: C.paper }}>
      <Track name="English narration · Kokoro" durationInFrames={totalFrames}>
        <Audio name="The inbox problem" src={staticFile("voiceover/00-problem.wav")} durationInFrames={240} />
        <Audio name="The Inboxed approach" from={240} src={staticFile("voiceover/01-opening.wav")} durationInFrames={180} />
        <Audio name="Scan narration" from={420} src={staticFile("voiceover/02-scan.wav")} durationInFrames={270} />
        <Audio name="Review narration" from={690} src={staticFile("voiceover/03-review.wav")} durationInFrames={300} />
        <Audio name="Unsubscribe narration" from={990} src={staticFile("voiceover/07-unsubscribe.wav")} durationInFrames={270} />
        <Audio name="Classes narration" from={1260} src={staticFile("voiceover/04-classes.wav")} durationInFrames={240} />
        <Audio name="CLI narration" from={1500} src={staticFile("voiceover/05-cli.wav")} durationInFrames={270} />
        <Audio name="Privacy narration" from={1770} src={staticFile("voiceover/06-privacy.wav")} durationInFrames={330} />
        <Audio name="End card narration" from={2100} src={staticFile("voiceover/08-end.wav")} durationInFrames={210} />
      </Track>
      <Track name="Showcase scenes" durationInFrames={totalFrames}>
        <Sequence name="Too much mail" durationInFrames={240}>
          <ProblemScene />
        </Sequence>
        <Sequence name="Make room for what matters" from={240} durationInFrames={180}>
          <Opening />
        </Sequence>
        <Sequence name="Scan and workspace" from={420} durationInFrames={270}>
          <WorkspaceScene />
        </Sequence>
        <Sequence name="Review and apply" from={690} durationInFrames={300}>
          <ApplyScene />
        </Sequence>
        <Sequence name="Unsubscribe" from={990} durationInFrames={270}>
          <UnsubscribeScene />
        </Sequence>
        <Sequence name="Classes and automation" from={1260} durationInFrames={240}>
          <ClassesScene />
        </Sequence>
        <Sequence name="Command line" from={1500} durationInFrames={270}>
          <CliScene />
        </Sequence>
        <Sequence name="Privacy and local history" from={1770} durationInFrames={330}>
          <PrivacyScene />
        </Sequence>
        <Sequence name="Make room · get started" from={2100} durationInFrames={210}>
          <EndCard />
        </Sequence>
      </Track>
    </AbsoluteFill>
  );
};

export const MyComposition = () => (
  <Composition
    id="InboxedShowcase"
    component={InboxedFilm}
    durationInFrames={totalFrames}
    fps={fps}
    width={1920}
    height={1080}
  />
);
