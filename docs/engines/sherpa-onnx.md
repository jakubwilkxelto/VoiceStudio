# VoiceStudio — Sherpa-ONNX Engine

Sherpa-ONNX (k2-fsa/sherpa-onnx) is a unified C++ ONNX runtime that wraps
20+ TTS model families (VITS, MeloTTS, Piper, Kokoro, Matcha, and more)
behind one API, with pre-built wheels for Linux, Windows, and macOS (x86 and
ARM). You bring the model: point VoiceStudio at any downloaded sherpa-onnx
TTS model directory.

## When to pick it

- You want a specific community model (e.g. a Piper or VITS voice for your
  language) that no other engine hosts.
- You need a dependable CPU engine with optional CUDA acceleration.

## Setup

1. Install the runtime:

   ```bash
   pip install sherpa-onnx
   ```

2. Download a TTS model from the
   [sherpa-onnx releases](https://github.com/k2-fsa/sherpa-onnx/releases)
   and unpack it somewhere permanent.

3. Point VoiceStudio at the model directory and restart:

   ```bash
   export OMNIVOICE_SHERPA_MODEL=/path/to/model-dir
   ```

4. Select the engine via **Model Catalogue** (TTS tab → **Use**) or
   `OMNIVOICE_TTS_BACKEND=sherpa-onnx`.

The directory must contain `tokens.txt` and the model: `model.onnx`, or the
single `<voice>.onnx` that Piper voices (`vits-piper-*`) ship. Files the release
puts next to it are picked up automatically: `espeak-ng-data/` (Piper) and
`lexicon.txt` (lexicon-based VITS such as MeloTTS). Sherpa-ONNX ships
no bundled default model, so the engine reports unavailable — with the
reason — until `OMNIVOICE_SHERPA_MODEL` points at a valid directory. (Before
this gate, selecting the engine unconfigured produced a failure mislabeled
as out-of-memory —
[#919](https://github.com/debpalash/VoiceStudio/issues/919).)

## Configuration

| Variable | Default | Meaning |
| --- | --- | --- |
| `OMNIVOICE_SHERPA_MODEL` | (unset) | Directory containing `model.onnx` (or one Piper `<voice>.onnx`) + `tokens.txt` |

### Example: a Turkish Piper voice

```bash
curl -LO https://github.com/k2-fsa/sherpa-onnx/releases/download/tts-models/vits-piper-tr_TR-dfki-medium.tar.bz2
tar xjf vits-piper-tr_TR-dfki-medium.tar.bz2
export OMNIVOICE_SHERPA_MODEL=$PWD/vits-piper-tr_TR-dfki-medium
```

The same pattern works for every `vits-piper-<locale>-<voice>` archive on the
releases page.

## Licences

The model you bring keeps its own terms. VoiceStudio has no licence record for
a local model folder, so the Model Catalogue licence prompt does not cover it
and shows nothing for this engine. Read the `MODEL_CARD` in the unpacked
folder before you use its audio. For example, `tr_TR-dfki-medium` is trained
on CC BY-NC-SA 4.0 data (non-commercial). `tr_TR-fahrettin-medium` and
`tr_TR-fettah-medium` use CC0 data but are fine-tuned from the English
`lessac` voice, whose dataset has its own terms.

## Behaviour notes

- Output defaults to 22.05 kHz (the VITS default); once a model is loaded,
  its own sample rate is used.
- CPU is the universal baseline; the CUDA onnxruntime provider is available
  on Linux/Windows installs.
- **No cloning**: voices come from the model itself. Multi-speaker VITS
  models select a voice by numeric speaker id; speed is supported.
- Languages depend entirely on the model you download.

## Known limits

- One model at a time — switching models means changing
  `OMNIVOICE_SHERPA_MODEL` and restarting.
- No voice design, no reference-audio cloning, no emotion controls
  (see [expressive-speech.md](../expressive-speech.md)).

## Troubleshooting

- "OMNIVOICE_SHERPA_MODEL not set" / "No model.onnx in …": follow Setup
  above — the variable must point at the *unpacked* model directory, not
  the archive. A directory with several `.onnx` files and no `model.onnx`
  is ambiguous and reported the same way.
- Numbers are only verbalized where the text normalizer supports the
  language; decimals written with a comma (`1,5`) reach the model as digits.
  Write them out (`bir virgül beş`) for the cleanest read.
- Other issues: [install/troubleshooting.md](../install/troubleshooting.md).

See also: [benchmarks.md](../benchmarks.md),
[languages.md](../languages.md),
[disk usage](disk-usage.md).
