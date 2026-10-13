"""Opt-in real local classification evaluation; no mail provider is constructed."""

import argparse
import json
from pathlib import Path

from inboxed.bootstrap import build_classifier
from inboxed.config import load_settings
from inboxed.domain.entities import EmailMessage, MessageRef


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=Path("config.example.toml"))
    parser.add_argument("--dataset", type=Path, default=Path("tests/fixtures/labeled_emails.json"))
    args = parser.parse_args()
    settings = load_settings(args.config)
    classifier = build_classifier(settings)
    rows = json.loads(args.dataset.read_text())
    predictions = []
    for index, row in enumerate(rows):
        message = EmailMessage(
            MessageRef("fixture", "evaluation", str(index)),
            row["sender"],
            row["subject"],
            row["body"],
        )
        result = classifier.classify(message, settings.definitions())
        predictions.append(
            {
                "expected": row["class_id"],
                "predicted": result.class_id,
                "language": row["language"],
                "confidence": result.confidence,
                "truncated": result.truncated,
                "model": result.model,
            }
        )
    correct = sum(p["expected"] == p["predicted"] for p in predictions)
    print(
        json.dumps(
            {
                "count": len(rows),
                "accuracy": correct / len(rows) if rows else None,
                "predictions": predictions,
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
