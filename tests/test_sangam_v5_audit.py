"""Comprehensive Automated Audit & Verification Suite for Sangam Study v5.0."""
from __future__ import annotations

import unittest
from unittest import mock

import test_phase0_1 as p01
from app.shared.latex_lint import clean_question_text, clean_question_dict
from app.shared.models import (
    compute_nta_test_meta,
    compute_nta_subject_cap,
    get_test_syllabus,
)

APP = p01.APP
DB = p01.DB
make_client = p01.make_client
post = p01.post


class SangamV5AuditTests(p01.Base):
    def setUp(self):
        super().setUp()
        self.stream = DB.insert("streams", {
            "id": "stream-neet",
            "name": "NEET UG",
            "slug": "neet",
            "description": "National Eligibility cum Entrance Test (UG)",
            "is_active": True,
        })
        DB.insert("user_streams", {
            "user_id": self.ids["alice"],
            "stream_id": "stream-neet",
        })
        self.sub_phy = DB.insert("subjects", {
            "id": "sub-phy", "stream_id": "stream-neet", "name": "Physics", "slug": "physics", "display_order": 1, "is_active": True,
        })
        self.sub_chem = DB.insert("subjects", {
            "id": "sub-chem", "stream_id": "stream-neet", "name": "Chemistry", "slug": "chemistry", "display_order": 2, "is_active": True,
        })
        self.sub_bio = DB.insert("subjects", {
            "id": "sub-bio", "stream_id": "stream-neet", "name": "Biology", "slug": "biology", "display_order": 3, "is_active": True,
        })
        self.chapter = DB.insert("chapters", {
            "id": "ch-basic-maths",
            "subject_id": "sub-phy",
            "name": "Basic Maths & Vectors",
            "slug": "basic-maths",
            "display_order": 1,
            "is_active": True,
        })
        self.series_alaukik = DB.insert("mock_test_series", {
            "id": "series-alaukik",
            "stream_id": "stream-neet",
            "name": "Alaukik Test Series",
            "is_paid": False,
            "price_inr": 0,
        })
        self.series_other = DB.insert("mock_test_series", {
            "id": "series-sankalp",
            "stream_id": "stream-neet",
            "name": "Sankalp Test Series",
            "is_paid": False,
            "price_inr": 0,
        })

    def test_01_logo_assets_and_case_insensitive_routes(self):
        c = APP.test_client()
        for path in ("/static/img/logo.png", "/static/Img/logo.png", "/static/img/logo-mark.png", "/static/Img/logo-mark.png"):
            res = c.get(path)
            self.assertEqual(res.status_code, 200, f"Failed to serve logo at {path}")
            self.assertTrue(len(res.data) > 1000, f"Logo at {path} should be non-empty")

        svg_res = c.get("/static/img/logo-mark.svg")
        self.assertEqual(svg_res.status_code, 200)
        self.assertIn(b"<svg", svg_res.data)

        login_html = c.get("/auth/login").get_data(as_text=True)
        self.assertIn("/static/img/logo-mark.png", login_html)
        self.assertNotIn("Lock In", login_html)

    def test_02_welcome_screen_and_dashboard_cleanup(self):
        c = make_client(self.ids["alice"])
        start_html = c.get("/start").get_data(as_text=True)
        self.assertIn("Start Learning", start_html)
        self.assertNotIn("Ready to Rank Up", start_html)
        self.assertNotIn("Enter Command HQ", start_html)
        self.assertNotIn("Switch Stream", start_html)

        dash_html = c.get("/streams/neet").get_data(as_text=True)
        self.assertNotIn("Open Pack", dash_html)
        self.assertNotIn("Command HQ", dash_html)

    def test_03_neet_720_mark_pattern_and_scoring(self):
        self.assertEqual(compute_nta_subject_cap(45), 45)
        self.assertEqual(compute_nta_subject_cap(50), 45)
        self.assertEqual(compute_nta_subject_cap(90), 90)
        self.assertEqual(compute_nta_subject_cap(100), 90)

        meta = compute_nta_test_meta(
            {"Physics": [{}] * 45, "Chemistry": [{}] * 45, "Biology": [{}] * 90},
            stored_total_marks=720,
        )
        self.assertEqual(meta["uploaded_questions"], 180)
        self.assertEqual(meta["attemptable_questions"], 180)
        self.assertEqual(meta["effective_total_marks"], 720)
        self.assertFalse(meta["is_incomplete_full_mock"])

        meta_fixed = compute_nta_test_meta(
            {"Physics": [{}] * 45, "Chemistry": [{}] * 45, "Biology": [{}] * 90},
            stored_total_marks=540,
        )
        self.assertEqual(meta_fixed["effective_total_marks"], 720)

    def test_04_admin_create_720_test_with_syllabus_and_delete_cleanup(self):
        admin_client = make_client(self.ids["owner"])
        create_url = f"/admin/test-series/{self.series_alaukik['id']}/tests"
        resp = post(admin_client, create_url, data={
            "title": "Alaukik Reading Test One",
            "duration_minutes": "180",
            "total_marks": "720",
            "syllabus_physics": "Laws of Motion\nWork, Energy and Power",
            "syllabus_chemistry": "Chemical Bonding\nThermodynamics",
            "syllabus_biology": "Cell Structure\nHuman Physiology",
        })
        self.assertEqual(resp.status_code, 302)

        tests = [t for t in DB.rows("tests") if t.get("title") == "Alaukik Reading Test One"]
        self.assertEqual(len(tests), 1)
        new_test = tests[0]
        self.assertEqual(new_test["total_marks"], 720)

        syllabus = get_test_syllabus(new_test["id"])
        self.assertIn("Laws of Motion", syllabus["Physics"])
        self.assertIn("Work, Energy and Power", syllabus["Physics"])
        self.assertIn("Chemical Bonding", syllabus["Chemistry"])
        self.assertIn("Thermodynamics", syllabus["Chemistry"])
        self.assertIn("Cell Structure", syllabus["Biology"])
        self.assertIn("Human Physiology", syllabus["Biology"])

        student_client = make_client(self.ids["alice"])
        overview_html = student_client.get(f"/streams/neet/tests/{new_test['id']}").get_data(as_text=True)
        self.assertIn("Laws of Motion", overview_html)
        self.assertIn("Work, Energy and Power", overview_html)
        self.assertIn("Chemical Bonding", overview_html)
        self.assertIn("Human Physiology", overview_html)
        self.assertIn("720", overview_html)

        DB.insert("test_attempts", {
            "id": "att-to-delete",
            "user_id": self.ids["alice"],
            "test_id": new_test["id"],
            "score": 600,
        })
        del_resp = post(admin_client, f"/admin/tests/{new_test['id']}/delete")
        self.assertEqual(del_resp.status_code, 302)
        self.assertFalse(any(t["id"] == new_test["id"] for t in DB.rows("tests")))
        self.assertFalse(any(a["test_id"] == new_test["id"] for a in DB.rows("test_attempts")))

        stale_resp = student_client.get(f"/streams/neet/tests/{new_test['id']}")
        self.assertEqual(stale_resp.status_code, 302)

    def test_05_syllabus_ignores_duplicate_subject_names_and_handles_empty(self):
        t = DB.insert("tests", {
            "id": "test-empty-syl",
            "stream_id": "stream-neet",
            "series_id": self.series_alaukik["id"],
            "title": "Test Without Custom Syllabus",
            "description": "",
            "duration_minutes": 180,
            "total_marks": 720,
            "is_active": True,
        })
        mq = DB.insert("mock_questions", {
            "id": "mq-placeholder",
            "stream_id": "stream-neet",
            "subject_id": "sub-phy",
            "topic_name": "Physics",
            "question_text": "Sample question",
            "option_a": "1", "option_b": "2", "option_c": "3", "option_d": "4",
            "correct_option": "A",
        })
        DB.insert("mock_test_questions", {"test_id": t["id"], "mock_question_id": mq["id"]})

        syl = get_test_syllabus(t["id"])
        self.assertNotIn("Physics", syl.get("Physics", []))

        c = make_client(self.ids["alice"])
        html = c.get(f"/streams/neet/tests/{t['id']}").get_data(as_text=True)
        self.assertIn("Syllabus details have not been added for this test yet.", html)

    def test_06_series_folder_my_attempts_isolation(self):
        t_alaukik = DB.insert("tests", {
            "id": "test-alaukik-1",
            "stream_id": "stream-neet",
            "series_id": self.series_alaukik["id"],
            "title": "Alaukik Test 1",
            "description": "",
            "duration_minutes": 180,
            "total_marks": 720,
            "is_active": True,
        })
        t_sankalp = DB.insert("tests", {
            "id": "test-sankalp-1",
            "stream_id": "stream-neet",
            "series_id": self.series_other["id"],
            "title": "Sankalp Test 1",
            "description": "",
            "duration_minutes": 180,
            "total_marks": 720,
            "is_active": True,
        })

        DB.insert("test_attempts", {
            "id": "att-alice-alaukik",
            "user_id": self.ids["alice"],
            "test_id": t_alaukik["id"],
            "score": 665,
            "correct_count": 168,
            "wrong_count": 7,
            "skipped_count": 5,
            "started_at": "2026-10-26T10:00:00Z",
            "submitted_at": "2026-10-26T12:45:00Z",
            "attempt_kind": "full",
            "parent_attempt_id": None,
        })
        DB.insert("test_attempts", {
            "id": "att-alice-sankalp",
            "user_id": self.ids["alice"],
            "test_id": t_sankalp["id"],
            "score": 590,
            "correct_count": 150,
            "wrong_count": 10,
            "skipped_count": 20,
            "started_at": "2026-10-26T13:00:00Z",
            "submitted_at": "2026-10-26T15:30:00Z",
            "attempt_kind": "full",
            "parent_attempt_id": None,
        })
        DB.insert("test_attempts", {
            "id": "att-bob-alaukik",
            "user_id": self.ids["bob"],
            "test_id": t_alaukik["id"],
            "score": 412,
            "correct_count": 110,
            "wrong_count": 28,
            "skipped_count": 42,
            "started_at": "2026-10-26T14:00:00Z",
            "submitted_at": "2026-10-26T16:30:00Z",
            "attempt_kind": "full",
            "parent_attempt_id": None,
        })

        c_alice = make_client(self.ids["alice"])
        alaukik_html = c_alice.get(f"/streams/neet/series/{self.series_alaukik['id']}/tests?tab=attempts").get_data(as_text=True)
        self.assertIn("Alaukik Test 1", alaukik_html)
        self.assertIn("665", alaukik_html)
        self.assertNotIn("Sankalp Test 1", alaukik_html)
        self.assertNotIn("590", alaukik_html)
        self.assertNotIn("412", alaukik_html)

        sankalp_html = c_alice.get(f"/streams/neet/series/{self.series_other['id']}/tests?tab=attempts").get_data(as_text=True)
        self.assertIn("Sankalp Test 1", sankalp_html)
        self.assertIn("590", sankalp_html)
        self.assertNotIn("Alaukik Test 1", sankalp_html)
        self.assertNotIn("665", sankalp_html)

    def test_07_chapter_practice_attempts_and_runner_layout(self):
        DB.insert("questions", {
            "id": "q-bm-1",
            "chapter_id": self.chapter["id"],
            "is_pyq": False,
            "category": "topic",
            "folder_name": "Vectors",
            "set_name": "DPP 1",
            "is_premium": False,
            "marks": 4,
            "question_text": "If \\vec{A} + \\vec{B} = \\vec{C}, find the angle.",
            "option_a": "0", "option_b": "45", "option_c": "90", "option_d": "180",
            "correct_option": "C",
            "explanation": "Orthogonal vectors.",
            "image_url": None,
            "has_image": False,
        })
        att = DB.insert("practice_attempts", {
            "id": "pact-alice-1",
            "user_id": self.ids["alice"],
            "chapter_id": self.chapter["id"],
            "mode": "mcq",
            "category": "topic",
            "folder_name": "Vectors",
            "set_name": "DPP 1",
            "score": 4,
            "total_marks": 4,
            "total_questions": 1,
            "correct_count": 1,
            "wrong_count": 0,
            "skipped_count": 0,
            "started_at": "2026-10-26T10:00:00Z",
            "submitted_at": None,
            "attempt_kind": "full",
            "parent_attempt_id": None,
        })

        c = make_client(self.ids["alice"])
        with mock.patch("app.user.routes._practice_ctx", return_value=(self.stream, self.chapter)):
            run_html = c.get(f"/streams/neet/practice/chapter/{self.chapter['id']}/mcq/topic/Vectors/DPP%201/run?attempt_id={att['id']}").get_data(as_text=True)

        self.assertIn("Correct</span>", run_html)
        self.assertIn("Incorrect</span>", run_html)
        self.assertIn("0 Unanswered", run_html)
        self.assertIn("Question Palette", run_html)
        self.assertIn("Not Visited", run_html)
        self.assertIn("Not Answered", run_html)
        self.assertIn("Marked for Review", run_html)
        self.assertIn("Save &amp; Check Answer", run_html)
        self.assertIn("Clear Response", run_html)
        self.assertIn('<template x-if="hasValidImage(currentQuestion)">', run_html)

    def test_08_live_mock_test_interface_and_no_ghost_diagram_error(self):
        t = DB.insert("tests", {
            "id": "test-live-nta",
            "stream_id": "stream-neet",
            "series_id": self.series_alaukik["id"],
            "title": "NTA Full Length Mock Test",
            "description": "",
            "duration_minutes": 180,
            "total_marks": 720,
            "is_active": True,
        })
        att = DB.insert("test_attempts", {
            "id": "att-live-nta",
            "user_id": self.ids["alice"],
            "test_id": t["id"],
            "started_at": "2026-10-26T10:00:00Z",
            "submitted_at": None,
            "attempt_kind": "full",
            "parent_attempt_id": None,
        })
        sample_questions = {
            "Physics": [{"id": f"p{i}", "question_text": f"Physics Q{i}", "option_a": "A", "option_b": "B", "option_c": "C", "option_d": "D", "image_url": None, "has_image": False} for i in range(1, 46)],
            "Chemistry": [{"id": f"c{i}", "question_text": f"Chem Q{i}", "option_a": "A", "option_b": "B", "option_c": "C", "option_d": "D", "image_url": None, "has_image": False} for i in range(1, 46)],
            "Biology": [{"id": f"b{i}", "question_text": f"Bio Q{i}", "option_a": "A", "option_b": "B", "option_c": "C", "option_d": "D", "image_url": None, "has_image": False} for i in range(1, 91)],
        }
        c = make_client(self.ids["alice"])
        with mock.patch("app.user.routes.get_mock_questions_for_test", return_value=sample_questions):
            html = c.get(f"/streams/neet/tests/{t['id']}/attempt/{att['id']}").get_data(as_text=True)

        self.assertIn("+4 Correct", html)
        self.assertIn("-1 Incorrect", html)
        self.assertIn("0 Unanswered", html)
        self.assertIn("Question Palette", html)
        self.assertIn("overflow-y-auto", html)
        self.assertIn("Not Visited", html)
        self.assertIn("Not Answered", html)
        self.assertIn("Marked for Review", html)
        self.assertIn("Answered &amp; Marked", html)
        self.assertIn("Save &amp; Next", html)
        self.assertIn("Skip / Next", html)
        self.assertIn("Clear Response", html)
        self.assertIn('<template x-if="hasValidImage(currentQuestion)">', html)

    def test_09_latex_and_span_artifact_cleaning(self):
        dirty_text = (
            "[span_0](start_span)Assertion (A): Acceleration is proportional to displacement.[span_0](end_span) "
            "[span_1](start_span)Reason (R): \\frac{d^2x}{dt^2} = -\\omega^2 x[span_1](end_span)"
        )
        cleaned = clean_question_text(dirty_text)
        self.assertNotIn("[span_0]", cleaned)
        self.assertNotIn("(start_span)", cleaned)
        self.assertNotIn("(end_span)", cleaned)
        self.assertIn(r"\(", cleaned)
        self.assertIn(r"\frac{d^2x}{dt^2}", cleaned)

        double_esc = r"Find the value of \\(\sqrt{2gH}\\)"
        cleaned_esc = clean_question_text(double_esc)
        self.assertIn(r"\(\sqrt{2gH}\)", cleaned_esc)
        self.assertNotIn(r"\\(", cleaned_esc)

        q_dict = {
            "question_text": "[span_0](start_span)What is \\alpha + \\beta?[span_0](end_span)",
            "option_a": "1",
            "option_b": "2",
            "option_c": "3",
            "option_d": "4",
            "image_url": "null",
        }
        cleaned_q = clean_question_dict(q_dict)
        self.assertIsNone(cleaned_q["image_url"])
        self.assertNotIn("span_0", cleaned_q["question_text"])
        self.assertIn(r"\(\alpha\)", cleaned_q["question_text"])


if __name__ == "__main__":
    unittest.main()
