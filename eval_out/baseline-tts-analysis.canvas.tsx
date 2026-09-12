import {
  Callout,
  Card,
  CardBody,
  CardHeader,
  Divider,
  Grid,
  H1,
  H2,
  H3,
  LineChart,
  Pill,
  Row,
  Stack,
  Stat,
  Table,
  Text,
  UsageBar,
  useCanvasState,
  useHostTheme,
} from "cursor/canvas";

const SRC =
  "The courier left the package at 10 Downing Street, London SW1A 2AA this morning. Count seventeen to twenty-five: seventeen, eighteen, nineteen, twenty, twenty-one, twenty-two, twenty-three, twenty-four, twenty-five.";

const TIME = [
  "0",
  "1",
  "2",
  "3",
  "4",
  "5",
  "6",
  "7",
  "8",
  "9",
  "10",
  "11",
  "12",
  "13",
];

const VOICED = {
  nano: [0.551, 0.54, 0.38, 0.86, 0.67, 0.75, 0.5, 0.45, 0.68, 0.44, 0.57, 0.65, 0.4, 0.59],
  turbo: [0.516, 0.53, 0.72, 0.64, 0.716, 0.49, 0.44, 0.75, 0.66, 0.87, 0.67, 0.5, 0.67, 0.46],
  v3: [0.472, 0.67, 0.46, 0.79, 0.606, 0.49, 0.63, 0.53, 0.69, 0.72, 0.74, 0.7, 0.59, 0.51],
};

const HNR = {
  nano: [12.71, 14.33, 15.06, 16.36, 14.4, 14.79, 19.71, 14.77, 15.15, 16.15, 17.37, 19.74, 12.86, 16.31],
  turbo: [12.41, 14.58, 16.27, 14.04, 13.32, 17.2, 11.76, 13.84, 13.32, 22.16, 16.74, 14.97, 16.98, 13.66],
  v3: [13.7, 14.26, 16.78, 19.38, 11.88, 15.77, 15.26, 14.13, 20.74, 21.35, 16.05, 15.32, 14.38, 13.81],
};

type Family = "nano" | "turbo" | "v3";

const FAMILIES: Record<
  Family,
  {
    wav: string;
    arch: string;
    duration: number;
    tokens: number;
    eos: number;
    sil: number;
    silRuns: string;
    pauses: number;
    tone: string;
    wer: number;
    cer: number;
    utmos: number;
    pq: number;
    ce: number;
    f0: number;
    f0std: number;
    voiced: number;
    jitter: number;
    shimmer: number;
    hnr: number;
    mfcc: number;
    emo: number;
    asr: string;
    fails: string;
    knobs: string;
    extra: string;
  }
