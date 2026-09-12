import {
  BarChart,
  Callout,
  Card,
  CardBody,
  CardHeader,
  Grid,
  H1,
  H2,
  H3,
  LineChart,
  Row,
  Stack,
  Stat,
  Table,
  Text,
  useHostTheme,
} from "cursor/canvas";

const D = {
  session: "2026-09-12 · 62 synth runs · nano + turbo · Vulkan synth / CPU eval",
  sweetSpot:
    "Likely sweet spot for counting: repeat-penalty between 1.05 and 1.15 (not yet measured). At 1.0: longest output (+108 tokens) but silence-token collapse (171×4299). At 1.2 header: misses numbers 20–21. At 1.5: shortest, cleanest silence (5×4299), all numbers OK.",
  knobBars: [
    { id: "header 1.2", pred: 582, sil: 34, utmos: 4.33, dur: 23.4 },
    { id: "repeat 1.0", pred: 690, sil: 171, utmos: 4.51, dur: 27.7 },
    { id: "repeat 1.5", pred: 470, sil: 5, utmos: 4.26, dur: 18.9 },
    { id: "temp 0.6", pred: 489, sil: 26, utmos: 4.25, dur: 19.6 },
    { id: "cfm 4", pred: 582, sil: 34, utmos: 4.5, dur: 23.4 },
    { id: "top_p 1.0", pred: 500, sil: 17, utmos: 4.28, dur: 20.1 },
  ],
  bpeNano: [
    { l: "hello", b: 2, p: 15, d: 0.7 },
    { l: "count", b: 63, p: 582, d: 23.4 },
    { l: "25%", b: 135, p: 790, d: 31.7 },
    { l: "50%", b: 298, p: 145, d: 5.9 },
    { l: "75%", b: 436, p: 15, d: 0.7 },
    { l: "100%", b: 556, p: 15, d: 0.7 },
  ],
  bpeTurbo: [
    { l: "hello", b: 2, p: 19, d: 0.8 },
    { l: "count", b: 63, p: 474, d: 19.0 },
    { l: "25%", b: 135, p: 681, d: 27.3 },
    { l: "50%", b: 298, p: 298, d: 12.0 },
    { l: "75%", b: 436, p: 145, d: 5.9 },
    { l: "100%", b: 556, p: 15, d: 0.7 },
  ],
  silTimeline: {
    cats: ["0", "50", "100", "150", "200", "250", "300", "350", "400", "450", "500", "550", "580"],
    header: [0, 0, 0, 0.02, 0, 0.63, 0.13, 0.49, 0.87, 0.82, 0, 0.79, 0],
    repeat1: [0, 0, 0.11, 0.87, 0.91, 0.02, 0.95, 0.89, 0.9, 0.96, 0.86, 0, 0.52],
  },
  quality: [
    { id: "header", utmos: 4.33, emo: 0.247, hnr: 16.4, pred: 582 },
    { id: "repeat 1.0", utmos: 4.51, emo: 0.0, hnr: 16.1, pred: 690 },
    { id: "repeat 1.5", utmos: 4.26, emo: 0.136, hnr: 15.3, pred: 470 },
    { id: "cfm 4", utmos: 4.5, emo: 0.24, hnr: 16.8, pred: 582 },
  ],
  toolsUsed: [
    "faster-whisper medium.en (word ASR, counting coverage)",
    "Silero VAD pause gaps >=120ms (breath / phrase boundary proxy)",
    "parselmouth F0, jitter, shimmer, HNR (voice quality)",
    "UTMOS + audiobox PQ/CE (naturalness)",
    "emotion2vec cosine vs reference.wav (prosody/emotion drift)",
    "MFCC speaker cosine vs reference (clone fidelity)",
    "T3 dump: BPE, predicted_count, eos, prompt_slots",
    "Sampler CSV: per-step chosen4299 probability (time series)",
  ],
  toolsSkipped: [
    "STOI (reference is clone prompt, not parallel clean speech)",
    "Dedicated breath detector (VAD pauses only)",
    "Full jiwer WER on all 62 runs (count coverage on probe text)",
    "min-p / cfg-weight (not wired in nano/turbo C++)",
  ],
  phase3: [
    "Bisect repeat-penalty: 1.0, 1.05, 1.1, 1.15, 1.2 on count_10_27",
    "Edge knobs: top-k 0/50/1000, repeat-last-n 64/256/1000, cfm-steps 2/4/8/10",
    "seed sweep for stability",
    "n-predict only validates cap (does not fix eos early stop)",
  ],
};

