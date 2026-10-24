# Cartesia Voice for Hermes

Native Python **TTS-only** plugin for Hermes Agent **>=0.22.5**. Plugin ID:
`cartesia-voice`; provider ID: `cartesia`; version: `0.2.0`.

Whole-file WAV and MP3 synthesis is implemented. **Streaming, STT, voice cloning,
voice discovery over the network, and realtime conversation are deferred.**
This repository is independent of Nous Research and Cartesia. Passing local
validation is not a claim of Hermes catalog acceptance.

## Privacy, billing and execution disclosure

- Synthesis sends your text, configured voice/model, language and speed to
  **https://api.cartesia.ai/tts/bytes**. Cartesia is a paid cloud service and its
  billing, retention and terms apply. Do not synthesize sensitive text without
  reviewing those policies. There is no automatic retry that could duplicate billing.
- Credentials are resolved on every call exclusively with
  `agent.secret_scope.get_secret('CARTESIA_API_KEY')`. The plugin never reads
  credential files, parses dotenv, or caches another profile's secrets.
- Settings use Hermes's profile-aware `hermes_cli.config.load_config()`.
  Synthesized audio is written to the caller's requested directory, with an
  accurate extension, via a mode-0600 temporary file and atomic replacement.
- There are no plugin subprocesses, telemetry, background workers, self-updates,
  additional runtime packages, or network calls at import/registration/picker time.
  Hermes itself may run ffmpeg for voice-bubble conversion.
- All HTTP redirects are refused. The API host is fixed; there is no custom
  endpoint setting. HTTPS certificate verification remains enabled.

## Install and configure

Install the standalone repository through Hermes:

```sh
hermes plugins install Aquillum/hermes-cartesia-voice-plugin --enable
```

For a local checkout, place the reviewed plugin directory under the selected
profile's `plugins/cartesia-voice/` directory and run
`hermes plugins enable cartesia-voice`. The installed CLI accepts catalog/Git
sources, not a local path, for `plugins install`.

Use `hermes --profile PROFILE ...` for a named profile; otherwise these commands
affect the active profile.

Use Hermes's credential/setup UI (`hermes tools`) to provide `CARTESIA_API_KEY`
for the active profile. Never put the key in `config.yaml`, this repository, or a
shell command that would save it in history. Configure a voice ID from your
Cartesia account; **there is intentionally no guessed default voice**:

```sh
hermes config set --force tts.cartesia.voice YOUR_CARTESIA_VOICE_ID
hermes config set --force tts.cartesia.model sonic-3.5
hermes config set --force tts.cartesia.language de
hermes config set --force tts.cartesia.speed 1.0
hermes config set tts.provider cartesia
```

Restart Hermes after enabling. A legacy `tts.providers.cartesia` command provider
wins over this plugin; remove that entry through your normal Hermes configuration
workflow before testing native dispatch. Built-in providers are not overridden.

### Settings

Settings live under **`tts.cartesia`**, not `plugins.entries.cartesia-voice.config`.
The latter is why the manifest intentionally has no misleading `config_schema`.
Provider settings override synthesize arguments (including global arguments
forwarded by Hermes); those arguments override `tts.voice/model/speed` defaults.
This lets a Cartesia voice override an unrelated global voice such as `alloy`.

| Setting | Default | Notes |
| --- | --- | --- |
| `voice` | required | Account voice ID; configured voice and saved favorites appear in local metadata |
| `model` | `sonic-3.5` | Deliberately pinned compatibility baseline, not `sonic-latest` |
| `speed` | global `tts.speed`, then `1.0` | 0.6–1.5 inclusive; invalid speeds fail, not silently clamp |
| `language` | unset | Optional language/locale string, e.g. `de` or `en-GB` |
| `timeout` | `60` | Socket/network-operation timeout in seconds, 1–300; not a total wall-clock deadline |
| `sample_rate` | `44100` | 8000, 16000, 22050, 24000, 44100 or 48000 Hz |
| `api_version` | `2026-08-14` | Version documented by Cartesia; configurable date header |

Current Cartesia docs also list Sonic 3.6. This plugin deliberately retains
`sonic-3.5`; select another model explicitly only after testing your account.
The local model picker advertises the pinned baseline, not an invented live catalog.

### Five personal voice favorites and Desktop settings

The same package includes an opt-in native Desktop settings page. After installing
the agent plugin in the intended profile, open **Capabilities → Plugins → Cartesia
Voice**, enable its Desktop half, and open **Settings → Plugins → Cartesia Voice**.
If discovery is stale, run **Reload desktop plugins** from the command palette.

