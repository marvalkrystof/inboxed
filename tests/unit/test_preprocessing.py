import unittest
from dataclasses import replace

from inboxed.models.preprocessing import clean_body, prepare_email_input
from tests.helpers import email, settings


class PreprocessingTests(unittest.TestCase):
    def test_noise_removed_but_czech_and_category_clues_preserved(self):
        text = (
            "Vy\u0301platni\u0301 pa\u0301ska\u200b\r\n\r\n\r\n"
            "  Dokument\t v příloze. \nDokument v příloze.\n-----\n"
            "https://example.com/payroll?token=" + "x" * 2000 + "#tracking\n"
            "Odhlásit odběr newsletteru\n> Previous correspondence"
        )
        cleaned = clean_body(text)
        self.assertEqual(
            cleaned,
            "Výplatní páska\n\nDokument v příloze.\n"
            "https://example.com/payroll\nOdhlásit odběr newsletteru\n> Previous correspondence",
        )

    def test_metadata_precedes_body_and_original_message_is_preserved(self):
        message = replace(
            email(body=" Text   with spaces \n\n\n"),
            attachment_names=("Výplatní páska.pdf", "Výplatní páska.pdf"),
        )
        prepared = prepare_email_input(message)
        self.assertEqual(prepared.state["body"], "Text with spaces")
        self.assertEqual(prepared.state["attachment_names"], "Výplatní páska.pdf")
        keys = list(prepared.state)
        self.assertLess(keys.index("attachment_names"), keys.index("body"))
        self.assertFalse(prepared.shortened)
        self.assertEqual(message.body, " Text   with spaces \n\n\n")

    def test_cleaning_noise_does_not_hide_loss_of_substantive_text(self):
        self.assertFalse(prepare_email_input(email(body=" " * 60000 + "Hello")).shortened)
        self.assertTrue(prepare_email_input(email(body="Real text. " * 6000)).shortened)
        self.assertEqual(clean_body("data:image/png;base64,QUJDRA=="), "[embedded image]")

    def test_classifier_uses_cleaned_input_and_tracks_truncation(self):
        from inboxed.models.ollama.kev_classifier import KevClassifier

        class Client:
            def systemone(self, model, state, questions):
                self.state = state
                return {
                    "answers": {
                        "email_class": {
                            "choice": "other",
                            "confidence": 0.9,
                            "probabilities": {"other": 0.9},
                        }
                    }
                }

        client = Client()
        classifier = KevClassifier("http://localhost:11434", "kev:test", client=client)
        result_clean = classifier.classify(
            email(body="Email\nhttps://example.com/?tracking=" + "x" * 60000),
            settings().definitions(),
        )
        self.assertEqual(client.state["body"], "Email\nhttps://example.com/")
        self.assertFalse(result_clean.truncated)

        result_trunc = classifier.classify(
            email(body="Real text. " * 6000),
            settings().definitions(),
        )
        self.assertTrue(result_trunc.truncated)