> = {
  nano: {
    wav: "20260912-130448-nano.wav",
    arch: "GPT-2 T3, 12 layer, 768 emb, gpt2_bpe",
    duration: 18.6,
    tokens: 463,
    eos: 1,
    sil: 18,
    silRuns: "1,1,1,1,3,2,1,1,2,5 (clustered after 13 s)",
    pauses: 8,
    tone: "mixed",
    wer: 0.1852,
    cer: 0.0569,
    utmos: 4.412,
    pq: 7.892,
    ce: 6.444,
    f0: 225.5,
    f0std: 49.16,
    voiced: 0.559,
    jitter: 0.0163,
    shimmer: 0.0741,
    hnr: 15.7,
    mfcc: 0.9936,
    emo: -0.1367,
    asr: "The courier left the package at 10 Downing Street, London Air's W1A 2AA this morning. Count 17 to 25, 17, 18, 19, 20, 21, 22, 23, 24, 25",
    fails: "dropped_street_postal_time (literal sw1a 2aa missing). Integers 17-25 present. Tail 25 present.",
    knobs: "empty stamp = header: T 0.8, top-k 1000, top-p 0.95, repeat-penalty 1.2, repeat-last-n 1000, N_PREDICT 1000, CFM 2, silence-token 4299, SILENCE_COUNT 3. No min-p / cfg.",
    extra: "Language not used. Vulkan synth. Dump n_ctx 8196, stop_speech 6562.",
  },
  turbo: {
    wav: "20260912-130506-turbo.wav",
    arch: "GPT-2 T3, 24 layer, 1024 emb, gpt2_bpe",
    duration: 16.0,
    tokens: 398,
    eos: 1,
    sil: 3,
    silRuns: "1,1,1 at 0.04 s, 6.24 s, 14.88 s",
    pauses: 2,
    tone: "flowing",
    wer: 0.5185,
    cer: 0.2114,
    utmos: 4.303,
    pq: 7.849,
    ce: 6.389,
    f0: 208.1,
    f0std: 30.98,
    voiced: 0.595,
    jitter: 0.0174,
    shimmer: 0.0758,
    hnr: 15.22,
    mfcc: 0.9961,
    emo: -0.129,
    asr: "The courier left a package at 10 Downing Street, London, September 21A-2AA this morning. Counts 17-25, 17-18, 19-19, 20, 21-22, 23-24, 25.",
    fails: "dropped_street_postal_time. Integers found as substrings inside hyphenated spans. Tail 25 present. WER is the large gap vs source wording.",
    knobs: "same GPT-2 header defaults as nano. SILENCE_COUNT 3 never tripped (no 3-in-a-row 4299).",
    extra: "Language not used. Vulkan synth. Dump n_ctx 8196, stop_speech 6562.",
  },
  v3: {
    wav: "20260912-130527-v3.wav",
    arch: "Llama T3, mtl_bpe, language argv en",
    duration: 14.24,
    tokens: 358,
    eos: 1,
    sil: 2,
    silRuns: "2 consecutive at 5.40-5.44 s (sentence boundary)",
    pauses: 0,
    tone: "flowing",
    wer: 0.2963,
    cer: 0.1626,
    utmos: 4.432,
    pq: 7.832,
    ce: 6.466,
    f0: 234.8,
    f0std: 40.61,
    voiced: 0.605,
    jitter: 0.0166,
    shimmer: 0.0677,
    hnr: 16.21,
    mfcc: 0.9971,
    emo: -0.1339,
    asr: "The courier left the package at 10 Downing Street, London, Swann-wann-na, 2AA this morning. Counts 17-25, 17, 18, 19, 20, 21, 20, 21, 22, 24, 25.",
    fails: "dropped_integers (23 missing; 20 and 21 repeated). dropped_street_postal_time. Tail 25 present. English ASR, not a Chinese hyp on this text.",
    knobs: "empty stamp = header: T 0.8, top-k 0, top-p 1.0, min-p 0.05, cfg-weight 0.5, CFM 10, cfm-cfg 0.7, repeat-penalty 1.2, N_PREDICT 1000, silence-token 4299. No SILENCE_COUNT.",
    extra: "models/v3.language = en. Vulkan synth. C++ checkout after this run is v3.",
  },
};

