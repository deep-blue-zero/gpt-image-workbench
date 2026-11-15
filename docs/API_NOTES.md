# API implementation notes

These notes describe the shipped integration, not a fresh provider qualification.
Model capabilities and GPT Image 2.6 pricing were last reviewed on 2026-11-05;
the GPT Image 2.5 Batch integration was reviewed on 2026-10-31. Source references
and verification dates are retained in `app/defaults/*.json`.

## Requests and settings

- The official OpenAI SDK sends Images generation and editing requests. There is
  no alternate provider host, model fallback, or seed emulation.
- Selected model identifiers, dimensions, quality, moderation, and optional
  complexity are validated and stored without silently substituting alternatives.
- Standard edits use the original reference bytes in multipart requests. The
  installed SDK's public `extra_body` extension carries documented fields absent
  from its explicit method signature, including edit moderation.
- GPT Image 2.6 supports the workbench's PNG/JPEG/WebP workflows and optional
  low/medium/high complexity. SVG, GIF, markup overlays, and 2.6 Batch are not exposed.
- SDK retries are disabled. The application retries only bounded cases known not
  to represent an ambiguous paid submission; uncertain outcomes remain recorded.

## Usage and estimates

Output estimates use model-specific calculator settings in the pricing registry.
The 2.6 low/medium multipliers are applied separately from the 2.5 calculation.
Prompt and reference input allowances are disclosed planning assumptions.

Each request keeps its price snapshot. Returned usage is accounted for once per
request and retained even if later image saving fails. When usage is incomplete,
the estimate remains visibly an estimate. Local totals and guards do not represent
an account-wide spending limit or a provider invoice.

## Official Batch

GPT Image 2.5 aliases and pinned snapshots are enabled for official Batch
generation and editing. Other models are disabled until their Batch capabilities
and rates are explicitly verified. Standard queueing never receives Batch pricing.

Mixed plans split by model and endpoint. JSONL rows retain `custom_id`; ordered
edit references use uploaded file IDs. Results are matched by identity rather
than output line order. Repeated imports do not duplicate image or usage records.
Unknown submission outcomes require recovery rather than automatic resubmission.

Tests exercise the real SDK against mocked HTTP transports. Live Batch completion
has not been qualified. See [testing notes](TESTING.md).

## Moderation and portable files

Both endpoints transmit the selected `auto` or `low` setting. `low` reduces
filtering strictness without removing content-policy restrictions. Provider
message/type and optional public `moderation_details` are saved and displayed;
absent diagnostics do not become invented scores or reasons.

Original API image bytes remain unchanged. Portable PNG copies add allowlisted
application-authored settings. That metadata is not provider-signed provenance
and does not guarantee compatibility with every third-party metadata parser.

## Provider references

- [Image generation guide](https://developers.openai.com/api/docs/guides/image-generation)
- [Image prompting guide](https://developers.openai.com/api/docs/guides/image-prompting)
- [Generation reference](https://developers.openai.com/api/reference/resources/images/methods/generate)
- [Editing reference](https://developers.openai.com/api/reference/resources/images/methods/edit)
- [Pricing](https://developers.openai.com/api/docs/pricing)
- [Batch guide](https://developers.openai.com/api/docs/guides/batch)
- [GPT Image 2.6 Sunburst](https://developers.openai.com/api/docs/models/gpt-image-2.6-sunburst)
- [GPT Image 2.6 Flare](https://developers.openai.com/api/docs/models/gpt-image-2.6-flare)
