# Regulation Rule Operations

Task 15 provides an auditable calculation framework. It does not ship any
production regulation threshold. Values under `tests/` are synthetic fixtures
and must not be copied into a production rule.

## Rule Identity And Scope

A rule version is immutable and identified by `rule_id + rule_version`. It
records:

- exchange, board, and security-status applicability;
- half-open effective dates `[effective_from, effective_to)`;
- window, direction, legal threshold, and independent product warning;
- benchmark, calculation method, anomaly-count mode, and reset condition;
- official HTTPS source, independent review/release record, frozen examples,
  and the explicit `regression_passed` decision.

For `upper`, `warning_threshold < threshold`. For `lower`,
`warning_threshold > threshold`. Boundary behavior is inclusive at the legal
threshold and at the warning edge.

## Production Gate

A rule can be active only when all of these are true:

1. `review_switch` is `approved`.
2. An official absolute HTTPS source is present.
3. The author and reviewer differ, and review precedes release.
4. `regression_passed` is explicitly true.
5. Every frozen example matches the deterministic engine.
6. The latest append-only lifecycle record is active.

Non-approved, inactive, unsupported, inapplicable, missing, or conflicting
inputs produce `pending` with machine-readable reasons. They never produce
`idle`, `approaching_alert`, or `triggered`.

Withdrawal appends a `withdrawn` lifecycle record. It excludes the version from
new calculation snapshots without changing existing rules, calculations,
events, snapshots, or published payloads.

## Formal Events

Formal events are separate disclosed facts with the PRD table 8 fields plus
nullable immutable `source_id` and `source_version` provenance. Legacy events
without provenance remain displayable only under unknown source completeness.
A calculation result never creates, changes, or closes an event.

## Controlled CLI

All commands require `DRAGON_DATABASE_URL` after argument parsing:

```text
dragon-radar regulation-rule-register RULE.json --key KEY
dragon-radar regulation-rule-activate RULE_ID VERSION --key KEY
dragon-radar regulation-rule-withdraw RULE_ID VERSION --reason REASON --key KEY
dragon-radar regulation-event-add EVENT.json --key KEY
dragon-radar regulation-materialize OBSERVATIONS.json \
  --market-snapshot UUID \
  --event-source-completeness complete \
  --event-source-id SOURCE_ID \
  --event-source-version SOURCE_VERSION \
  --key KEY
```

`OBSERVATIONS.json` is an array of `RegulationObservation` objects. Every
observation includes `rule_id` and `rule_version`; values are never reused
between rule versions. Omitted observations are materialized as
`pending/input_missing` for every applicable rule across the market snapshot's
full expected security pool.

Event-source evidence defaults to `unknown`, with no source ID or version.
`complete` is accepted only when both identifier flags are present and valid.
The attestation is operator-sealed snapshot evidence, not a fabricated raw
archive. It is immutable, participates in the materialization command hash,
and is copied into the published payload. A complete snapshot selects only
effective events whose source identity and version exactly match the
attestation; an empty complete source still creates an event-source dependency.
Unknown completeness retains authorized sourced events and legacy events but
never reports a complete total. No calculation state, readiness state, or
listed event count upgrades completeness.

Publication binds the resulting snapshot through
`PublicationCommand.regulation_input_id`; `PublicationService` materializes the
stable completeness, rule, calculation, event, and event-source payload.
Event-source lineage uses the regulation snapshot UUID as archive identity.
Materialization, publication, and reads recheck display authorization, and a
source withdrawal invalidates protected event display.
