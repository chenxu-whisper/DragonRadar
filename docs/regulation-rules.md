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

Formal events are separate disclosed facts with exactly the PRD table 8 fields:
event and rule identity, security, event type, announcement identity/URL, and
effective dates. A calculation result never creates, changes, or closes an
event. Event-type display mapping belongs to Task 16.

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
The attestation is immutable, participates in the materialization command hash,
and is copied into the published payload. Listed formal events remain visible
when completeness is unknown, but a complete total is reported only for an
explicitly complete source. No calculation state, readiness state, or listed
event count upgrades completeness.

Publication binds the resulting snapshot through
`PublicationCommand.regulation_input_id`; `PublicationService` materializes the
stable completeness, rule, calculation, event, and event-source payload.
