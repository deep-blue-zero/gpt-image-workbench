# Updating supported models and prices

The shipped registries live in `app/defaults`. To override either one, copy it
here as `capabilities.json` or `pricing.json` and restart the application. Both
files are JSON and independent of the GUI. Never put credentials here.

Only add documented model identifiers. Keep endpoint-specific moderation,
streaming, quality and size constraints accurate. Each request stores its pricing
snapshot, so updating a rate will not rewrite historical costs. There is no seed
control: none is documented for the supported models. The reserved
`seed_parameter` registry entry is null and no seed is sent.

The official output calculator formula uses the configured long-edge grid,
rounds the short-edge grid with ties to even, multiplies the two, and scales by
`(2,000,000 + width × height) / 4,000,000`, rounding up. This is an output-token
estimate, not a guaranteed price. Input token allowances are disclosed in every
prepared request and can be adjusted for references in Settings.

For GPT Image 2.6, apply `output_token_multipliers` after rounding the base token
count up: low ×4.2, medium ×3.3, other explicit qualities ×1. Round that result
to the nearest integer, with positive half-ties up (JavaScript `Math.round`).
These factors come from the official calculator asset recorded in the pricing
registry, not from an assumed relationship between model generations.
The 2.6 family has Standard rates but no verified Batch rates or capabilities.
`complexities` allows explicit planning effort only on documented models;
an absent/empty list permits only omission of the request field.

Official Batch support is explicit and independent of synchronous endpoint
support. A capability entry's `batch_endpoints` lists `generate` and/or `edit`;
an absent or empty list disables Batch for that model. The shipped registry
enables both operations for GPT Image 2.5 aliases and snapshots. Never enable a
model by assuming that all Images models support Batch.

`rates_per_million` contains Standard token prices. `batch_rates_per_million`
contains official Batch prices, currently 2.50/4.00/15.00 USD per million
text-input/image-input/image-output tokens for the 2.5 family. The estimator
selects by processing mode; it does not halve Standard prices automatically.
Old custom registries without Batch capabilities or rates will produce a clear
local validation error instead of silently changing the mode. Saved requests
retain their original price snapshot, including when Batch results are imported
after prices change. Unsubmitted saved jobs require a new preview if their
estimate or pricing version changes.
