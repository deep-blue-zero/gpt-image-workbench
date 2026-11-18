# Changelog

## 0.2.2 — 2026-11-17

- Add **Settings → Restart Workbench** to source, installed, and portable launches.
  The current tab reconnects to the same library and port after a fresh server starts.
  Active local queues and Batch operations must finish first.

## 0.2.1 — 2026-11-17

- Fix WebP references rejected by Edit/Image to Image on Windows with
  `unsupported_file_mimetype`. Standard edits and Batch reference uploads now
  send the detected PNG, JPEG, or WebP type explicitly, preserving the original
  bytes and reference order. Existing library images work after updating.

## 0.2.0 — 2026-11-15

- Add a bundled Windows x64 installer and portable ZIP, with a graphical launcher.
- Add API-key setup, session-only credentials, Windows Credential Manager storage,
  and an optional read-only connection test without restarting.
- Keep installed libraries outside program files; add existing-library selection,
  duplicate-instance reopening, port selection, and graceful request-saving shutdown.
- Add automated Windows package builds, installer/portable smoke tests, upgrade
  and uninstall preservation checks, checksums, and GitHub Release publication.
- Initial binary distributions are unsigned; signing and other OS packages are deferred.

## 0.1.0 — 2026-11-14

Initial public source release.

- Local Generate, Edit/Image to Image, Compare, Batch jobs, Gallery, Experiments,
  Usage & Costs, and Settings workflows.
- GPT Image 2 / 2.5 / 2.6 raster controls, explicit moderation, and supported 2.6 complexity.
- Ordered reference uploads and dimension sliders in 16-pixel steps.
- Official Batch support for compatible 2.5 configurations, with durable result imports.
- Original-byte storage, portable PNG metadata, native-pixel inspection, and Windows Explorer integration.
- Request estimates, reported usage, local budget controls, and provider refusal diagnostics.
- Windows offline CI, portable setup documentation, and the MIT license.

See [testing notes](docs/TESTING.md) for qualification limits. This release does
not add SVG/GIF, masks, markup overlays, or GPT Image 2.6 Batch support.
