# GPT Image Workbench

[![Offline tests](https://github.com/deep-blue-zero/gpt-image-workbench/actions/workflows/tests.yml/badge.svg)](https://github.com/deep-blue-zero/gpt-image-workbench/actions/workflows/tests.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

A local interface for GPT Image generation, editing, comparison experiments,
portable metadata, and cost tracking. Built with Python, Gradio, the official
OpenAI SDK, Pillow, and SQLite. Windows is the supported desktop platform.

This is an independent project, not an official OpenAI application. API access
and billing are separate from the application. No API key, generated images,
personal prompts, or usage database are included in this repository.

## Features

- **Generate and edit:** prompts, up to 16 ordered reference images, repeated
  drag-and-drop uploads, editable dimensions with 16-pixel sliders, quality,
  moderation, and supported output controls.
- **Compare configurations:** vary model, prompt, resolution, quality, moderation,
  and supported complexity settings across repeated independent samples.
- **Official Batch processing:** submit compatible GPT Image 2.5 generation and
  editing experiments, check remote status, and import results into the same
  gallery and usage records. This is separate from the local Standard queue.
- **Inspect originals:** padded thumbnails, adjustable browsing area, native-pixel
  zoom, side-by-side comparisons, original-byte downloads, and Show in Explorer.
- **Portable settings:** save a PNG copy with embedded prompt/settings and drag it
  back into the prompt box to recover them without the original database.
- **Track costs:** preview estimates before submission, retain reported usage and
  price snapshots, inspect grouped totals, and optionally enable a local budget guard.
- **Inspect refusals:** distinguish moderation refusals from API failures and keep
  the provider's available public diagnostic details.
- **Work offline:** explore all tabs and create a labeled inspection sample without
  an API key. Paid requests require a preview and explicit confirmation.

## Quick start on Windows

Requirements: Windows 10/11, Python 3.12 with the `py` launcher, and a browser.
Installation downloads Python dependencies. Paid image requests additionally
require an OpenAI API key with access to the selected model and available credits.

Clone the repository, or use **Code > Download ZIP** and extract it:

```powershell
git clone https://github.com/deep-blue-zero/gpt-image-workbench.git
cd gpt-image-workbench
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\Install.ps1
```

The installer creates `.venv`, installs pinned dependencies, and creates `.env`
from `.env.example` only if `.env` does not already exist. The execution-policy
option applies only to this invocation.

1. For a free first look, double-click **Start Demo.cmd**.
2. For paid generation, edit the local `.env` file and set `OPENAI_API_KEY`.
3. Double-click **Start Workbench.cmd** and open
   [http://127.0.0.1:7865](http://127.0.0.1:7865).
4. Choose settings, preview the estimated cost, and confirm before generating.

Keep the launcher running while using the app. Before stopping it, let the active
request finish so its output can be saved. Closing the browser does not stop a
request. Restart the app after changing credentials.

The tested dependency set is in [requirements-lock.txt](requirements-lock.txt).
See the [usage guide](docs/USAGE.md) for detailed workflows and runtime options.

## Supported workflows and limits

The shipped model registry includes GPT Image 2.6 Sunburst/Flare, GPT Image 2.5
Sunburst/Flare and their September 2026 snapshots, and GPT Image 2. Account access
is not guaranteed by a model appearing in the interface.

| Capability | Current scope |
| --- | --- |
| Output formats | PNG, JPEG, WebP |
| GPT Image 2.6 | Standard generation/editing and low/medium/high complexity |
| Official Batch | GPT Image 2.5 aliases and pinned snapshots; offline contract tests only |
| Reference editing | Up to 16 still PNG/JPEG/WebP inputs; no mask-painting interface |
| Resolution | Multiples of 16, maximum edge 3840, aspect ratio up to 3:1, 655,360–8,294,400 pixels |
| Moderation | Explicit `auto` or `low`; `auto` is the initial default |
| Streaming | Optional partial previews; one final image per streamed request in this app |

`low` moderation reduces filtering strictness but does not remove content-policy
restrictions. Invalid or incompatible settings receive an error; the application
does not silently substitute a different model or moderation setting.

Edits are generative, not deterministic upscaling. Repeating settings does not
reproduce identical pixels. SVG, animated GIF, markup overlays, GPT Image 2.6
Batch, and Responses API conversations are not implemented here.

The registries record their source dates. Provider capabilities and pricing can
change; see [API notes](docs/API_NOTES.md) and
[updating registries](config/README.md). See [testing and qualification](docs/TESTING.md)
for the distinction between offline coverage and live checks.

## Storage and sharing

The application binds to `127.0.0.1` and is intended for one local user. Publishing
or cloning this repository does not host the application or share an API key.
Each installation supplies its own local configuration. Public Gradio sharing,
analytics, and remote fonts/themes are disabled.

Runtime files live under `data/` by default:

| Path | Contents |
| --- | --- |
| `data/images/` | Unchanged API originals and imported references |
| `data/portable/` | PNG copies with embedded prompt/settings |
| `data/thumbnails/` | Derived thumbnails |
| `data/workbench.sqlite3` | Requests, images, experiments, prompts, and usage |
| `data/batches/` | Batch input and downloaded output/error files |
| `data/experiments/` | JSON/CSV exports |

Back up the whole data directory while the application is stopped. Portable PNGs
contain the full prompt and selected settings when shared; original downloads
retain the provider bytes. Reference collections are not embedded in portable files.

The repository excludes `.env`, runtime data, caches, local registry overrides,
and private development evidence. Cost estimates and the optional local budget
guard are not account-wide billing controls or a provider invoice.

## Development

After running `Install.ps1`:

```powershell
.\.venv\Scripts\python.exe -m pytest -q -m "not live"
.\.venv\Scripts\python.exe -m ruff check app tests
.\.venv\Scripts\python.exe -m ruff format --check app tests
.\.venv\Scripts\python.exe -m pip check
```

CI runs the offline suite on Windows with Python 3.12. It does not receive an API
key or submit paid requests. See [CONTRIBUTING.md](CONTRIBUTING.md) for the project
layout, verification commands, and contribution guidelines.

## License

[MIT](LICENSE). Copyright 2026 deep-blue-zero and contributors.
