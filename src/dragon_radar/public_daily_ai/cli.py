"""Local operator licensing and promoted-summary selection validation."""

import argparse
import json
from pathlib import Path


def load_document(raw):
    def pairs(values):
        result = {}
        for key, value in values:
            if key in result:
                raise ValueError("Duplicate document key")
            result[key] = value
        return result

    return json.loads(
        raw,
        object_pairs_hook=pairs,
        parse_constant=lambda value: (_ for _ in ()).throw(
            ValueError("Nonfinite JSON")
        ),
    )


def validate_selections(values):
    from dragon_radar.news.summary_publication import (
        SummarySelection,
        selection_identity,
    )

    if not isinstance(values, list) or len(values) > 10000:
        raise ValueError("Invalid promoted-summary selection document")
    selections = [
        SummarySelection.model_validate(value).model_dump(mode="json")
        for value in values
    ]
    selections.sort(
        key=lambda item: (
            item["article_id"],
            item["article_revision_id"],
            item["summary_id"],
        )
    )
    return {
        "summary_selections": selections,
        "selection_sha256": selection_identity(selections),
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description="Local AI output licensing registry")
    commands = parser.add_subparsers(dest="command", required=True)
    register = commands.add_parser("register")
    register.add_argument("--document", type=Path, required=True)
    revoke = commands.add_parser("revoke")
    revoke.add_argument("--reference", required=True)
    revoke.add_argument("--idempotency-key", required=True)
    revoke.add_argument("--reason-reference", required=True)
    selections = commands.add_parser("validate-summary-selections")
    selections.add_argument("--document", type=Path, required=True)
    args = parser.parse_args(argv)
    database = None
    try:
        if args.command == "validate-summary-selections":
            result = validate_selections(
                load_document(args.document.read_text(encoding="utf-8"))
            )
        else:
            from dragon_radar.public_daily_ai.entitlements import (
                OutputEntitlementRepository,
            )
            from dragon_radar.public_daily_ai.models import AIOutputEntitlement
            from dragon_radar.storage.database import Database

            # Validate external input before constructing/opening a database.
            document = (
                AIOutputEntitlement.model_validate(
                    load_document(
                        args.document.read_text(encoding="utf-8"),
                    )
                )
                if args.command == "register"
                else None
            )
            database = Database()
            repository = OutputEntitlementRepository(database)
            if document is not None:
                reference = repository.register(document)
                result = {
                    "reference": reference,
                    "status": "registered",
                    "production_approved": False,
                }
            else:
                repository.revoke(
                    args.reference,
                    key=args.idempotency_key,
                    reason_reference=args.reason_reference,
                )
                result = {"reference": args.reference, "status": "revoked"}
        print(json.dumps(result, sort_keys=True, ensure_ascii=True))
        return 0
    except Exception:
        print('{"error":"AI_REGISTRY_COMMAND_FAILED","production_approved":false}')
        return 2
    finally:
        if database is not None:
            database.close()


if __name__ == "__main__":
    raise SystemExit(main())
