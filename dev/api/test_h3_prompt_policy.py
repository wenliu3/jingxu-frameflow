"""Offline prompt policy tests: fake model responses and in-memory references."""
import unittest
from unittest.mock import patch

import agents
import ref_plan
from h3_prompt_policy import REF_SECTIONS, audit_body, check_ref_prompt, prompt_for_reference_images, shot_intent, split_ref_prompt
from schemas import Project


class PromptPolicyTests(unittest.TestCase):
    def setUp(self):
        self.project = Project.from_dict({"title": "Test", "style_en": "Natural daylight",
            "characters": [{"name": "Hero", "anchor_en": "a woman with short hair", "image_path": "hero.png"}]})
        self.materials = ["<Subject 1> = Hero (source <Picture 1>), a woman with short hair"]
        self.body = "Natural daylight. [Shot 1] <Subject 1> walks slowly through a quiet room."

    def compose(self, body=None, **extra):
        return ref_plan.compose_ref2va_prompt(self.project, characters=["Hero"],
            video_prompt=body or self.body, duration=6.5, use_voice=False, **extra)

    def write(self, description="她慢慢走过房间", **extra):
        return agents.compose_segment_prompt(self.project, description=description,
            material_lines=self.materials, duration=6.5, **extra)

    def test_default_single_take_even_for_long_video(self):
        for duration in (1, 1.5, 4, 6.5, 10, 15):
            self.assertEqual(shot_intent("女孩回头，镜头缓慢推进", duration), (1, []))

    def test_explicit_cuts_and_single_take_override(self):
        self.assertEqual(shot_intent("她走到门口，切到门外", 6.5)[0], 2)
        for text in ("三个镜头", "3 shots", "[Shot 1] a [Shot 2] b [Shot 3] c"):
            self.assertEqual(shot_intent(text, 10)[0], 3)
        self.assertEqual(shot_intent("一镜到底，不要切换到另一机位", 10)[0], 1)

    def test_dense_cuts_are_capped_with_visible_reminder(self):
        count, warnings = shot_intent("4 shots", 4)
        self.assertEqual(count, 2)
        self.assertTrue(warnings)

    def test_fractional_timestamps_remain_precise(self):
        text = self.body + " [Shot 2] At 00:04.500, she stops."
        self.assertEqual(audit_body(text, 6.5).errors, [])
        self.assertEqual(audit_body(text, 6.5).warnings, [])
        self.assertIn("不足 2 秒", audit_body(text.replace("04.500", "04.501"), 6.5).warnings[0])

    def test_invalid_timing_and_numbering_fail(self):
        bad = ("[Shot 1] At 00:00.000, a", "[Shot 1] a [Shot 3] At 00:02.000, b",
            "[Shot 1] a [Shot 2] b", "[Shot 1] a [Shot 2] At 00:06.500, b",
            "[Shot 1] a [Shot 2] At 00:03.500, b [Shot 3] At 00:03.499, c",
            "[Shot 1] a [Shot 2] At 00:61.000, b", "[Shot 1]")
        for text in bad:
            with self.subTest(text=text):
                self.assertTrue(audit_body(text, 6.5).errors)

    def test_unknown_references_cannot_be_submitted(self):
        for label in ("<Picture 2>", "<Subject 2>", "<Video 1>", "<Audio 1>"):
            with self.subTest(label=label), self.assertRaises(ValueError):
                self.compose(self.body + " Follow " + label)

    def test_dialogue_is_not_interpreted_as_reference_or_timing(self):
        text = self.body + ' (S1) says <d>[Chinese] 他说“<Video 99> [Shot 8] At 00:60.000,”</d>'
        plan = self.compose(text)
        self.assertIn('他说“<Video 99> [Shot 8] At 00:60.000,”', plan.prompt)
        self.assertFalse(audit_body(text, 6.5, allowed={"<Subject 1>"}).errors)

    def test_complete_prompt_preserved_without_rewrapping(self):
        prompt = self.compose().prompt
        self.assertEqual(self.compose(prompt).prompt, prompt)
        self.assertIn("6.5-second", prompt)
        self.assertEqual(list(split_ref_prompt(prompt)), list(REF_SECTIONS))

    def test_bad_complete_structures_fail(self):
        prompt = self.compose().prompt
        for bad in (prompt.replace("summary:", "subject_definitions:"),
                    prompt.replace("non_diegetic_music: N/A", "non_diegetic_music:"),
                    "Unexpected heading\n" + prompt, prompt.replace("[reference generation]", "Reference generation"),
                    prompt.replace("fully_preserved -", "fully_copy -"),
                    prompt.replace("6.5-second", "10-second")):
            with self.subTest(bad=bad[:80]), self.assertRaises(ValueError):
                check_ref_prompt(bad, 6.5, {"<Subject 1>", "<Picture 1>"})

    def test_subject_use_requires_a_definition_even_if_asset_exists(self):
        prompt = self.compose().prompt.replace('detailed_description: Natural daylight.',
            'detailed_description: Natural daylight. <Subject 2> is present.')
        with self.assertRaisesRegex(ValueError, 'subject_definitions'):
            check_ref_prompt(prompt, 6.5, {'<Subject 1>', '<Subject 2>', '<Picture 1>'})

    def test_plain_english_manual_prompt_becomes_one_shot(self):
        with patch.object(agents, "chat_json", side_effect=AssertionError("No model calls")):
            plan = self.compose("A woman walks through a room.")
        self.assertIn("[Shot 1] A woman walks", plan.prompt)

    def test_constraints_respect_requested_changes(self):
        constraints = ref_plan.DETAILED_CONSTRAINTS
        self.assertIn("requested changes", constraints)
        self.assertIn("need not all be visible", constraints)
        self.assertNotIn("identical in every shot", self.compose().prompt)

    def test_model_gets_separate_context_decimal_duration_and_correct_rules(self):
        with patch.object(agents, "chat_json", return_value={"video_prompt": self.body, "soundscape": "Footsteps."}) as model:
            result = self.write(context="上一镜包含三个镜头，她切到室外")
        self.assertEqual(model.call_count, 1)
        user = model.call_args.args[1]
        self.assertIn("6.5 秒", user)
        self.assertIn("350-500", user)
        self.assertIn("[Shot 1]", user)
        self.assertNotIn("[Shot 2]", user)
        self.assertIn("前情说明", user)
        self.assertIn("a woman with short hair", user)
        self.assertTrue(result["warnings"])
        self.assertIn("fully_preserved", agents._prompt_rules(True))
        self.assertNotIn("fully_preserved", agents._prompt_rules(False))

    def test_invalid_model_output_gets_one_bounded_repair(self):
        bad = {"video_prompt": self.body + " <Video 1>", "soundscape": "Footsteps."}
        good = {"video_prompt": self.body, "soundscape": "Footsteps."}
        with patch.object(agents, "chat_json", side_effect=[bad, good]) as model:
            self.assertEqual(self.write()["video_prompt"], self.body)
        self.assertEqual(model.call_count, 2)
        self.assertIn("未提供的素材编号", model.call_args.args[1])

    def test_failed_repair_stops_instead_of_using_invalid_output(self):
        for bad in ({"video_prompt": "missing shot", "soundscape": ""},
                    {"video_prompt": self.body, "soundscape": "Use <Audio 1>."},
                    {"video_prompt": None, "soundscape": []}, None):
            with self.subTest(bad=bad), patch.object(agents, "chat_json", return_value=bad) as model:
                with self.assertRaises(ValueError):
                    self.write()
                self.assertEqual(model.call_count, 2)

    def test_model_cannot_add_cuts_to_single_take_request(self):
        two_shots = self.body + " [Shot 2] At 00:03.000, she stops."
        with patch.object(agents, "chat_json", return_value={"video_prompt": two_shots, "soundscape": ""}):
            with self.assertRaises(ValueError):
                self.write("一镜到底，镜头跟随她走过房间")

    def test_changed_appearance_uses_partial_retention(self):
        result = {"video_prompt": self.body, "soundscape": "Footsteps.",
                  "retention": {"<Subject 1>": "partially_preserved"}}
        with patch.object(agents, "chat_json", return_value=result):
            data = self.write("她换上红外套走过房间")
        prompt = self.compose(retention=data["retention"]).prompt
        self.assertIn("partially_preserved -", prompt)
        self.assertNotIn("fully_preserved -", prompt)

    def test_invalid_retention_is_repaired_or_rejected(self):
        for retention in ({"<Subject 1>": "fully_copy"}, {"<Video 1>": "fully_preserved"},
                          {"<Picture 1>": "fully_preserved"}, []):
            result = {"video_prompt": self.body, "soundscape": "", "retention": retention}
            with self.subTest(retention=retention), patch.object(agents, "chat_json", return_value=result) as model:
                with self.assertRaises(ValueError):
                    self.write()
                self.assertEqual(model.call_count, 2)

    def test_provider_uses_six_sections_for_legacy_body_and_preserves_full_prompt(self):
        text = prompt_for_reference_images("A woman walks.", 6.5, 2)
        self.assertEqual(list(split_ref_prompt(text)), list(REF_SECTIONS))
        self.assertNotIn("integrated_multimodal_description", text)
        self.assertNotIn("at 0.00 seconds", text)
        self.assertEqual(prompt_for_reference_images(self.compose().prompt, 6.5, 1), self.compose().prompt)
        with self.assertRaises(ValueError):
            prompt_for_reference_images("[Shot 1] See <Picture 2>.", 6.5, 1)


if __name__ == "__main__":
    unittest.main()
