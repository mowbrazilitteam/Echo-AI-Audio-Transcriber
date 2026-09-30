<p align="center"><img src="icones/echo-ai-128.png" width="96" height="96" alt="Echo-AI"></p>

<h1 align="center">Echo-AI</h1>
<p align="center"><b>Echo AI Audio Transcriber</b><br>Private audio transcription with a local AI assistant. Windows, macOS and Linux, on the GPU or the CPU.</p>
<p align="center"><b>English</b> · <a href="README.pt-BR.md">Português</a> · <a href="README.es.md">Español</a></p>

<p align="center"><img src="docs/tela-en.png" alt="Echo-AI: a transcript with the audio player and an AI summary" width="900"></p>

Drop any audio or video file, or record with the microphone. Echo-AI transcribes it on your own computer with
[Whisper](https://github.com/openai/whisper), shows the text live, lets you play it back with the transcript
highlighted, and answers questions about what was said with a local AI. Nothing leaves your computer.

## Features

- **Any file with audio.** mp3, m4a, wav, ogg, opus, flac, webm, mp4, mkv, mov, voice notes and more. The format is
  detected from the content, not the file name.
- **Record** straight from the microphone.
- **Live transcript** with timestamps; click a line to hear it. Export as `.txt` or `.srt` subtitles.
- **Ask about the audio:** summaries, key topics, decisions and next steps, points of attention, or any question.
  Answers cite the part of the audio they came from, and you can search your whole history.
- **Models side by side.** Six Whisper models and six local AIs, each with its accuracy (or quality), speed, size and
  a *best for your PC* badge. Pick one, and if it is not installed yet, it downloads right there with a progress bar.
- **Plug and play.** It detects your hardware and picks where to run: an NVIDIA GPU when available, the CPU otherwise.
  No drivers or Python to install by hand; the AI engine installs itself when you first need it.
- **Private by design.** Audio, transcripts and history are stored only on your computer. The internet is used only to
  download a model when you ask. No accounts, no telemetry.
- **English, Portuguese and Spanish** interface, dark and light themes.

## Download

Get the installer for your system from the [latest release](https://github.com/mowbrazilitteam/Echo-AI-Audio-Transcriber/releases/latest).
The Whisper **Small** model comes inside, so your first transcription works offline.

| System | File | First launch |
|---|---|---|
| Windows 10 / 11 (64-bit) | `echo-ai-*-windows.msi` | Installs for your user only, with no administrator password. The installer is not code-signed yet: on the SmartScreen warning, click **More info → Run anyway**. |
| macOS 13+ | `echo-ai-*-mac-arm.dmg` for Apple Silicon (M1 or newer), `echo-ai-*-mac-intel.dmg` for Intel | Drag Echo-AI to Applications (without an administrator account, to an `Applications` folder inside your home folder). The app is not notarized yet: the first time, right-click it and choose **Open**, or allow it in **System Settings → Privacy & Security**. |
| Ubuntu 24.04+ and derivatives | `echo-ai-*-linux.deb` | `sudo apt install ./echo-ai-*-linux.deb` (a `.deb` always asks for `sudo`; without it, [install from the terminal](#install-from-the-terminal-no-admin)) |
| Any other Linux | — | [Install from the terminal](#install-from-the-terminal-no-admin): two commands, no admin. |

**Requirements:** 8 GB of RAM (16 GB for the largest AI), about 3 GB of free disk space, and optionally an NVIDIA
graphics card with its driver for much faster transcription. Everything also runs on the CPU, just slower.

## Install from the terminal (no admin)

For people who prefer the command line, or who can't run an installer. Everything goes into your user folder: no
administrator password, no system Python and no git. [uv](https://docs.astral.sh/uv/) downloads Python 3.12 just for
Echo-AI.

**macOS and Linux**

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
uv tool install https://github.com/mowbrazilitteam/Echo-AI-Audio-Transcriber/archive/refs/tags/v1.0.0.zip
echo-ai
```

**Windows** (PowerShell)

```powershell
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
uv tool install https://github.com/mowbrazilitteam/Echo-AI-Audio-Transcriber/archive/refs/tags/v1.0.0.zip
echo-ai
```

- If `uv` or `echo-ai` is not found, open a new terminal (the uv installer adds `~/.local/bin` to your `PATH`), or run
  `uv tool update-shell`.
- The installers bring the Whisper Small model inside; here it is downloaded on first use. The first time you send an
  audio, Echo-AI opens the model list: pick **Small** (about 480 MB) or another one.
- `echo-ai --navegador` opens the interface in your browser instead of the app window. On a minimal Linux without the
  libraries the window needs, Echo-AI opens in the browser by itself.
- With git installed, `uv tool install git+https://github.com/mowbrazilitteam/Echo-AI-Audio-Transcriber.git` installs the latest code from `main` instead of a release.

## How it works

| Part | What runs | Where |
|---|---|---|
| Transcription | [faster-whisper](https://github.com/SYSTRAN/faster-whisper) (Whisper on CTranslate2) | NVIDIA GPU in float16, or the CPU in int8 |
| AI answers | [Ollama](https://ollama.com) with models of 1 to 8 billion parameters | uses the Ollama already on your computer, or installs a private copy for Echo-AI |
| Memory search | SQLite full-text search plus `nomic-embed-text` vectors | your computer |
| Interface | a local web app in its own window (WebView2, WebKit or Qt WebEngine) | `127.0.0.1` only |

**NVIDIA acceleration.** The installers are kept small, so the CUDA libraries (about 1.4 GB) are not inside. When
Echo-AI finds an NVIDIA card with a working driver, it offers to download the official NVIDIA packages from PyPI,
checks them against the published SHA-256 checksum, and switches transcription to the GPU without a restart. Other
graphics cards and Macs transcribe on the CPU (CTranslate2 accelerates only NVIDIA).

**The AI engine.** If Ollama is not installed, the first time you install an AI Echo-AI downloads the official Ollama
package for your system (a pinned version, checked against Ollama's published SHA-256), unpacks it in its own data
folder and runs it on a private port. No administrator rights are needed.

### Transcription models

| Model | Accuracy | Speed | Download | Best for |
|---|:---:|:---:|---:|---|
| Tiny | ●○○○○ | ●●●●● | 75 MB | tests, very old computers |
| Base | ●●○○○ | ●●●●● | 145 MB | clear speech in a quiet room |
| **Small** (included) | ●●●○○ | ●●●●○ | 480 MB | everyday audio on any computer |
| Medium | ●●●●○ | ●●●○○ | 1.5 GB | more accuracy, a bit slower |
| Large v3 Turbo | ●●●●○ | ●●●●○ | 1.6 GB | near-best accuracy, fast on a GPU |
| Large v3 | ●●●●● | ●●○○○ | 3.1 GB | hard audio: noise, accents, many voices |

### AI models

| Model | Quality | Speed | Download | Best for |
|---|:---:|:---:|---:|---|
| Qwen 2.5 1.5B | ●●○○○ | ●●●●● | 1.0 GB | short summaries on simple computers |
| Llama 3.2 3B | ●●●○○ | ●●●●○ | 2.0 GB | quick answers in English |
| Qwen 2.5 3B | ●●●○○ | ●●●●○ | 1.9 GB | 8 GB of RAM; English, Portuguese, Spanish |
| Gemma 3 4B | ●●●●○ | ●●●○○ | 3.3 GB | natural Portuguese and Spanish |
| Qwen 2.5 7B | ●●●●● | ●●○○○ | 4.7 GB | the best summaries; 16 GB of RAM or a 6 GB GPU |
| Llama 3.1 8B | ●●●●○ | ●●○○○ | 4.9 GB | an 8B alternative, strong in English |

Echo-AI only offers conversational models from 1 to 8 billion parameters: small enough for a normal computer and good
at talking about a transcript. Mixture-of-experts, code and embedding models are left out.

## Your data

Everything is stored in one folder on your computer:

| System | Folder |
|---|---|
| Windows | `%LOCALAPPDATA%\Echo-AI` |
| macOS | `~/Library/Application Support/Echo-AI` |
| Linux | `~/.local/share/echo-ai` |

The folder holds the audio files, the SQLite database with transcripts and chats, the log, and (when installed by
the app) the AI engine and the NVIDIA acceleration package. Whisper models go to the Hugging Face cache
(`~/.cache/huggingface`). Deleting a chat in the app deletes its audio file too.

**What uses the internet.** Only the downloads you start: Whisper models (Hugging Face), AI models (the Ollama
library), the AI engine (Ollama's releases on GitHub) and the NVIDIA package (PyPI). Your audio, transcripts and chats
never leave your computer. There are no accounts and no telemetry.

## Update and uninstall

| Installed with | Update | Uninstall |
|---|---|---|
| Windows installer | Run the `.msi` of the [latest release](https://github.com/mowbrazilitteam/Echo-AI-Audio-Transcriber/releases/latest) | **Settings → Apps**, Echo-AI, **Uninstall** |
| macOS `.dmg` | Drag the new version over the old one | Drag Echo-AI from Applications to the Trash |
| Ubuntu `.deb` | `sudo apt install ./echo-ai-*-linux.deb` with the new file | `sudo apt remove echo-ai` |
| Terminal (uv) | `uv tool install --reinstall` with the `.zip` of the new version | `uv tool uninstall echo-ai-audio-transcriber` |

Uninstalling keeps your transcripts and chats. To erase everything, also delete the [data folder](#your-data) and, if
you want, the Whisper models in `~/.cache/huggingface`.

## Troubleshooting

- **The window doesn't open.** Echo-AI then opens in your default browser; you can also open http://127.0.0.1:8765
  while it runs. The reason is in the log (below).
- **It asks to install an AI, but I already use Ollama.** Open Ollama (the app on Windows and macOS, `ollama serve` on
  Linux) and ask again. Echo-AI looks for it at `http://127.0.0.1:11434`; for another address, set `ECHO_OLLAMA_URL`.
- **Transcription is slow.** Without an NVIDIA card it runs on the CPU: choose a smaller model (Base or Small). With an
  NVIDIA card, accept the acceleration package when Echo-AI offers it.
- **"Port already in use".** Another program is using port 8765: set the `ECHO_PORTA` environment variable to another
  port.
- **The log.** `echo.log`, in the [data folder](#your-data), records what the app did and every error with its reason.
  Check what it contains before attaching it to a public issue.

## Run from source

With [uv](https://docs.astral.sh/uv/) (it installs Python 3.12 for you):

```bash
git clone https://github.com/mowbrazilitteam/Echo-AI-Audio-Transcriber.git
cd Echo-AI-Audio-Transcriber
uv sync                 # add --extra gpu on a computer with an NVIDIA card
uv run echo-ai          # the app, in its own window
```

To install it as a command instead, see [Install from the terminal](#install-from-the-terminal-no-admin).

`echo-ai --navegador` opens the interface in your default browser instead of a window. `python -m echo_ai.servidor`
runs only the local server. Settings can be changed with environment variables prefixed `ECHO_` (see `.env.example`).

## Development

```bash
uv sync --extra gpu
uv run ruff check . && uv run ruff format --check .
uv run mypy echo_ai tests
uv run pytest
```

Installers are built with [Briefcase](https://briefcase.readthedocs.io) by GitHub Actions for every tag `v*`
(`.github/workflows/instaladores.yml`). To build one locally: `uv run python scripts/incluir_modelo.py small`, then
`uv run briefcase create`, `build` and `package` for your system. The code, comments and variable names are in
Portuguese; the interface is in English, Portuguese and Spanish (`echo_ai/static/i18n.js`).

## Third-party software and models

Echo-AI uses [faster-whisper](https://github.com/SYSTRAN/faster-whisper) and
[CTranslate2](https://github.com/OpenNMT/CTranslate2) (MIT), OpenAI's [Whisper](https://github.com/openai/whisper)
models (MIT), [Ollama](https://github.com/ollama/ollama) (MIT), [PyAV](https://github.com/PyAV-Org/PyAV) and FFmpeg,
[FastAPI](https://fastapi.tiangolo.com), [pywebview](https://pywebview.flowrl.com) and the
[Ubuntu Sans](https://design.ubuntu.com/font) font (Ubuntu Font Licence 1.0, see `echo_ai/static/fontes`). NVIDIA's
CUDA libraries are downloaded from PyPI under NVIDIA's license. **Each AI model has its own license**, shown by Ollama
when it is downloaded: Qwen (Apache 2.0 or the Qwen license), Llama (Llama Community License) and Gemma (Gemma Terms
of Use).

## Credits

Created by **Victor Medeiros**, Mid-level IT Analyst & AI Full-Stack Engineer.

## License

[MIT](LICENSE). You can use, copy, modify and distribute Echo-AI, including commercially, as long as the copyright
notice stays in every copy.