export default function TtsKnowledgeReport() {
  const theme = useHostTheme();

  return (
    <Stack gap={24} style={{ padding: 24, maxWidth: 1100, color: theme.text.primary }}>
      <Stack gap={8}>
        <H1>TTS Knob Sweep — Human and AI Knowledge Report</H1>
        <Text tone="secondary">{D.session}</Text>
        <Text>
          Pins: nano 41e1f684 · turbo 6af3e8b2 · Trident v3 fc0d3e1f. Regenerate: python eval_out/knob_sweep.py
        </Text>
      </Stack>

      <Callout tone="info" title="Sweet spot hypothesis (Phase 3 target)">
        {D.sweetSpot}
      </Callout>

      <Grid columns={2} gap={16}>
        <Stat label="Nano max good · 135 BPE · ~32s prose" value="790 tok" tone="success" />
        <Stat label="Nano cliff · 436+ BPE · instant stop" value="15 tok" tone="danger" />
        <Stat label="Turbo max trap text · 436 BPE degraded" value="145 tok" tone="warning" />
        <Stat label="Full benchmark cliff both models" value="556 BPE" tone="danger" />
      </Grid>

      <Card>
        <CardHeader>How synthesis works (one prompt)</CardHeader>
        <CardBody>
          <Stack gap={6}>
            <Text>1. Text to GPT-2 BPE tokens (dump text line).</Text>
            <Text>2. prompt_slots = 1 + cond_prompt_len(375) + bpe + 1. Hard max n_ctx=8196.</Text>
            <Text>3. T3 samples speech tokens until stop_speech(6562) or N_PREDICT(1000).</Text>
            <Text>4. SILENCE_COUNT copies of token 4299 appended for S3Gen.</Text>
            <Text>5. S3Gen CFM: 960 samples/token at 24 kHz (~0.04 s per speech token).</Text>
          </Stack>
        </CardBody>
      </Card>

      <Grid columns={2} gap={16}>
        <Card>
          <CardHeader>Nano maximum usable example</CardHeader>
          <CardBody>
            <Stack gap={8}>
              <Text weight="semibold">long_25pct slice · 487 chars · 135 BPE</Text>
              <Text size="small" tone="secondary">
                First quarter of benchmark_text.txt — flowing prose.
              </Text>
              <Text size="small">
                Header defaults: 790 speech tokens, 31.7 s, eos=1. Content can regress after ~14 s in long runs.
              </Text>
            </Stack>
          </CardBody>
        </Card>
        <Card>
          <CardHeader>Nano cliff example</CardHeader>
          <CardBody>
            <Stack gap={8}>
              <Text weight="semibold">Full benchmark · 1951 chars · 556 BPE</Text>
              <Text size="small" tone="secondary">eval_out/benchmark_text.txt entire string.</Text>
              <Text size="small">
                15 speech tokens, 0.68 s, stop_speech at step 15. Not ctx overflow (933 slots). Knobs do not fix.
              </Text>
            </Stack>
          </CardBody>
        </Card>
      </Grid>

      <H2>Round 1 — repeat-penalty vs length (count 10–27, nano)</H2>
      <Text size="small" tone="secondary">
        Source: knob_sweep_report.jsonl · speech tokens and silence picks
      </Text>
      <BarChart
        height={260}
        categories={D.knobBars.map((k) => k.id)}
        series={[
          { name: "Speech tokens generated", data: D.knobBars.map((k) => k.pred), tone: "info" },
          { name: "Silence token 4299 picks", data: D.knobBars.map((k) => k.sil), tone: "warning" },
        ]}
      />
      <Row gap={16}>
        <BarChart
          height={220}
          categories={D.knobBars.map((k) => k.id)}
          series={[{ name: "UTMOS (1–5)", data: D.knobBars.map((k) => k.utmos), tone: "success" }]}
          beginAtZero={false}
          yMin={3.8}
          yMax={4.7}
        />
        <BarChart
          height={220}
          categories={D.knobBars.map((k) => k.id)}
          series={[{ name: "Duration (seconds)", data: D.knobBars.map((k) => k.dur), tone: "neutral" }]}
        />
      </Row>

      <H2>Input BPE vs speech output (threshold probe)</H2>
      <Text size="small" tone="secondary">
        Source: threshold_report.json · reference line at 15-token cliff floor
      </Text>
      <Grid columns={2} gap={16}>
        <Stack gap={4}>
          <H3>Nano</H3>
          <LineChart
            height={220}
            categories={D.bpeNano.map((x) => x.l)}
            series={[
              { name: "Speech tokens", data: D.bpeNano.map((x) => x.p), tone: "info" },
              { name: "Duration (s)", data: D.bpeNano.map((x) => x.d), tone: "neutral" },
            ]}
            referenceLines={[{ value: 15, label: "cliff floor", tone: "danger" }]}
          />
        </Stack>
        <Stack gap={4}>
          <H3>Turbo</H3>
          <LineChart
            height={220}
            categories={D.bpeTurbo.map((x) => x.l)}
            series={[
              { name: "Speech tokens", data: D.bpeTurbo.map((x) => x.p), tone: "info" },
              { name: "Duration (s)", data: D.bpeTurbo.map((x) => x.d), tone: "neutral" },
            ]}
            referenceLines={[{ value: 15, label: "cliff floor", tone: "danger" }]}
          />
        </Stack>
      </Grid>

      <H2>Sampler time series — P(silence token 4299)</H2>
      <Text size="small" tone="secondary">
        count_10_27 nano · dump CSV sil4299_prob · downsampled every ~50 T3 steps
      </Text>
      <LineChart
        height={240}
        categories={D.silTimeline.cats}
        series={[
          { name: "repeat-penalty 1.2 header", data: D.silTimeline.header, tone: "info" },
          { name: "repeat-penalty 1.0", data: D.silTimeline.repeat1, tone: "danger" },
        ]}
        beginAtZero
        yMax={1}
      />
      <Text size="small">
        repeat=1.0 drives P(4299) above 0.9 for long stretches — silence collapse in the sampler, not VAD breath gaps.
      </Text>

      <H2>Analysis tools</H2>
      <Grid columns={2} gap={16}>
        <Card>
          <CardHeader>Used in sweep</CardHeader>
          <CardBody>
            <Stack gap={4}>
              {D.toolsUsed.map((t) => (
                <Text key={t} size="small">
                  {t}
                </Text>
              ))}
            </Stack>
          </CardBody>
        </Card>
        <Card>
          <CardHeader>Not used</CardHeader>
          <CardBody>
            <Stack gap={4}>
              {D.toolsSkipped.map((t) => (
                <Text key={t} size="small" tone="tertiary">
                  {t}
                </Text>
              ))}
            </Stack>
          </CardBody>
        </Card>
      </Grid>

      <Table
        headers={["Profile", "Tokens", "UTMOS", "HNR dB", "Emotion cos"]}
        rows={D.quality.map((q) => [q.id, String(q.pred), q.utmos.toFixed(2), q.hnr.toFixed(1), q.emo.toFixed(3)])}
        columnAlign={["left", "right", "right", "right", "right"]}
      />

      <Callout tone="warning" title="Phase 3 — edge knobs not yet swept">
        <Stack gap={4}>
          {D.phase3.map((p) => (
            <Text key={p} size="small">
              {p}
            </Text>
          ))}
        </Stack>
      </Callout>

      <Text size="small" tone="tertiary" style={{ borderTop: `1px solid ${theme.stroke.primary}`, paddingTop: 12 }}>
        Legal knobs: repeat-penalty, temperature, top-k, top-p, repeat-last-n, seed, n-predict, cfm-steps, silence-token,
        silence-count. C++ headers match PyTorch Turbo (top_p=0.95, repeat=1.2).
      </Text>
    </Stack>
  );
}
