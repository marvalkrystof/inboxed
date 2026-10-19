import base64
import tempfile
import unittest
from pathlib import Path

from pydantic import ValidationError

from inboxed.config import Settings, load_settings, write_default_config
from inboxed.domain.errors import ModelError
from tests.helpers import email, settings


class AdapterTests(unittest.TestCase):
    def test_config_classes(self):
        example = Path("config.example.toml")
        config = load_settings(example)
        self.assertIn("payslips", {c.id for c in config.classes})
        self.assertIn("mfa_codes", {c.id for c in config.classes})
        self.assertFalse(next(c for c in config.classes if c.id == "mfa_codes").allow_automation)
        raw = settings().model_dump()
        raw["classes"].append(raw["classes"][0])
        with self.assertRaises(ValidationError):
            Settings.model_validate(raw)

    def test_init_uses_canonical_template_and_does_not_overwrite(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "config.toml"
            write_default_config(target)
            self.assertEqual(target.read_text(), Path("config.example.toml").read_text())
            self.assertIn("payslips", {c.id for c in load_settings(target).classes})
            target.write_text("user settings")
            with self.assertRaises(Exception):
                write_default_config(target)
            self.assertEqual(target.read_text(), "user settings")

    def test_czech_imap_transfer_decoding_and_charsets(self):
        from email.message import EmailMessage
        from inboxed.providers.imap.parsing import parse_email

        text = "Výplatní páska: příloha obsahuje výplatní lístek a mzdu."
        for charset in ("utf-8", "iso-8859-2", "windows-1250"):
            for transfer in ("base64", "quoted-printable"):
                with self.subTest(charset=charset, transfer=transfer):
                    email_message = EmailMessage()
                    email_message["Subject"] = "Výplatní páska"
                    email_message.set_content(text, charset=charset, cte=transfer)
                    message = parse_email(
                        email_message.as_bytes(), "account", "1", "1", (), "INBOX", "INBOX", None
                    )
                    self.assertEqual(message.subject, "Výplatní páska")
                    self.assertEqual(message.body, text)

    def test_imap_recovers_mislabeled_utf8(self):
        from inboxed.providers.imap.parsing import parse_email

        text = "Výplatní páska"
        raw = (
            b'MIME-Version: 1.0\r\nContent-Type: text/plain; charset="iso-8859-2"\r\n'
            b"Content-Transfer-Encoding: base64\r\n\r\n"
            + base64.b64encode(text.encode("utf-8"))
            + b"\r\n"
        )
        message = parse_email(raw, "account", "1", "1", (), "INBOX", "INBOX", None)
        self.assertEqual(message.body, text)

    def test_kev_rejects_unknown_class_nan_and_invalid_probabilities(self):
        from inboxed.models.ollama.kev_classifier import KevClassifier

        for answer in [
            {"choice": "unknown", "confidence": 0.9, "probabilities": {"unknown": 0.9}},
            {"choice": "other", "confidence": float("nan"), "probabilities": {"other": 0.9}},
            {"choice": "other", "confidence": 0.9, "probabilities": {"invalid": 0.9}},
        ]:

            class Client:
                def __init__(self, ans):
                    self.ans = ans

                def systemone(self, model, state, questions):
                    return {"answers": {"email_class": self.ans}}

            classifier = KevClassifier("http://localhost:11434", "kev:test", client=Client(answer))
            with self.assertRaises(ModelError):
                classifier.classify(email(), settings().definitions())

    def test_kev_classifier_injects_exemplars_into_state(self):
        from inboxed.models.ollama.kev_classifier import KevClassifier

        class Client:
            def systemone(self, model, state, questions):
                self.model = model
                self.state = state
                self.questions = questions
                return {
                    "answers": {
                        "email_class": {
                            "choice": "promotions",
                            "confidence": 0.95,
                            "probabilities": {"promotions": 0.95, "other": 0.05},
                        }
                    }
                }

        client = Client()
        classifier = KevClassifier("http://localhost:11434", "kev:9b-nvfp4", client=client)
        exemplars = {
            "promotions": [{"sender": "shop@example.com", "subject": "Sale"}],
            "unknown": [{"sender": "x", "subject": "y"}],
        }
        result = classifier.classify(email(), settings().definitions(), exemplars=exemplars)
        self.assertEqual(result.class_id, "promotions")
        self.assertEqual(result.confidence, 0.95)
        self.assertEqual(result.model, "kev:kev:9b-nvfp4")
        self.assertIn("approved_examples", client.state)
        # "unknown" should be filtered out because it is not in allowed classes
        self.assertEqual(
            client.state["approved_examples"],
            {"promotions": [{"sender": "shop@example.com", "subject": "Sale"}]},
        )

    def test_kev_classifier_without_exemplars(self):
        from inboxed.models.ollama.kev_classifier import KevClassifier

        class Client:
            def systemone(self, model, state, questions):
                self.state = state
                return {
                    "answers": {
                        "email_class": {
                            "choice": "other",
                            "confidence": 0.9,
                            "probabilities": {"promotions": 0.1, "other": 0.9},
                        }
                    }
                }

        client = Client()
        classifier = KevClassifier("http://localhost:11434", "kev:9b-nvfp4", client=client)
        result = classifier.classify(email(), settings().definitions())
        self.assertEqual(result.class_id, "other")
        self.assertNotIn("approved_examples", client.state)

    def test_run_skip_processed_serialization_and_defaults(self):
        from inboxed.domain.entities import Run

        # Test default values
        r = Run("imap", "user@example.com", {})
        self.assertFalse(r.skip_processed)
        self.assertEqual(r.skipped_count, 0)
        d = r.to_dict()
        self.assertEqual(d["skip_processed"], False)
        self.assertEqual(d["skipped_count"], 0)

        # Test with skipping used
        r2 = Run("imap", "user@gmail.com", {}, skip_processed=True, skipped_count=12)
        self.assertTrue(r2.skip_processed)
        self.assertEqual(r2.skipped_count, 12)
        d2 = r2.to_dict()
        self.assertEqual(d2["skip_processed"], True)
        self.assertEqual(d2["skipped_count"], 12)

        # Test from_dict roundtrip
        loaded = Run.from_dict(d2)
        self.assertTrue(loaded.skip_processed)
        self.assertEqual(loaded.skipped_count, 12)

        # Test backward compatibility when keys are absent in payload
        legacy_dict = {
            "provider": "imap",
            "account": "user@gmail.com",
            "config": {},
            "messages": [],
            "id": "123",
            "created_at": "2026-01-01T00:00:00",
            "status": "ready",
            "account_alias": "personal",
            "errors": [],
        }
        loaded_legacy = Run.from_dict(legacy_dict)
        self.assertFalse(loaded_legacy.skip_processed)
        self.assertEqual(loaded_legacy.skipped_count, 0)