export default function BaselineTtsAnalysis() {
  const theme = useHostTheme();
  const [fam, setFam] = useCanvasState<Family>("family", "nano");
  const f = FAMILIES[fam];

  return (
    <Stack gap={24}>
      <Stack gap={8}>
        <H1>Two-sentence baseline, header defaults</H1>
        <Text tone="secondary">
          Same English prompt on nano, turbo, and v3. Vulkan synth, CPU eval.
          Empty knobs. No ranking. Source: eval_out/report.json plus windowed
          parselmouth / silero-vad / dump CSV on the dated WAVs.
        </Text>
      </Stack>

      <Callout tone="warning" title="Length cap is not the failure">
        All three reached eos 1 (stop_speech) with 358-463 speech tokens against
        N_PREDICT 1000. This prompt is not too long for the generation budget.
        All three FAIL the PASS gate because WER is above 0.12 and the literal
        postal SW1A 2AA is missing after medium.en + jiwer. v3 also drops 23.
      </Callout>

      <Text tone="secondary" size="small">
        Prompt: {SRC}
      </Text>

      <H2>Token budget vs stop</H2>
      <Text tone="secondary" size="small">
        Usage of N_PREDICT 1000. Remainder is unused cap. Duration follows 960
        samples/token at 24 kHz, so shorter audio is fewer tokens, not a quality
        score.
      </Text>
      <Stack gap={10}>
        <UsageBar
          total={1000}
          topLeftLabel="nano 463 tokens, 18.6 s, eos 1"
          topRightLabel="463 / 1000"
          segments={[{ id: "nano", value: 463, color: "blue" }]}
        />
        <UsageBar
          total={1000}
          topLeftLabel="turbo 398 tokens, 16.0 s, eos 1"
          topRightLabel="398 / 1000"
          segments={[{ id: "turbo", value: 398, color: "purple" }]}
        />
        <UsageBar
          total={1000}
          topLeftLabel="v3 358 tokens, 14.24 s, eos 1, language en"
          topRightLabel="358 / 1000"
          segments={[{ id: "v3", value: 358, color: "orange" }]}
        />
      </Stack>

      <H2>How they differ on the same text</H2>
      <Table
        headers={[
          "Family",
          "Tone",
          "WER",
          "UTMOS",
          "PQ / CE",
          "HNR dB",
          "VAD pauses",
          "4299 sil",
        ]}
        columnAlign={["left", "left", "right", "right", "right", "right", "right", "right"]}
        rowTone={["warning", "warning", "warning"]}
        rows={[
          ["nano GPT-2", "mixed", "0.185", "4.41", "7.89 / 6.44", "15.7", "8", "18"],
          ["turbo GPT-2", "flowing", "0.519", "4.30", "7.85 / 6.39", "15.2", "2", "3"],
          ["v3 Llama + en", "flowing", "0.296", "4.43", "7.83 / 6.47", "16.2", "0", "2"],
        ]}
      />
      <Text tone="secondary" size="small">
        Source: eval_out/report.json. Verdict FAIL on every row. Tone from VAD+F0
        only. UTMOS is utmos-pytorch on CPU. PQ/CE from audiobox-aesthetics.
      </Text>

      <H2>Naturalness over time (does not collapse)</H2>
      <Text>
        Human-range here means F0 stays in the 75-400 Hz analysis band with
        voiced fraction not stuck at zero, and HNR stays above about 10 dB except
        at pauses. Windowed scores cover the overlapping 0-13 s body. nano
        continues to 18.6 s, turbo to 16.0 s; those tails still have voiced
        frames and HNR above 11 dB.
      </Text>
      <Grid columns={2} gap={16}>
        <Stack gap={8}>
          <H3>Voiced fraction, 1 s bins</H3>
          <LineChart
            categories={TIME}
            beginAtZero
            yMax={1}
            height={220}
            series={[
              { name: "nano", data: VOICED.nano },
              { name: "turbo", data: VOICED.turbo },
              { name: "v3", data: VOICED.v3 },
            ]}
          />
          <Text tone="secondary" size="small">
            Mean of two 0.5 s parselmouth windows. Dips align with pauses (nano
            after 6 s and on the count) or unvoiced consonants, not a flatline.
            Source: parselmouth Pitch 75-400 Hz on the dated WAVs.
          </Text>
        </Stack>
        <Stack gap={8}>
          <H3>HNR, 1 s bins (dB)</H3>
          <LineChart
            categories={TIME}
            yMin={8}
            yMax={24}
            beginAtZero={false}
            height={220}
            valueSuffix=" dB"
            referenceLines={[{ value: 10, label: "10 dB floor", tone: "neutral" }]}
            series={[
              { name: "nano", data: HNR.nano },
              { name: "turbo", data: HNR.turbo },
              { name: "v3", data: HNR.v3 },
            ]}
          />
          <Text tone="secondary" size="small">
            Harmonicity CC mean per 1 s. All families stay in a conversational
            12-22 dB band through the count. Source: praat-parselmouth on the
            dated WAVs.
          </Text>
        </Stack>
      </Grid>

      <H2>Coverage vs acoustics</H2>
      <Table
        headers={["Check", "nano", "turbo", "v3"]}
        rows={[
          ["Downing Street", "present", "present", "present"],
          ["Literal SW1A 2AA", "missing (Air's W1A 2AA)", "missing (September 21A-2AA)", "missing (Swann-wann-na 2AA)"],
          ["Integers 17-25 in order", "all present", "substring-present, hyphenated", "23 missing; 20, 21 repeated"],
          ["Tail 25", "present", "present", "present"],
          ["Trailing garbage", "no", "no", "no"],
          ["clip_frac", "0", "0", "0"],
          ["MFCC vs reference.wav", "0.994", "0.996", "0.997"],
          ["emotion2vec vs ref", "-0.137", "-0.129", "-0.134"],
        ]}
      />
      <Text tone="secondary" size="small">
        Speaker cosine is MFCC-mean vs reference.wav (17.88 s, 24 kHz). Affect
        cosine is funasr emotion2vec_plus_base at 16 kHz vs the same reference.
        High timbre match, negative affect match: they clone spectrum, not the
        reference utterance's emotion contour.
      </Text>

      <H2>Family detail</H2>
      <Row gap={8} wrap>
        {(["nano", "turbo", "v3"] as Family[]).map((name) => (
          <Pill key={name} active={fam === name} onClick={() => setFam(name)}>
            {name}
          </Pill>
        ))}
      </Row>
      <Card>
        <CardHeader trailing={<Pill size="sm" active>FAIL</Pill>}>
          {fam} · {f.wav}
        </CardHeader>
        <CardBody>
          <Grid columns={4} gap={12}>
            <Stat value={`${f.duration}s`} label="duration" />
            <Stat value={String(f.tokens)} label="predicted tokens" />
            <Stat value={f.tone} label="tone class" />
            <Stat value={f.wer.toFixed(3)} label="WER" tone="danger" />
          </Grid>
          <Divider />
          <Stack gap={8}>
            <Text weight="semibold">{f.arch}</Text>
            <Text>{f.fails}</Text>
            <Text tone="secondary">ASR hyp: {f.asr}</Text>
            <Text tone="secondary">{f.knobs}</Text>
            <Text tone="secondary">
              Jitter {f.jitter.toFixed(4)}, shimmer {f.shimmer.toFixed(4)}, F0 mean{" "}
              {f.f0.toFixed(1)} Hz, F0 std {f.f0std.toFixed(1)}, voiced frac{" "}
              {f.voiced.toFixed(3)}. Silence 4299 count {f.sil}; runs {f.silRuns}.
              VAD pauses {f.pauses}. {f.extra}
            </Text>
          </Stack>
        </CardBody>
      </Card>

      <H2>Header defaults that show up here</H2>
      <Grid columns={2} gap={16}>
        <Card>
          <CardHeader trailing="all three families">Shared</CardHeader>
          <CardBody>
            <Text>
              Temperature 0.8, repeat-penalty 1.2, repeat-last-n 1000, seed 42,
              N_PREDICT 1000, silence-token 4299. Repeat-penalty over a 1000-token
              window is live while counting seventeen to twenty-five; that is a
              plausible contributor to v3 skipping 23 and repeating 20/21, but
              this session did not sweep the knob.
            </Text>
          </CardBody>
        </Card>
        <Card>
          <CardHeader trailing="not interchangeable">Family-only</CardHeader>
          <CardBody>
            <Text>
              nano/turbo: top-k 1000, top-p 0.95, CFM steps 2, SILENCE_COUNT 3.
              v3: top-k 0, top-p 1.0, min-p 0.05, cfg-weight 0.5, CFM steps 10,
              cfm-cfg 0.7, required language en. SILENCE_COUNT is not in v3.h.
              MIN_P is not in nano.h or turbo.h. CFM 2 vs 10 did not split UTMOS
              or PQ on this text; token count did split duration.
            </Text>
          </CardBody>
        </Card>
      </Grid>
      <Text tone="tertiary" size="small" style={{ color: theme.text.tertiary }}>
        Synth backend is Vulkan (GGML_VULKAN=ON). Eval libraries ran on CPU
        (faster-whisper medium.en int8, utmos-pytorch, audiobox, funasr). pystoi
        is installed but not scored: reference.wav is a different utterance, not
        an aligned same-text reference. Knob sweeps are later sessions.
      </Text>
    </Stack>
  );
}
