import io
import json
import os
import unittest
from unittest.mock import patch
from urllib.error import HTTPError

import ai
from ai import AIUnavailable, classify_question, classify_questions, format_questions, summarise_class_results, summarise_performance


def fake_response(result):
    body = {"candidates": [{"content": {"parts": [{"text": json.dumps(result)}]}}]}
    return io.BytesIO(json.dumps(body).encode("utf-8"))


def fake_groq_response(result):
    body = {"choices": [{"message": {"content": json.dumps(result)}}]}
    return io.BytesIO(json.dumps(body).encode("utf-8"))


class AIHelperTests(unittest.TestCase):
    def setUp(self):
        with ai._request_lock:
            ai._requests.clear()

    def test_no_key_does_not_call_external_service(self):
        with patch.dict(os.environ, {"GEMINI_API_KEY": "", "GROQ_API_KEY": ""}):
            with self.assertRaises(AIUnavailable):
                classify_question("What is a variable?")

    def test_classification_checks_structured_response(self):
        with patch.dict(os.environ, {"GEMINI_API_KEY": "demo-test-key"}):
            with patch("ai.request.urlopen", return_value=fake_response({"topics": [{"topic": "Computing", "subtopic": "Variables"}]})):
                self.assertEqual(classify_question("What is a variable?"), ("Computing", "Variables"))

    def test_batch_classification_uses_one_request(self):
        labels = [{"topic": "Maths", "subtopic": "Addition"}, {"topic": "Science", "subtopic": "Biology"}]
        with patch.dict(os.environ, {"GEMINI_API_KEY": "demo-test-key"}):
            with patch("ai.request.urlopen", return_value=fake_response({"topics": labels})) as call:
                self.assertEqual(classify_questions(["What is 2 + 2?", "What is a cell?"]), [("Maths", "Addition"), ("Science", "Biology")])
                call.assert_called_once()
                payload = json.loads(call.call_args.args[0].data)
                self.assertEqual(payload["generationConfig"]["responseFormat"]["text"]["mimeType"], "APPLICATION_JSON")

    def test_service_unavailable_tries_one_free_tier_backup(self):
        unavailable = HTTPError("https://example.invalid", 503, "Service unavailable", {}, io.BytesIO(b"{}"))
        labels = {"topics": [{"topic": "Maths", "subtopic": "Addition"}]}
        with patch.dict(os.environ, {"GEMINI_API_KEY": "demo-test-key"}):
            with patch("ai.request.urlopen", side_effect=[unavailable, fake_response(labels)]) as call:
                self.assertEqual(classify_question("What is 2 + 2?"), ("Maths", "Addition"))
                self.assertEqual(call.call_count, 2)
                self.assertIn("gemini-3.5-flash-lite", call.call_args_list[0].args[0].full_url)
                self.assertIn("gemini-3.1-flash-lite", call.call_args_list[1].args[0].full_url)

    def test_request_limit_stops_bursts(self):
        with patch("ai.time.monotonic", return_value=100):
            for _ in range(ai.MAX_REQUESTS_PER_MINUTE):
                ai._reserve_request()
            with self.assertRaises(AIUnavailable):
                ai._reserve_request()

    def test_groq_is_third_provider_with_strict_schema(self):
        unavailable = HTTPError("https://example.invalid", 503, "Service unavailable", {}, io.BytesIO(b"{}"))
        labels = {"topics": [{"topic": "Maths", "subtopic": "Addition"}]}
        with patch.dict(os.environ, {"GEMINI_API_KEY": "demo-test-key", "GROQ_API_KEY": "groq-test-key"}):
            with patch("ai.request.urlopen", side_effect=[unavailable, unavailable, fake_groq_response(labels)]) as call:
                self.assertEqual(classify_question("What is 2 + 2?"), ("Maths", "Addition"))
        self.assertEqual(call.call_count, 3)
        groq_call = call.call_args_list[2]
        self.assertEqual(groq_call.args[0].full_url, "https://api.groq.com/openai/v1/chat/completions")
        self.assertEqual(groq_call.kwargs["timeout"], ai.SHORT_TIMEOUTS[2])
        body = json.loads(groq_call.args[0].data)
        self.assertTrue(body["response_format"]["json_schema"]["strict"])
        self.assertFalse(body["response_format"]["json_schema"]["schema"]["additionalProperties"])
        self.assertEqual(groq_call.args[0].get_header("User-agent"), "CBT-Exam-System/1.0")

    def test_groq_only_configuration(self):
        labels = {"topics": [{"topic": "Maths", "subtopic": "Addition"}]}
        with patch.dict(os.environ, {"GEMINI_API_KEY": "", "GROQ_API_KEY": "groq-test-key"}):
            with patch("ai.request.urlopen", return_value=fake_groq_response(labels)) as call:
                self.assertEqual(classify_question("What is 2 + 2?"), ("Maths", "Addition"))
                call.assert_called_once()

    def test_invalid_gemini_response_tries_next_provider(self):
        bad = {"topics": [{"topic": "", "subtopic": ""}]}
        good = {"topics": [{"topic": "Maths", "subtopic": "Addition"}]}
        with patch.dict(os.environ, {"GEMINI_API_KEY": "demo-test-key", "GROQ_API_KEY": "groq-test-key"}):
            with patch("ai.request.urlopen", side_effect=[fake_response(bad), fake_response(bad), fake_groq_response(good)]) as call:
                self.assertEqual(classify_question("What is 2 + 2?"), ("Maths", "Addition"))
                self.assertEqual(call.call_count, 3)

    def test_formatted_questions_are_reviewable(self):
        item = {"question": "What is 2 + 2?", "a": "3", "b": "4", "c": "5", "d": "6", "answer": "B"}
        with patch.dict(os.environ, {"GEMINI_API_KEY": "demo-test-key"}):
            with patch("ai.request.urlopen", return_value=fake_response({"questions": [item]})):
                text = format_questions("What is 2 + 2? A. 3 B. 4 C. 5 D. 6 Answer: B")
        self.assertIn("A. 3\nB. 4", text)
        self.assertTrue(text.endswith("ANSWER: B"))

    def test_invalid_answer_label_is_rejected(self):
        item = {"question": "Example?", "a": "One", "b": "Two", "c": "Three", "d": "Four", "answer": "E"}
        with patch.dict(os.environ, {"GEMINI_API_KEY": "demo-test-key", "GROQ_API_KEY": ""}):
            with patch("ai.request.urlopen", return_value=fake_response({"questions": [item]})):
                with self.assertRaises(AIUnavailable):
                    format_questions("Example? A. One B. Two C. Three D. Four Answer: B")

    def test_ready_format_skips_network(self):
        ready = "What is 2 + 2?\nA. 3\nB. 4\nC. 5\nD. 6\nANSWER: B"
        with patch("ai.request.urlopen") as call:
            self.assertEqual(format_questions(ready), ready)
            call.assert_not_called()

    def test_missing_explicit_answer_is_not_guessed(self):
        with patch("ai.request.urlopen") as call:
            with self.assertRaises(ValueError):
                format_questions("What is 2 + 2? A. 3 B. 4 C. 5 D. 6")
            call.assert_not_called()

    def test_model_cannot_change_explicit_answer(self):
        item = {"question": "What is 2 + 2?", "a": "3", "b": "4", "c": "5", "d": "6", "answer": "A"}
        with patch.dict(os.environ, {"GEMINI_API_KEY": "demo-test-key", "GROQ_API_KEY": ""}):
            with patch("ai.request.urlopen", return_value=fake_response({"questions": [item]})) as call:
                with self.assertRaises(AIUnavailable):
                    format_questions("What is 2 + 2? A is 3, B is 4, C is 5, D is 6. Answer: B")
                self.assertEqual(call.call_count, 2)

    def test_report_summary_uses_aggregate_counts(self):
        with patch.dict(os.environ, {"GEMINI_API_KEY": "demo-test-key"}):
            with patch("ai.request.urlopen", return_value=fake_response({"summary": "Revise algebra."})):
                self.assertEqual(
                    summarise_performance({"Algebra": {"correct": 2, "total": 5}}),
                    "Revise algebra.",
                )

    def test_class_insight_uses_aggregate_figures(self):
        with patch.dict(os.environ, {"GEMINI_API_KEY": "demo-test-key"}):
            with patch("ai.request.urlopen", return_value=fake_response({"summary": "The class average was 60%."})):
                self.assertEqual(
                    summarise_class_results(10, 60, 50),
                    "The class average was 60%.",
                )


if __name__ == "__main__":
    unittest.main()
