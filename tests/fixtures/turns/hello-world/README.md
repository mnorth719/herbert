# hello-world fixture

Seed fixture for the happy-path e2e scenario (Unit 7b).

## Contents (as of Unit 4 landing)

- `input.wav` — 1s of 16kHz mono silence. Placeholder; the e2e replay
  transport does not need to run whisper on it (it reads `stt.json`
  directly). Kept so future fixture tooling has a concrete PCM artifact
  in every fixture directory.
- `stt.json` — synthetic transcript `"hello herbert"`.

## Replacing with a real recording

Once `scripts/capture-fixture.py` lands in Unit 7b, a real PTT session can
be recorded against a live Claude + ElevenLabs turn and serialized here.
The replay providers in `tests/e2e/replay_transport.py` read the manifest
files verbatim; nothing else needs to change.