- Five slots: **Voice Name**, **Language**, **Gender** (`male` / `female`), and
  **Voice ID** (UUID). Leave an entire slot blank or complete all four fields.
  Names are personal labels, not required to match Cartesia's catalog names.
- **Save favorites** persists edits. The radio button selects one saved voice
  for the **loaded backend profile**. Editing an entry does not select it;
  deleting an entry does not silently replace the active voice.
- Selection writes only `tts.cartesia.voice` and `tts.cartesia.language` through
  Hermes configuration APIs. The language is the explicit synthesis hint. It
  does not change `tts.provider`, enable automatic read-aloud, or supply API keys.
- The catalog is deliberately **shared across profiles of this local OS user**,
  at `<default Hermes root>/plugin-data/cartesia-voice/favorites.json`. It is not
  stored in the plugin directory or Git, so updates do not overwrite personal
  entries. Profiles retain separate selected voices and credentials. A remote
  backend has its own local catalog; no cross-machine synchronization is implied.
- Import/export uses the expandable JSON editor. Import creates a draft; inspect
  it and press **Save favorites** to persist. Export never includes credentials.
- Reading, saving and selecting favorites make **no Cartesia requests and spend
  no TTS credits**. There is no automatic preview or full-catalog download.

**SDK profile limitation:** on the tested Hermes SDK, plugin REST follows the
active **gateway profile**, not the separate Settings **Applies to** override.
Always check **Loaded backend profile** on this page. Switch the active gateway
profile and click **Reload** to retarget; changing only **Applies to** is not
sufficient. Every write checks and verifies the backend profile. A profile where
the agent plugin is absent/disabled cannot serve this page's backend API. This
release does not claim Settings-selector-aware routing or a phone/watch editor.

The current favorites filesystem implementation targets Linux/macOS. Windows
favorites management is not supported in this release.

### Formats and limits

- `mp3` requests MP3 at 128 kbps; `wav` requests mono PCM16 WAV.
- `ogg`, `opus`, `flac` (and unknown format values) return **WAV with a `.wav`
  path**, not mislabeled bytes. The caller must honor the returned path.
- `voice_compatible=True` lets Hermes convert to Opus/OGG with **ffmpeg** for
  Telegram/Discord voice bubbles. Install ffmpeg on the Hermes host if needed;
  the plugin does not install or execute it. Conversion and delivery are separate
  from synthesis and must be tested on the target platform.
- At most 20,000 input characters and 32 MiB response bytes per call. The text
  limit is a conservative plugin safety bound, not a claim about every Cartesia
  model's limit. Input is rejected, never truncated. For long content, use Hermes's
  chunking controls (e.g. `tts.cartesia.max_text_length` at or below 20000).
- Non-200 responses, unexpected MIME types, empty/oversized/truncated WAV,
  invalid MP3 frame headers, and transport failures raise sanitized errors.
  MP3 header checks are not a full audio decoder. Existing output is preserved
  when synthesis or atomic replacement fails.

## Development and verification

No API key or paid request is needed for tests. Use a prepared Hermes environment
(or an isolated virtual environment with Hermes installed); do not inject packages
into a managed Hermes environment with ad-hoc pip.

```sh
PYTHONPATH=/path/to/hermes-agent python -m unittest discover -s tests -v
node --test tests/test_desktop.mjs
hermes plugins doctor /path/to/clean-plugin-copy --ci
hermes plugins validate /path/to/clean-plugin-copy --install-deps
```

Use a clean plugin copy containing only deliverable source files. Keep a diagnostic
`HERMES_HOME` outside that copy: an isolated home nested inside the plugin can be
recursively copied by the diagnostic tooling. Doctor isolates its own probe.
The validator may install the declared package into its own probe environment;
the plugin itself has no runtime dependencies.
Python tests use the real Hermes TTS ABC, profile/config APIs and secret-scope
context with mocked HTTP transport. Desktop tests mount React using the existing
Hermes checkout's React/React DOM/jsdom; set `HERMES_AGENT_ROOT` when necessary.
These test fixtures are synthetic, not a live gateway/Desktop integration.
Red/green records are local `evidence/*.log` (ignored by Git).
Real Cartesia synthesis, ffmpeg conversion and platform delivery require
separately authorized integration checks; unit tests do not prove cloud account
access or audible quality.

References: [Hermes plugins](https://hermes-agent.nousresearch.com/docs/developer-guide/plugins),
[Hermes TTS](https://hermes-agent.nousresearch.com/docs/user-guide/features/tts),
[catalog rules](https://hermes-agent.nousresearch.com/docs/developer-guide/plugins/catalog-submission),
[Cartesia bytes API](https://docs.cartesia.ai/api-reference/tts/bytes).

## License

MIT — Copyright (c) 2026 Aquillum.
