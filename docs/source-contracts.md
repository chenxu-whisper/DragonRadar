# Source contracts

## Production gate

DragonRadar currently has no approved real-market provider integration. No
supplier contract, API specification, credential, sample payload, or license
evidence was supplied for this task. Production ingestion is therefore
**blocked** until those materials are reviewed and a provider-specific adapter
is separately approved and tested against licensed historical data.

`market-import` is only a generic adapter for an operator-provided, explicitly
licensed local JSON envelope. It does not collect data automatically and must
not be described as a provider connection.

## Rights

Every import embeds a `SourceGrant`, and the current registry grant is checked
again for imports, retries, attempt reads, and materialization.

- `local_storage`: permission to persist the source archive locally.
- `local_display`: permission to display authorized data in this installation.
- `export`: permission to export data outside the local application.
- `ai`: permission to submit source content to an AI system.
- `sharing`: permission to share source content with other people or systems.

These rights are independent and fail closed. In particular,
`local_display=true` never grants export, AI processing, or public sharing.

## Licensed local JSON

The accepted envelope contains:

- one immutable `MarketBatch`, including its explicit source grant;
- one `MarketPageManifest` with the original request range, page hashes, row
  counts, and cursor chain.

The adapter accepts exact decimal strings or integers for decimal fields.
JSON floating-point values are rejected. Source archives remain canonical and
immutable through `dragon_radar.storage.repository.Repository`.

Invalid business data may be archived only while current source authorization
allows it. It is recorded as a quarantined attempt and is never materialized
into usable read tables. Missing pages, cursor gaps, unsupported units, and
negative size, turnover, count, or amount values all cause quarantine. Missing
optional market size, turnover, first-seal, and last-seal fields remain `null`;
they are never changed to zero.

## Units and missing values

The normalized contract uses currency per share for prices, shares or lots for
source volume, currency for amount and market capitalization, and a fraction
for turnover rate. A different declared unit is quarantined rather than
silently converted.

Net inflow is not derivable from turnover or traded amount. Until an approved
source supplies net inflow with a documented unit and methodology, downstream
read models must return `null` with a reason such as
`net_inflow_source_unavailable`.

## Reset role evidence

The frozen `role-v1` trend benchmark remains the median return of the eligible
declared market pool. It is a historical research rule and is not an index.
Neither its name nor its archived canonical digest may be changed to describe
it as CSI 300 or another market index. `role-test-v1` remains test-only.

Reset-facing roles use an explicit, case-sensitive adapter:
`emotion_leader`, `trend_leader`, `zhongjun`, `genfeng`, `buZhang`, and
`jieli`. The adapter preserves the internal capability, result status, rule
version, and reasons for every result row, including tied first candidates and
later non-candidates. An evaluation without result rows retains one topic-level
row with a null security ID. Simplified and watch-only evidence is not
relabeled as a formal candidate; pending-validation rows become
`waiting_verify` only after explicit rule approval.

Reset production approval is a separate allowlist and defaults to empty.
`production_activatable=true` in a historical rule does not approve that rule
for the reset product. An unapproved rule returns `insufficient` evidence and
no formal rank. New benchmark definitions or candidate thresholds require a
new immutable rule version and explicit product approval.

FAC-R01 requires an explicit benchmark security whose point-in-time identity
is `index`. Security and benchmark bars must match the `MarketBatch`
source ID, source version, adjustment version, calendar positions, and
knowledge cutoff. Missing, duplicated, mismatched, or short windows are
unavailable; the implementation does not substitute the historical market
median or move a window endpoint.

FAC-R01 through FAC-C02 are research observations, not an approved ranking
rule. Each metric records its value, unit, exact window, coverage partition,
reasons, and source/version metadata. Topic amount share additionally requires
a bound `TopicBatch`: its trade date must equal the market trade date; its
declared exchange and embedded calendar exchange must equal the market calendar
exchange; declared and embedded calendar versions and sessions must agree; and
its embedded grant must allow local storage and display for its source ID at
the knowledge cutoff. Any failed binding returns a null value with a specific
reason. Complete same-day member, amount, and currency coverage is also
required. Currency-incompatible members are missing inputs rather than valid
coverage.

FAC-C02 directional amount coverage is evaluated per adjacent return
observation. Position `i` is valid only when adjusted closes at `i-1` and `i`
and amount at `i` are all valid. An incomplete 20-return window keeps its real
valid/missing partition while the metric value remains null. A complete window
with no rising or no falling observations reports `not_applicable` for that
direction without reducing coverage.

Missing turnover or float market capitalization remains unavailable and is
not replaced by traded amount or total market capitalization. No real index
provider or new role threshold has been approved by this contract.
