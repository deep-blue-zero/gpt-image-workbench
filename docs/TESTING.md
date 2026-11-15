# Testing and qualification

## Windows desktop release checks — 2026-11-15

- 214 offline tests passed, with the paid live test deselected.
- Lint, formatting, and dependency checks passed.
- Both the frozen portable executable and installed executable launched with
  Python absent from the child process PATH, using the bundled interpreter.
- The package probes exercised Tk, Windows Credential Manager with disposable
  fake credentials, the HTTP UI, shipped model registries, and local image storage.
- Reinstall preserved original sample image bytes and saved settings; uninstall
  preserved the library. The test installer uses an isolated application registration.
- Browser testing verified session-only API-key entry, field clearing, and
  disconnect without restarting. No live OpenAI request was submitted.

The release workflow repeats the offline tests and packaged checks on a fresh
Windows runner before uploading the installer and ZIP. The startup probe also
fetches the packaged frontend JavaScript and CSS. These checks do not establish
provider model access or billing behavior. Packages are initially unsigned.

See [Windows distribution](WINDOWS_DISTRIBUTION.md) for building and publishing.

## Initial public release checks — 2026-11-14

The full offline suite passed on Windows with Python 3.12:

- 197 tests passed; the one paid live test was explicitly deselected.
- Ruff lint and formatting checks passed.
- Installed dependencies passed `pip check`.

Run the same checks after installation:

```powershell
.\.venv\Scripts\python.exe -m pytest -q -m "not live"
.\.venv\Scripts\python.exe -m ruff check app tests
.\.venv\Scripts\python.exe -m ruff format --check app tests
.\.venv\Scripts\python.exe -m pip check
```

GitHub Actions runs these checks on Windows with Python 3.12 and builds a wheel.
The workflow has no API credentials and excludes all tests marked `live`.

## What offline coverage establishes

Tests cover capability validation; serialized generation/edit payloads through
the real SDK with mocked HTTP transports; moderation settings and public error
details; streamed response parsing; comparison matrices; original-byte and
portable-image persistence; usage accounting; budget reservations; Batch
submission/recovery/import; and UI callback wiring and approval invalidation.

The GPT Image 2.6 tests cover optional complexity, model-specific output estimates,
metadata and UI round trips, and the application's explicit Batch restriction.
Slider regression tests guard against the repeated-callback loop fixed before
the initial public release.

Offline tests establish local behavior and request serialization. They do not
prove provider access, live streaming delivery, runtime moderation decisions,
remote Batch completion, or agreement with a provider invoice.

## Historical live checks and limits

During private development, separately authorized GPT Image 2.5 requests verified
generation and reference editing, original file preservation, metadata, usage
records, a maximum-resolution edit, and persistence of provider refusals. Those
checks apply to their specific requests rather than every exposed combination.

Private source images, prompts, request identifiers, account usage, screenshots,
and full development receipts are intentionally not distributed in this repository.
No new paid request was submitted to prepare this public release.

Official Batch remains implemented and offline-tested without a qualified live
Batch run. Broader model/snapshot coverage, 2.6 paid smoke-test qualification,
multi-reference editing combinations, streaming, and billing reconciliation
remain outside the release's claimed verification.

The optional live smoke test requires both `--run-live` and
`WORKBENCH_RUN_LIVE=I_ACCEPT_API_CHARGES`. See the [usage guide](USAGE.md#tests-and-maintenance)
for its deliberate opt-in and parameters. Do not use it for ordinary CI.
