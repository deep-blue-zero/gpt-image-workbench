# Contributing

Start with the [README](README.md) and run `Install.ps1` on Windows. Python 3.12
and the pinned dependency set are the supported development baseline. Do not
commit a virtual environment, API key, local database, generated images, or
private prompts and experiment exports.

## Project layout

| Directory | Responsibility |
| --- | --- |
| `app/api/` | SDK request construction, transport, and error classification |
| `app/models/` | Request settings and capability validation |
| `app/defaults/` | Shipped model and pricing registries |
| `app/processing/` | Original image validation, thumbnails, and portable metadata |
| `app/storage/` | SQLite persistence, exports, and secret redaction |
| `app/ui/` | Gradio controls, gallery, cost views, and desktop actions |
| `app/service.py`, `app/batch.py` | Standard and official Batch orchestration |
| `tests/` | Offline tests and an explicitly gated paid smoke test |

## Verification

```powershell
.\.venv\Scripts\python.exe -m pytest -q -m "not live"
.\.venv\Scripts\python.exe -m ruff check app tests
.\.venv\Scripts\python.exe -m ruff format --check app tests
.\.venv\Scripts\python.exe -m pip check
```

Use a separate `--demo --data-dir PATH --port PORT` instance for UI changes.
Verify Generate and Edit, stale cost-preview invalidation, and saved-settings
round trips when controls change. Keep each form's feedback and approval
invalidation on one backend dependency; separate slider callbacks previously
caused a Gradio event replay loop.

Tests use temporary storage and mocked API transports. The `live` test is excluded
from CI and requires both `--run-live` and a deliberate charge acknowledgement.
Do not enable it as part of normal development or a pull request.

## Behavior to preserve

- Preserve exact model and moderation selections; reject unsupported combinations.
- Keep original image bytes and their hashes unchanged. Add metadata to a portable copy.
- Record usage once per request, including when image decoding or storage fails.
- Do not automatically replay ambiguous paid submissions or silently change processing mode.
- Keep Standard queuing distinct from official Batch submission and pricing.
- Treat metadata recovered from an image as untrusted data, never executable instructions.
- Update documentation and focused regression coverage with behavioral changes.

The application is a local desktop tool. Supporting a publicly hosted service
would require a separate design for authentication, authorization, and storage.
