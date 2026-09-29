# realtime_transcription

Test of the [Together.AI realtime transcription example](https://github.com/togethercomputer/together-py/blob/main/examples/realtime_transcription.py).

Streams PCM audio and receives interim + finalized transcripts using
`together.beta.realtime.transcription` with the `openai/whisper-large-v3` model.

## Setup

The `realtime` extra is not yet on PyPI, so install from GitHub main
(requires Python >= 3.10):

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install "together[realtime] @ git+https://github.com/togethercomputer/together-py.git"
```

## Usage

### From a WAV file

```bash
export TOGETHER_API_KEY=...
python3 examples/realtime_transcription.py audio.wav   # 16 kHz mono s16le WAV
```

### Live dictation from your microphone

```bash
pip install sounddevice numpy
export TOGETHER_API_KEY=...
python3 examples/realtime_mic_transcription.py               # default input device
python3 examples/realtime_mic_transcription.py --list-devices  # find your AirPods' index
python3 examples/realtime_mic_transcription.py --device 2      # use that device explicitly
```

Streams live PCM straight from the mic to Together.AI, printing interim
text as you talk and a finalized transcript per utterance. Press Ctrl+C to
stop and print the full transcript. Requires mic permission for your
terminal app (macOS: System Settings > Privacy & Security > Microphone).

**Using AirPods**: pair/connect them normally (macOS routes both playback
and mic through the same Bluetooth profile once selected as the input
device in System Settings > Sound, or via `--device <index>` above).
AirPods' Bluetooth mic (HFP) is natively 16kHz mono, matching this script's
required format exactly.

## Verified

Ran successfully against a generated 16kHz mono WAV test clip (via macOS `say`
+ `afconvert`), producing correct interim and final transcripts.
