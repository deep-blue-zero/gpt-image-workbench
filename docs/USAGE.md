# Workbench usage guide

See the [README](../README.md) for installation and the [testing notes](TESTING.md)
for current validation scope. The application runs locally and requires a separate
OpenAI API account for paid generation.

## API key and offline mode

Use **Connect OpenAI / API-key settings** at the top of the interface. Paste your
key into the password field and click **Save API key**. Remember uses Windows
Credential Manager, scoped to this library; unchecked means this session only.
The field clears after saving. No restart or image request is required.

**Test connection** makes a read-only models request. Success proves the key was
accepted for that endpoint; it does not guarantee image-model access or billing.
Some restricted keys can generate images but cannot list models.

Advanced/source users can still use `OPENAI_API_KEY` or `.env`. On startup the
environment takes priority over `.env`, and either takes priority over a saved
Credential Manager key. A key explicitly entered in the UI overrides those for
the current session. **Disconnect and forget saved key** removes the vault entry
and disconnects this session; an environment/.env key returns after restarting
until removed at its source. Changing keys clears prepared request previews and
is blocked during an active request. Keys are never written into library metadata.

With no key, the entire interface remains available for inspection, prompt
management, cost previews and offline image samples. Requests are blocked before
submission. `Start Demo.cmd` forces this mode even when a key exists. The local
sample is a labeled pixel grid, **not AI-generated artwork**.

Optional `.env` settings are `WORKBENCH_PORT` (default 7865),
`WORKBENCH_DATA_DIR` (source/portable default `data`; installed default
`%LOCALAPPDATA%\GPT Image Workbench\data`), and
`WORKBENCH_TIMEZONE` (default `America/New_York`). Command-line options include
`--demo`, `--no-browser`, `--port 7866`, and `--data-dir PATH`.

## Models and supported controls

Choose exactly one of:

- `gpt-image-2.6-sunburst`
- `gpt-image-2.6-flare`
- `gpt-image-2.5-sunburst`
- `gpt-image-2.5-flare`
- `gpt-image-2`
- `gpt-image-2.5-sunburst-2026-09-08`
- `gpt-image-2.5-flare-2026-09-08`

The dated identifiers pin a model snapshot. Undated aliases can change on the
provider side. **The application never substitutes a model.** Selecting a model
does not establish your account's access to it. Model choices come from the local registry;
the optional connection test does not change that registry.
Generation is nondeterministic, including with a pinned snapshot; there is no
documented seed control for these models.

Both 2.6 and 2.5 models accept `auto`, `low`, `medium`, `high`, `xhigh`, and `max` quality.
GPT Image 2 accepts settings through `high`; choosing a higher setting produces
an actionable validation error. All requested resolution presets are included,
and width/height can be entered manually. Validation requires:

- Width and height divisible by 16.
- Neither edge greater than 3840.
- Long/short edge ratio at most 3.
- Between 655,360 and 8,294,400 total pixels, inclusive.

The app labels resolutions above 3,686,400 pixels (the area of 2560×1440) as
experimental. This is its interpretation of the documentation's experimental
threshold. Requested dimensions and decoded output dimensions are stored
separately; the image viewer always uses the decoded dimensions.

PNG, JPEG, and WebP are supported. Transparent output requires PNG or WebP.
Compression is optional and only valid for JPEG/WebP. Up to ten outputs may be
requested per non-streamed call. Streamed previews support one final image per
request in this application, with zero to three requested partial images.

### GPT Image 2.6

Sunburst and Flare are available in Generate, Edit, Compare, and Gallery filters.
The existing 2.5 startup default and pinned snapshots are preserved. The new
**Complexity / planning effort** control accepts `low`, `medium`, or `high`.
**Model default** omits the field; OpenAI documents `high` as the 2.6 default.
Complexity is separate from quality and is saved with requests, portable PNG
metadata, exports, and reruns. Explicit complexity is rejected for older models.
Compare can vary complexity as an axis, or use appended Generate/Edit settings.

2.6 uses Standard processing only in this workbench: Batch support/rates are
not yet verified. PNG/JPEG/WebP workflows are supported; SVG, animated GIF, and
markup-overlay controls are not implemented here. The published 2.6 calculator
uses quality-dependent token multipliers, so estimates do not reuse 2.5's output
token counts at low and medium quality. Prices and capabilities were checked
against the [image guide](https://developers.openai.com/api/docs/guides/image-generation),
[model catalog](https://developers.openai.com/api/docs/models/gpt-image-2.6-sunburst),
and [pricing](https://developers.openai.com/api/docs/pricing) on November 5, 2026.
Account access and paid 2.6 generation have not been tested; no paid request is
needed to preview settings or estimates.

## Moderation

Generate and Edit have an explicit moderation dropdown. `auto` is the initial
default; `low` reduces filtering strictness but **does not remove content-policy
restrictions**. Compare can vary moderation as another experiment axis.

Settings lets you save either value as the default for new sessions. Saving the
default does not change current forms. A separate button applies it to both
current forms if desired. The selected value is validated for the exact model
and endpoint, sent as `moderation`, retained in request metadata and exports,
and never silently changed. Policy refusals are identified separately from
authentication, credit, network, parameter, or server errors.

Provider refusals retain the provider's message, error type, and optional
`moderation_details` object, including `moderation_stage` and `categories`.
Open **Latest request: provider moderation details** below the status bar, or
select a saved request in **Experiments → Recorded request / configuration**.
The readable display distinguishes absent details from an explicit `unknown`
stage; the complete object remains in request JSON. JSON/CSV exports include
these diagnostics. Credentials are redacted, and provider text is displayed as
escaped text. Older records cannot recover fields that were never retained.
These are optional public diagnostics, not internal classifier scores.

## Generate an illustration

1. Choose the model or snapshot and enter your prompt. Japanese, emoji, and long
   Unicode prompts are supported up to the documented 32,000-character limit.
2. Choose resolution, quality, moderation, batch size, and output controls.
3. Click **Preview request & estimated cost**. Review the table and expand
   **Complete prepared settings** to inspect the entire prompt and request.
4. Check the authorization checkbox, then click **Generate**. Each preview may
   be submitted only once. Changing controls invalidates it.
5. Watch status, elapsed time, and optional partial previews. Final files and
   metadata are saved automatically. Use Gallery for full-size inspection.

Save, duplicate, load, and clear prompts in **Prompt library & history**. Saved
prompts and successful-generation history persist across restarts. Duplicate
creates a separate saved prompt, leaving the previous version intact.

In Generate and Edit/Image to Image, **Width** and **Height** have a slider beneath
an editable number field. Drag or use the arrow keys to adjust in 16-pixel steps,
or type a dimension directly. The preset follows the current dimensions and
shows **Custom** when they do not match a preset. The pixel count and validation
message update as you adjust the controls; normal model limits still apply.

## Edit/Image to Image with references

Upload one to sixteen still PNG/JPEG/WebP files in Edit, or select a Gallery image
and click **Use as source in Edit**. References must each be smaller than 50 MB;
the local decoder also limits inputs to 50 million pixels and rejects animation.
Drop additional images onto the **Source and references** box at any time to
append them, including after it already contains files. Successive drops are
processed in drop order. Drag the file rows to reorder them, remove individual
files with their × button, or clear the list to start again. The first is the
parent image; every reference ID is retained.

Enter an edit instruction, choose the model and controls, preview, and explicitly
confirm. The API receives the actual original image bytes through the edit
endpoint. Source files are copied into local storage without replacing originals.
Edited outputs are new files linked to their parent. Compare references and
outputs in Edit's overviews or Gallery's side-by-side viewer. Generative editing
is not guaranteed deterministic upscaling.

## Recover a prompt and settings from an image

Drag one PNG, JPEG, or WebP file directly onto the **Generate prompt box** or
the **Edit/Image to Image instruction box**. The prompt box highlights during
the drag. You can also expand **Recover prompt & settings from an image** and
choose a file there. This is a local metadata inspection; it sends no OpenAI
request and does not create a generation or permanently import a Gallery image.

The review panel shows the recovered prompt, settings, source, missing fields,
and any unsupported or conflicting values. Choose **Apply recovered prompt &
settings** or **Use recovered prompt only**. Applying values invalidates the
old cost preview; normal preview and confirmation are still required to generate.
Missing fields keep their current form values. Unsupported or conflicting
settings block the full-settings apply button, with prompt-only recovery still
available when the prompt itself is usable. Review the model and moderation
selection explicitly before preparing a new request.

Recovery uses an exact SHA-256 match against this workbench's saved generated
images first, including renamed copies. Otherwise it reads explicit PNG text
fields, JSON generation settings, common generation-parameter text, and explicit
EXIF UserComment/ImageDescription or XMP fields. It does not interpret arbitrary
workflow graphs, reconstruct reference-image collections, or guess prompts from
the artwork. The file's observed dimensions and format are shown separately from
recorded generation parameters.

An OpenAI-generated image does **not** necessarily contain its prompt or settings.
The API PNG outputs and clipboard images inspected in this workspace contained
neither. Saved workbench outputs can still recover their request by exact-byte
matching. A resized, recompressed, or otherwise changed copy may lose that match.
OpenAI's Content Credentials/provenance signals describe origin and do not
provide a way to infer the original prompt. See the
[official provenance guidance](https://developers.openai.com/api/docs/guides/content-provenance).
If neither embedded settings nor a saved matching request exists, the interface
reports that recovery is unavailable.

### Portable images with embedded settings

Every new generated or edited output, including completed official Batch outputs,
also saves a **portable PNG copy** under `data/portable/`. Generate and Edit result
downloads use these copies. In Gallery, select **Download PNG with prompt & settings**;
older saved outputs get a portable copy when selected. Source/demo images without
a saved request cannot gain a generation prompt this way.

On Windows, **Show in Explorer** beneath the Generate and Edit output panes opens
the saved output's folder with that file selected. Click an output to choose it;
without a selection, the button uses the newest result. It reveals the portable
PNG with embedded settings, or the original if a portable copy is unavailable.
The button is disabled until an output is available. It opens the saved file's
location, not a temporary browser upload/download cache.

Portable PNGs include the full prompt, exact selected model, requested width/height,
quality, moderation, output format, background, image count, compression and streaming
controls. They contain a versioned `gpt_image_workbench` JSON text field plus a common
`parameters` text field. UTF-8 preserves Japanese and other Unicode. The versioned
record takes priority when importing, including literal `Negative prompt:` or
`Size:` lines inside the prompt. Drop the portable file into another workbench to
restore its settings without the original database. Reference images are not embedded;
edits still require selecting the references separately. Settings do not make image
generation deterministic.

The unchanged API file remains in `data/images/`, with its original hash and metadata.
For PNG output, the portable copy retains the original compressed pixel data and other
chunks while adding text. JPEG/WebP output gets a PNG copy of its decoded pixels,
avoiding another lossy encode; the original requested format remains in the metadata.
Portable files can be larger. Added metadata changes file bytes and can invalidate
embedded Content Credentials, so use **Download original bytes** for provider provenance
verification. The new settings are explicitly workbench-authored, not provider-signed.

The portable file contains your full prompt and generation settings when shared.
Only allowlisted settings and basic image context are exported; credentials, local
paths, provider headers and full response bodies are excluded.

Third-party support varies. Civitai's published
[`@civitai/generation-metadata` parser](https://github.com/civitai/media-metadata)
recognizes an A1111 `parameters` block only when it contains a diffusion `Steps` field.
GPT Image does not expose that value. The workbench omits steps, sampler, seed and CFG
rather than inventing them. Do not assume Civitai will automatically populate its UI
from these files; workbench recovery uses the complete embedded JSON independently.
A local test with version 0.4.0, both with and without its Civitai plugin, read the
complete embedded text but returned no parsed generation fields. No image was
uploaded to Civitai for this validation.

## Controlled comparisons

Compare builds the Cartesian product of selected models, resolutions, qualities,
prompts, and moderation values. Enter resolutions one per line and separate
prompts with a line containing `---`. Set independent repetitions and outputs
per request. The default matrix output is PNG with automatic background and no
streaming.

For a non-Cartesian study, configure Generate or Edit and click **Append current
Generate configuration** or **Append current Edit configuration** under Compare's
**Selected configurations**. Append each desired
combination, such as 864×1536/high, 1440×2560/high, 1440×2560/max, and
2160×3840/max. These configurations retain the form's output controls, prompts,
and ordered edit references. Independent repetitions also apply to selected edits.

Preview the exact matrix, request count, output count, and cost before confirming.
In Standard mode, calls run sequentially, with a limit of 500 requests per plan
and one active local experiment. Any failed or incomplete response stops further submissions.
**Cancel remaining requests** stops local queued calls and future retries;
an already submitted call continues, can be billed, and is saved when it finishes.

Experiments stores the plan, settings, results, failures and request IDs. Select a
recorded request to inspect its usage/error metadata or preview a new independent
sample with the same settings. A rerun creates a separately named experiment.
Export JSON and CSV from this tab. Exports include file paths and metadata;
they do not embed image bytes. CSV formula-like cells are prefixed for safe
spreadsheet opening, while JSON retains the original prompt text.

## Official Batch processing

1. Select **Official Batch — 50% token rates, up to 24 hours** in the processing
   selector above the tabs. Standard remains the initial mode.
2. Configure Generate, Edit, or a Compare experiment, then preview it. The preview
   shows the discounted total, the corresponding Standard estimate, number of
   remote jobs, and reference uploads. Streaming and partial previews are
   incompatible with Batch; disable them explicitly before previewing.
3. Confirm the displayed cost and submit. The workbench reserves the entire
   plan's estimate before uploading. It splits mixed plans into one remote job
   per exact model identifier and endpoint, preserving configuration and sample IDs.
4. Open **Batch jobs → Refresh saved jobs**, select the job, and use **Check remote
   status**. Once terminal, **Import available results** saves original image
   bytes, thumbnails, reference/parent links, usage, and provider moderation details
   into the normal Gallery, Experiments, and Usage & Costs views. Refresh those
   views after import. Repeat for each job in a mixed experiment.

Submitted jobs run on OpenAI's servers while this app is closed. Saved remote
IDs survive restarts; status checks and imports are manual. Result order need
not match submission order: the app maps each result by its unique `custom_id`.
Repeated imports reuse existing images and request usage, including recovery
after an interrupted import. Import never starts a new image generation.

Both 2.5 aliases and their dated snapshots are enabled. GPT Image 2 is rejected
for Batch until its support is explicitly configured from official evidence.
The workbench retains its 500-request plan limit; it also checks the provider's
200 MB JSONL file limit. Edit references are uploaded once per remote job with
`purpose=vision` and passed as ordered `images: [{file_id: ...}]` entries.
Confirmed requests and their references leave your computer at submission time.

A remote Batch can contain successes, refusals, and other errors. A refusal does
not stop the remaining requests. **Cancel selected Batch** asks OpenAI to cancel
that remote job; cancellation can take up to ten minutes, and completed requests
can still be billed and imported. The cancellation button above the tabs stops
only local work, including jobs not yet submitted. It does not cancel remote jobs.

If submission loses its response, the job remains `submission_unknown`; it is
never automatically replayed. **Check remote status** searches the latest 1,000
jobs by saved ownership metadata. The recovery section also accepts a remote
Batch ID from the OpenAI dashboard. Resume submission is permitted only when
the job is known not to have been created and you confirm its saved estimate.
Changed cost settings require a new preview. The spending guard is checked again
on resume; with the guard enabled, an unsubmitted reservation from an earlier
month must be cancelled locally and prepared again.

Input JSONL and reference uploads are set to expire after 30 days. OpenAI's result
files expire 30 days after completion, so import before then. Imported originals,
diagnostics, and local result files remain in your data directory. No files are
automatically deleted locally. Failed input validation, explicit unexecuted
expiry/cancellation results, and local cancellations have zero local execution
cost. Missing outcomes and refusals without usage retain estimates rather than
being recorded as free.

## Gallery and native pixels

Refresh to include new outputs. Filter by model, local creation date, experiment
ID, actual dimensions, or quality; sort newest, oldest, or largest. The grid
shows up to 500 matching thumbnails; filters narrow larger collections.

Thumbnails show the full image with padding and captions below the artwork.
The browser uses 75% of the window height by default. **Browsing area** offers
Compact, Large, and Tall heights; **Thumbnails per row** adjusts thumbnail size.
Use **Fullscreen gallery** for the largest browsing area; press Esc to return.

Click a thumbnail or select an individual image to open its original. At **100%**,
one source pixel maps to one CSS pixel in a scrollable viewport. Browser zoom and
operating-system display scaling still affect physical display pixels. Fit and
25–400% zoom are provided. Side-by-side comparison uses each original independently.
**Download original bytes** does not re-encode the file. You can also open it in
the default desktop viewer, copy the prompt, or reuse settings in Generate.
**Download PNG with prompt & settings** provides a portable copy for reuse without
the database; both downloads appear beneath the original pixel viewer.

## Usage, prices, and local budgets

The separately updateable pricing registry contains Standard and official Batch
rates, checked on October 31, 2026. Batch text input, image input, and image output
are $2.50, $4.00, and $15.00 per million tokens respectively: half the corresponding
Standard rates. A local queue of Standard calls receives no Batch discount.
Output estimates implement the official
model-specific calculator. Text input uses UTF-8 byte length as a conservative
planning allowance, not an exact tokenizer. Reference-image inputs default to a
user-configurable allowance of 4096 tokens per reference; this is an application
assumption, not an official input-token formula. `auto` quality uses the highest
explicit quality as a planning allowance. Partial previews add 100 planned output
tokens each; actual delivery may include fewer previews.

When the API reports consistent text/image input and image output token counts,
the workbench calculates request cost from those counts and the saved rate
snapshot. Otherwise it retains an estimate. Usage is counted once per request,
not once per returned image. Known rejected/non-submitted calls are assigned zero
local cost; ambiguous outcomes and moderation refusals retain their estimate
when there is insufficient usage evidence. A usage-derived cost is **not a billing
invoice**. Direct Images requests do not receive the Responses API cache discount.

Usage & Costs groups attempts, outputs and costs by day, month, experiment, model,
resolution, quality, moderation, and processing mode. Timestamps are stored in UTC; grouping uses
the configured local timezone when each request is created. The database only
tracks this application's requests, not your entire OpenAI account.

Settings provides a per-plan warning threshold and optional monthly spending
guard. Before every local submission, SQLite atomically reserves the estimated
cost and compares recorded costs plus reservations against the monthly threshold.
Unpriced requests block the guard. Actual token use can exceed an estimate;
already submitted work and requests made elsewhere are outside its control.
This guard is **not an OpenAI account-level spending limit**. Interrupted and
uncertain calls retain reservations; reconcile them against billing before
manually modifying records or increasing your local threshold.

## Reliability and local storage

The SDK's automatic retries are disabled. Bounded exponential backoff with jitter
is used only for explicit rate-limit rejection or a connection failure known to
precede transmission. Credits/authentication/validation/refusal failures are not
blindly retried. Timeouts, broken streams, and server errors can have ambiguous
outcomes and are not automatically replayed. Request IDs, error categories,
status codes, safe metadata and attempts remain available for diagnosis.

Original response bytes are decoded and validated, saved under UUID filenames,
and never re-encoded. Thumbnails are separate derived files. A response's usage
is recorded before image decoding/saving, so a corrupt image does not erase its
cost. If saving fails midway, earlier saved images remain available. Files written
before a database failure can remain as orphans; preserve them when investigating.
The application does not auto-delete originals or replay interrupted work.

SQLite uses transactions, foreign keys, WAL mode, and per-operation connections.
An OS-held instance lock permits one process per data directory. On restart,
unfinished Standard requests become `interrupted` and are not resumed automatically.
Submitted Batch jobs keep their remote IDs and remain available for status checks
and imports. Batch creation interrupted before its response is retained as an
unknown submission requiring recovery, not automatic replay.
Back up the entire `data` folder while the application is stopped.

```text
app/api/          SDK adapter, payload construction, error classification
app/models/       immutable generation settings and capability validation
app/storage/      SQLite repository, exports, secret redaction
app/processing/   original-byte validation, decoding and thumbnails
app/ui/           Gradio tabs, controls, inspection and cost views
app/defaults/     shipped capability and pricing registries
app/config.py     environment, persistent nonsecret settings, JSON logging
app/costs.py      output estimates and usage-derived accounting
app/experiments.py  experiment matrices and independent sample records
app/service.py    sequential jobs, confirmation, cancellation and persistence
app/batch.py      durable remote Batch submission, recovery and result import
config/           optional independently updated registry overrides
tests/            offline tests and deliberately gated live smoke test
data/images/      originals, references and labeled demo samples
data/thumbnails/  separate thumbnails
data/experiments/ JSON and CSV exports
data/portable/    PNG copies with embedded prompts/settings; original pixels preserved
data/batches/     input JSONL and downloaded remote output/error files
data/previews/    temporary streamed image files (retained until manually cleared)
data/gradio-cache/ local Gradio upload/download cache
data/workbench.sqlite3  persistent records
data/workbench.jsonl    structured diagnostic log, excluding prompts and secrets
```

The server binds only to `127.0.0.1`; public sharing, monitoring, Hub telemetry,
Gradio analytics, and remote fonts/themes are disabled. Only original/reference
images and prompts in confirmed requests are sent to OpenAI. Metadata and logs
exclude API credentials; `.env`, local data and sensitive local configuration
are ignored by Git. Logs avoid raw API exception bodies. The local database and
image directory are not encrypted, so use appropriate operating-system access
controls and backups. This is a single-user local tool, not a hosted service.

## Tests and maintenance

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m ruff check app tests
.\.venv\Scripts\python.exe -m ruff format --check app tests
.\.venv\Scripts\python.exe -m pip check
```

Ordinary tests never require a key or a billable API request. The optional live
test requires **both** deliberate switches below and makes one paid low-quality
1024×1024 request, recording it under `.validation/live`. Do not run it unless
you intend to incur API charges:

```powershell
$env:WORKBENCH_RUN_LIVE = 'I_ACCEPT_API_CHARGES'
.\.venv\Scripts\python.exe -m pytest -q -m live --run-live
Remove-Item Env:\WORKBENCH_RUN_LIVE
```

It defaults to `gpt-image-2.5-flare`; `WORKBENCH_LIVE_MODEL` can explicitly select
another documented model. Retries are disabled for this test. It verifies original
API bytes, image decoding, thumbnail, settings, metadata, reported usage, calculated
cost and exports after reopening storage. `WORKBENCH_LIVE_DATA_DIR` can explicitly
select another data directory, such as `data` to retain results in the normal
gallery; stop the app first because the test takes its single-instance lock.
The default operation is generation. To run exactly one edit instead, explicitly
set `WORKBENCH_LIVE_OPERATION=edit` and `WORKBENCH_LIVE_REFERENCE_ID` to an existing
image ID in the selected data directory. The edit asks for a gold rim on the blue
teacup and additionally verifies that the source is unchanged and the output is
linked to it. Clear those overrides when finished. Streaming, multiple-reference
edits, a controlled model/quality matrix, live delivery of optional provider
moderation details, all snapshots and billing accuracy still need separately
authorized validation.

For explicitly authorized larger probes, the same single-request test accepts
`WORKBENCH_LIVE_WIDTH`, `WORKBENCH_LIVE_HEIGHT`, `WORKBENCH_LIVE_QUALITY`,
`WORKBENCH_LIVE_MODERATION`, `WORKBENCH_LIVE_PROMPT_FILE` (UTF-8) and
`WORKBENCH_LIVE_NAME`. Defaults remain 1024×1024, low quality and `auto`
moderation; no larger request is enabled automatically. Clear
overrides before later smoke tests to avoid reusing expensive settings.

To update models or prices, follow [config/README.md](../config/README.md). Never
reuse a different model's output-token formula without official evidence.

## Troubleshooting and limitations

- **Missing key:** use offline mode, or configure `.env` and restart normally.
  `Start Demo.cmd` deliberately ignores a present key.
- **Authentication/access:** verify the key, API project, model access and any
  organization-verification requirement. The application never substitutes models.
- **Credits/rate limit:** check API billing/limits. Rate-limit retries are bounded.
- **Invalid settings:** read the validation message. PNG compression, transparent
  JPEG, unsupported quality, and incompatible dimensions are rejected locally.
- **Policy refusal:** revise the instruction or references. Changing moderation
  does not remove policy restrictions; there is no automatic moderation fallback.
- **Port in use / instance already running:** open the existing browser, stop that
  instance, or use a separate data directory and `--port`. Do not delete a live
  database or lock file to bypass the instance check.
- **Slow request / timeout:** the default read timeout is 600 seconds. Review the
  saved request and account usage before retrying an ambiguous outcome.
- **Storage error:** check disk space, permissions, and the data directory. Preserve
  saved originals and the database while investigating; no auto-resubmission occurs.
- **Batch result missing or import failed:** inspect Batch details and the saved
  provider errors. Retry import after resolving local storage problems. Do not
  regenerate to repair an import. Unresolved outcomes remain visibly estimated.
- **UI looks stale after a code update:** stop, restart, then reload the browser.
- **4K/streaming:** experimental or model/account-dependent behavior must be checked
  live. Current model pages disagree with the detailed guide about streaming;
  this implementation follows the explicit Images API guide and event schemas.
- **Scope:** no mask-painting editor, Responses API conversations, deterministic
  upscaler, seed emulation, background cloud service, or account-wide billing sync.
  The edit interface supports actual multi-image reference editing.

## Official API sources

These are the source references used for the shipped capability and price
registries; this publication does not claim a new provider verification.

Reviewed October 30, 2026:

- [Image generation guide](https://developers.openai.com/api/docs/guides/image-generation)
- [Image prompting guide](https://developers.openai.com/api/docs/guides/image-prompting)
- [Generate endpoint](https://developers.openai.com/api/reference/resources/images/methods/generate)
- [Edit endpoint](https://developers.openai.com/api/reference/resources/images/methods/edit)
- [Pricing](https://developers.openai.com/api/docs/pricing)
- [Sunburst model](https://developers.openai.com/api/docs/models/gpt-image-2.5-sunburst)
- [Flare model](https://developers.openai.com/api/docs/models/gpt-image-2.5-flare)

Batch support and pricing additionally reviewed October 31, 2026:

- [Batch guide](https://developers.openai.com/api/docs/guides/batch)
- [Create Batch](https://developers.openai.com/api/reference/resources/batches/methods/create)
- [File uploads](https://developers.openai.com/api/reference/resources/files/methods/create)

SDK 2.54 accepts arbitrary size strings but its quality annotations predate `xhigh`
and `max`. The adapter sends those documented strings without modifying SDK types.
Its edit signature lacks `moderation`; the documented SDK `extra_body` extension
adds that API field to the multipart request. HTTP-transport tests verify the
serialized contract. No fallback model, fabricated parameter or custom API host
is used. See [API compatibility notes](API_NOTES.md).
