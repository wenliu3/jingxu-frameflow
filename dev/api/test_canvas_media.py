"""Canvas jobs and provider publishing; temporary projects, no remote model calls."""
import copy
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from PIL import Image, ImageDraw

from server.canvas_media import register_canvas_media_routes, MediaBody
from server.canvas_media_services import candidate_outputs, media_services
from server.portrait_reference import crop_portrait_reference
import pipeline


def write_sheet(path):
    image = Image.new("RGB", (900, 450), "white")
    draw = ImageDraw.Draw(image)
    for left, right, color in [(20, 270, "red"), (315, 445, "green"),
                               (515, 625, "blue"), (735, 845, "black")]:
        draw.rectangle((left, 20, right, 429), fill=color)
    image.save(path, format="PNG")


class CanvasMediaTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.task = {"status": "succeeded", "project": {"title": "Test", "assets": []}}
        self.calls = []
        self.gate = threading.Event()
        self.gate.set()
        self.fail = False
        self.client = self.client_for()
        self.base = "/api/tasks/abcdef123456/media-jobs"

    def tearDown(self):
        self.gate.set()
        for _ in range(100):
            if not any(t.name.startswith("canvas-test-") for t in threading.enumerate()):
                break
            time.sleep(.01)
        self.client.close()
        self.temp.cleanup()

    def lookup(self, task_id):
        if task_id != "abcdef123456":
            raise HTTPException(404, "No project")
        return self.task

    def client_for(self):
        app = FastAPI()
        def prepare(task_id, body):
            self.task["project"]["assets"].append({"name": body.title})
            return {"name": body.title}
        def render(task_id, body, output):
            threading.current_thread().name = f"canvas-test-{body.request_id}"
            self.calls.append(body.model_dump())
            self.gate.wait(3)
            if self.fail:
                raise RuntimeError("Provider unavailable")
            Path(output).write_bytes(b"fixture media")
        def publish(task_id, body, output):
            return {"asset_name": body.title, "file": Path(output).name, "version": "123abc"}
        register_canvas_media_routes(app, self.lookup, lambda _: str(self.root),
            prepare, render, publish, lambda: [{"id": "voice-test"}])
        return TestClient(app)

    def payload(self, kind="image", request_id="a" * 32, node_id="node-one"):
        return {"node_id": node_id, "request_id": request_id, "kind": kind,
            "title": "Canvas test", "prompt": "Literal text", "ratio": "9:16",
            "voice_id": "voice-test" if kind == "audio" else ""}

    def terminal(self, job_id):
        for _ in range(150):
            result = self.client.get(f"{self.base}/{job_id}").json()
            if result["status"] not in {"queued", "running"}:
                return result
            time.sleep(.01)
        self.failTest("Job never finished")

    def test_image_is_durable_and_duplicate_submission_never_generates_twice(self):
        body = self.payload()
        first = self.client.post(self.base, json=body)
        self.assertEqual(first.status_code, 200)
        result = self.terminal(body["request_id"])
        self.assertEqual(result["status"], "succeeded")
        self.assertTrue((self.root / "media_jobs" / result["result"]["file"]).is_file())
        self.assertEqual(self.client.post(self.base, json=body).json()["status"], "succeeded")
        self.assertEqual(len(self.calls), 1)
        restored = self.client_for()
        self.addCleanup(restored.close)
        self.assertEqual(restored.get(f"{self.base}/{body['request_id']}").json()["result"], result["result"])
        changed = {**body, "prompt": "Other text"}
        self.assertEqual(self.client.post(self.base, json=changed).status_code, 409)

    def test_active_retries_share_one_job_and_busy_node_cannot_be_submitted_again(self):
        self.gate.clear()
        body = self.payload()
        self.client.post(self.base, json=body)
        self.assertEqual(self.client.post(self.base, json=body).status_code, 200)
        self.assertEqual(self.client.post(self.base, json={**body, "request_id": "b" * 32}).status_code, 409)
        self.gate.set()
        self.assertEqual(self.terminal(body["request_id"])["status"], "succeeded")
        self.assertEqual(len(self.calls), 1)

    def test_speech_requires_current_voice_and_preserves_literal_words(self):
        body = self.payload("audio")
        self.assertEqual(self.client.post(self.base, json={**body, "voice_id": "missing"}).status_code, 422)
        self.assertEqual(self.calls, [])
        self.assertEqual(self.client.post(self.base, json=body).status_code, 200)
        result = self.terminal(body["request_id"])
        self.assertEqual(result["status"], "succeeded")
        self.assertTrue(result["result"]["file"].endswith(".mp3"))
        self.assertEqual(self.calls[0]["prompt"], "Literal text")

    def test_failure_does_not_publish_and_restart_never_replays(self):
        self.fail = True
        body = self.payload()
        self.client.post(self.base, json=body)
        result = self.terminal(body["request_id"])
        self.assertEqual(result["status"], "failed")
        self.assertNotIn("result", result)
        record = self.root / "media_jobs" / f"{body['request_id']}.json"
        stored = json.loads(record.read_text())
        stored["status"] = "running"
        record.write_text(json.dumps(stored))
        restored = self.client_for()
        self.addCleanup(restored.close)
        self.assertEqual(restored.get(f"{self.base}/{body['request_id']}").json()["status"], "interrupted")
        self.assertEqual(len(self.calls), 1)

    def test_bad_paths_empty_input_and_missing_project_never_reach_provider(self):
        body = self.payload()
        for patch_ in [{"node_id": "../bad"}, {"request_id": "../bad"}, {"prompt": "  "}, {"ratio": "21:9"}, {"image_mode": "unknown"}]:
            self.assertEqual(self.client.post(self.base, json={**body, **patch_}).status_code, 422)
        self.assertEqual(self.client.get(f"{self.base}/../../escape").status_code, 404)
        self.assertEqual(self.client.post(self.base.replace("abcdef123456", "missing"), json=body).status_code, 404)
        self.assertEqual(self.calls, [])

    def test_publish_binds_by_node_identity_and_preserves_old_result_until_success(self):
        writes, snapshots = [], []
        prepare, render, publish = media_services(self.lookup, lambda _: str(self.root),
            threading.RLock(), lambda _: writes.append(copy.deepcopy(self.task)),
            lambda *args: snapshots.append(args))
        body = MediaBody(**self.payload("audio"))
        entry = prepare("abcdef123456", body)
        with patch("server.canvas_media_services.tts.synth") as synth:
            render("abcdef123456", body, str(self.root / "test.mp3"))
            synth.assert_called_once_with("Literal text", "voice-test", str(self.root / "test.mp3"))
        output = self.root / "result.mp3"
        output.write_bytes(b"new audio")
        result = publish("abcdef123456", body, str(output))
        entry = self.task["project"]["assets"][0]
        self.assertEqual(Path(entry["images"][0]).read_bytes(), b"new audio")
        self.assertEqual(entry["canvas_input"]["request_id"], body.request_id)
        self.assertTrue(result["file"].startswith("snd_"))
        # A removed asset cannot turn a different asset into this node's output.
        self.task["project"]["assets"] = [{"kind": "audio", "name": "Unrelated", "images": []}]
        with self.assertRaises(HTTPException):
            publish("abcdef123456", body, str(output))
        self.assertEqual(self.task["project"]["assets"][0]["images"], [])

    def test_native_image_preserves_literal_prompt_and_needs_no_text_agent(self):
        prepare, render, publish = media_services(self.lookup, lambda _: str(self.root),
            threading.RLock(), lambda _: None, lambda *args: None)
        body = MediaBody(**self.payload())
        prepare("abcdef123456", body)
        output = self.root / "result.png"
        with patch("server.canvas_media_services.agents.design_asset") as design, \
             patch("server.canvas_media_services.create_provider") as provider:
            render("abcdef123456", body, str(output))
            design.assert_not_called()
            self.assertEqual(provider.return_value.generate.call_args.args[0], body.prompt)
            self.assertEqual(provider.return_value.generate.call_args.kwargs["size"], "576x1024")
        output.write_bytes(b"image")
        self.assertTrue(publish("abcdef123456", body, str(output))["file"].startswith("img_"))

    def services(self, snapshots=None):
        return media_services(self.lookup, lambda _: str(self.root), threading.RLock(), lambda _: None,
            lambda *args: snapshots.append(args) if snapshots is not None else None)

    def test_chosen_style_reaches_native_provider_and_templates_without_changing_project(self):
        self.task["project"]["style"] = "Original project style"
        _, render, _ = self.services()
        prompt, selected = "My subject description", "Selected watercolor style"
        for mode in ["native", "scene", "character", "prop"]:
            with self.subTest(mode=mode):
                body = MediaBody(**{**self.payload(), "image_mode": mode,
                    "prompt": prompt, "style_prompt": selected})
                with patch("server.canvas_media_services.create_provider") as provider, \
                     patch("server.canvas_media_services.agents.design_asset", return_value="Scene") as scene, \
                     patch("server.canvas_media_services.agents.compose_portrait_sheet", return_value={"body": "Character", "sheet": "Sheet"}) as character, \
                     patch("server.canvas_media_services.agents.compose_prop_sheet", return_value={"sheet": "Prop"}) as prop:
                    provider.return_value.generate.side_effect = lambda prompt, path, **kwargs: write_sheet(path)
                    render("abcdef123456", body, str(self.root / "styled.png"))
                    if mode == "native":
                        self.assertEqual(provider.return_value.generate.call_args.args[0], prompt + "\n画面风格：" + selected)
                        scene.assert_not_called()
                    else:
                        design = {"scene": scene, "character": character, "prop": prop}[mode]
                        self.assertEqual(design.call_args.args[0].style, selected)
                        self.assertEqual(design.call_args.kwargs["hint"], prompt)
                    self.assertEqual(body.prompt, prompt)
                    self.assertEqual(self.task["project"]["style"], "Original project style")

    def test_style_is_durable_idempotent_and_rejected_for_speech(self):
        body = {**self.payload(), "style_prompt": "Ink wash"}
        self.assertEqual(self.client.post(self.base, json=body).status_code, 200)
        result = self.terminal(body["request_id"])
        self.assertEqual(result["input"]["style_prompt"], "Ink wash")
        self.assertEqual(self.client.post(self.base, json=body).status_code, 200)
        self.assertEqual(self.client.post(self.base, json={**body, "style_prompt": "Film"}).status_code, 409)
        audio = {**self.payload("audio", request_id="b" * 32), "style_prompt": "Ink wash"}
        self.assertEqual(self.client.post(self.base, json=audio).status_code, 422)
        self.assertEqual(self.client.post(self.base, json={**body, "style_prompt": "x" * 1001}).status_code, 422)
        self.assertEqual(len(self.calls), 1)

    def test_specialized_modes_reuse_library_templates_and_user_preferences(self):
        prepare, render, publish = self.services()
        for mode in ["scene", "prop", "character"]:
            with self.subTest(mode=mode):
                body = MediaBody(**{**self.payload(node_id="node-" + mode), "image_mode": mode})
                prepare("abcdef123456", body)
                output = str(self.root / f"{mode}.png")
                def generate(prompt, path, **kwargs):
                    write_sheet(path)
                with patch("server.canvas_media_services.agents.design_asset", return_value="Forest setting") as scene, \
                     patch("server.canvas_media_services.agents.compose_prop_sheet", return_value={"body": "Sword", "sheet": "Prop sheet template"}) as prop, \
                     patch("server.canvas_media_services.agents.compose_portrait_sheet", return_value={"body": "Girl in gray", "sheet": "Character sheet template"}) as character, \
                     patch("server.canvas_media_services.create_provider") as provider:
                    provider.return_value.generate.side_effect = generate
                    render("abcdef123456", body, output)
                    calls = provider.return_value.generate.call_args_list
                    if mode == "scene":
                        scene.assert_called_once()
                        self.assertEqual(scene.call_args.args[1:3], ("scene", body.title))
                        self.assertEqual(scene.call_args.kwargs["hint"], body.prompt)
                        self.assertIn("空镜", calls[0].args[0])
                        self.assertIn("person", calls[0].kwargs["negative_prompt"])
                        self.assertEqual(calls[0].kwargs["size"], "576x1024")
                    elif mode == "prop":
                        prop.assert_called_once()
                        self.assertEqual(prop.call_args.kwargs["hint"], body.prompt)
                        self.assertEqual(calls[0].args[0], "Prop sheet template")
                        self.assertEqual(calls[0].kwargs["size"], "1024x576")
                    else:
                        character.assert_called_once()
                        self.assertEqual(character.call_args.kwargs["hint"], body.prompt)
                        self.assertEqual(len(calls), 1, "one entire sheet, no second image generation")
                        self.assertEqual(calls[0].args[0], "Character sheet template")
                        self.assertEqual(calls[0].kwargs["negative_prompt"], pipeline.SHEET_NEGATIVE)
                        self.assertEqual(calls[0].kwargs["size"], "1024x576")
                        with Image.open(output + ".portrait.png") as portrait:
                            colors = {color for _, color in portrait.getcolors()}
                            self.assertIn((0, 128, 0), colors)
                            self.assertNotIn((255, 0, 0), colors)
                            self.assertNotIn((0, 0, 255), colors)
                result = publish("abcdef123456", body, output)
                self.assertEqual(result["kind"], mode)
                entries = self.task["project"]["characters" if mode == "character" else "assets"]
                entry = entries[result["index"]]
                self.assertEqual(entry["canvas_input"]["image_mode"], mode)
                if mode == "character":
                    self.assertEqual(entry["images"], [entry["image_path"]])
                    self.assertNotEqual(entry["sheet"], entry["image_path"])
                    self.assertEqual(Path(entry["sheet"]).name, result["file"])
                    self.assertIn("characters", Path(entry["sheet"]).parts)
                elif mode == "prop":
                    self.assertEqual(entry["images"], [])
                    self.assertTrue(Path(entry["sheet"]).is_file())
                else:
                    self.assertTrue(Path(entry["images"][0]).is_file())

    def test_changing_type_preserves_previous_material_and_reuses_matching_category(self):
        prepare, _, publish = self.services()
        output = self.root / "result.png"
        for mode in ["native", "scene", "prop", "character"]:
            body = MediaBody(**{**self.payload(), "image_mode": mode})
            prepare("abcdef123456", body)
            output.write_bytes(mode.encode())
            Path(str(output) + ".portrait.png").write_bytes(b"portrait")
            publish("abcdef123456", body, str(output))
        self.assertEqual(len(self.task["project"]["assets"]), 3)
        self.assertEqual(len(self.task["project"]["characters"]), 1)
        for entry in self.task["project"]["assets"]:
            file = entry.get("sheet") or entry["images"][0]
            expected = "native" if entry["kind"] == "image" else entry["kind"]
            self.assertEqual(Path(file).read_bytes(), expected.encode())
        native = MediaBody(**self.payload())
        entry = prepare("abcdef123456", native)
        self.assertEqual(len(self.task["project"]["assets"]), 3)
        self.assertEqual(entry["kind"], "image")

    def test_character_failed_publication_keeps_both_previous_files_and_metadata(self):
        snapshots = []
        prepare, _, publish = self.services(snapshots)
        body = MediaBody(**{**self.payload(), "image_mode": "character"})
        prepare("abcdef123456", body)
        output = self.root / "sheet.png"
        output.write_bytes(b"old sheet")
        portrait = Path(str(output) + ".portrait.png")
        portrait.write_bytes(b"old portrait")
        publish("abcdef123456", body, str(output))
        previous = copy.deepcopy(self.task["project"]["characters"][0])
        retry = body.model_copy(update={"request_id": "b" * 32})
        portrait.unlink()
        with self.assertRaises(RuntimeError):
            publish("abcdef123456", retry, str(output))
        portrait.write_bytes(b"new portrait")
        output.write_bytes(b"new sheet")
        original_copy = __import__("shutil").copyfile
        count = 0
        def fail_second_copy(source, target):
            nonlocal count
            count += 1
            if count == 2:
                raise OSError("disk unavailable")
            return original_copy(source, target)
        with patch("server.canvas_media_services.shutil.copyfile", side_effect=fail_second_copy):
            with self.assertRaises(OSError):
                publish("abcdef123456", retry, str(output))
        self.assertEqual(self.task["project"]["characters"][0], previous)
        self.assertEqual(Path(previous["sheet"]).read_bytes(), b"old sheet")
        self.assertEqual(Path(previous["image_path"]).read_bytes(), b"old portrait")
        self.assertEqual(snapshots[-1][1], "character")
        self.assertEqual(set(snapshots[-1][3]), {previous["sheet"], previous["image_path"]})

    def test_sheet_ratio_is_enforced_and_mode_is_part_of_idempotent_input(self):
        body = {**self.payload(), "image_mode": "prop"}
        self.assertEqual(self.client.post(self.base, json=body).status_code, 200)
        self.assertEqual(self.terminal(body["request_id"])["input"]["ratio"], "16:9")
        self.assertEqual(self.client.post(self.base, json={**body, "ratio": "4:3"}).status_code, 200)
        self.assertEqual(self.client.post(self.base, json={**body, "image_mode": "scene"}).status_code, 409)
        self.assertEqual(self.client.post(self.base, json={**self.payload("audio", request_id="c" * 32), "image_mode": "prop"}).status_code, 422)
        self.assertEqual(len(self.calls), 1)

    def test_retry_of_legacy_record_without_image_mode_does_not_generate_again(self):
        body = self.payload()
        self.client.post(self.base, json=body)
        result = self.terminal(body["request_id"])
        record = self.root / "media_jobs" / f"{body['request_id']}.json"
        result["input"].pop("image_mode")
        result["input"].pop("style_prompt")
        result["input"].pop("count")
        record.write_text(json.dumps(result), encoding="utf-8")
        restored = self.client_for()
        self.addCleanup(restored.close)
        self.assertEqual(restored.post(self.base, json={**body, "image_mode": "native"}).json()["status"], "succeeded")
        self.assertEqual(len(self.calls), 1)

    def test_counts_are_validated_persisted_and_part_of_idempotency(self):
        for value in [0, 3, 5, True, 1.0, "2", None]:
            self.assertEqual(self.client.post(self.base, json={**self.payload(), "count": value}).status_code, 422)
        self.assertEqual(self.client.post(self.base, json={**self.payload("audio"), "count": 2}).status_code, 422)
        self.assertEqual(self.calls, [])
        body = {**self.payload(), "count": 4}
        self.assertEqual(self.client.post(self.base, json=body).status_code, 200)
        self.assertEqual(self.terminal(body["request_id"])["input"]["count"], 4)
        self.assertEqual(self.client.post(self.base, json=body).status_code, 200)
        self.assertEqual(self.client.post(self.base, json={**body, "count": 2}).status_code, 409)
        self.assertEqual(len(self.calls), 1)

    def test_batches_generate_requested_candidates_with_one_shared_design(self):
        prepare, render, publish = self.services()
        for mode in ["native", "character", "scene", "prop"]:
            for count in [1, 2, 4]:
                with self.subTest(mode=mode, count=count):
                    body = MediaBody(**{**self.payload(node_id=f"{mode}-{count}"),
                        "image_mode": mode, "count": count})
                    prepare("abcdef123456", body)
                    output = str(self.root / f"{mode}-{count}.png")
                    with patch("server.canvas_media_services.create_provider") as provider, \
                         patch("server.canvas_media_services.agents.design_asset", return_value="Forest") as scene, \
                         patch("server.canvas_media_services.agents.compose_portrait_sheet", return_value={"sheet": "Complete four-view sheet"}) as character, \
                         patch("server.canvas_media_services.agents.compose_prop_sheet", return_value={"sheet": "Prop sheet"}) as prop:
                        provider.return_value.generate.side_effect = lambda prompt, path, **kwargs: write_sheet(path)
                        render("abcdef123456", body, output)
                        calls = provider.return_value.generate.call_args_list
                        self.assertEqual(len(calls), count)
                        self.assertEqual(len({call.args[0] for call in calls}), 1)
                        self.assertEqual(len({call.kwargs["seed"] for call in calls}), count)
                        if mode == "native":
                            scene.assert_not_called()
                            character.assert_not_called()
                            prop.assert_not_called()
                        else:
                            {"scene": scene, "character": character, "prop": prop}[mode].assert_called_once()
                    result = publish("abcdef123456", body, output)
                    self.assertEqual(len(result["results"]), count)
                    self.assertEqual(result["file"], result["results"][0]["file"])
                    entries = self.task["project"]["characters" if mode == "character" else "assets"]
                    for i, candidate in enumerate(result["results"]):
                        entry = entries[candidate["index"]]
                        self.assertEqual(entry["name"], candidate["asset_name"])
                        self.assertEqual(entry["canvas_candidate"], i)
                        file = entry.get("sheet") or entry["images"][0]
                        self.assertEqual(Path(file).name, candidate["file"])
                        self.assertTrue(Path(file).is_file())
                        if mode == "character":
                            self.assertTrue(Path(entry["image_path"]).is_file())

    def test_failed_batch_keeps_every_previous_candidate_and_reuses_slots(self):
        prepare, _, publish = self.services()
        body = MediaBody(**{**self.payload(), "count": 4})
        prepare("abcdef123456", body)
        output = str(self.root / "batch.png")
        for path in candidate_outputs(output, 4):
            Path(path).write_bytes(b"old image")
        first = publish("abcdef123456", body, output)
        previous = copy.deepcopy(self.task["project"]["assets"])
        retry = body.model_copy(update={"request_id": "b" * 32})
        Path(candidate_outputs(output, 4)[2]).unlink()
        with self.assertRaisesRegex(RuntimeError, "第 3 张"):
            publish("abcdef123456", retry, output)
        self.assertEqual(self.task["project"]["assets"], previous)
        for path in candidate_outputs(output, 4):
            Path(path).write_bytes(b"new image")
        original_copy = __import__("shutil").copyfile
        def fail_third(source, target):
            if "candidate-3" in source:
                raise OSError("disk unavailable")
            return original_copy(source, target)
        with patch("server.canvas_media_services.shutil.copyfile", side_effect=fail_third):
            with self.assertRaises(OSError):
                publish("abcdef123456", retry, output)
        self.assertEqual(self.task["project"]["assets"], previous)
        self.assertTrue(all(Path(entry["images"][0]).read_bytes() == b"old image" for entry in previous))
        second = publish("abcdef123456", retry, output)
        self.assertEqual(len(self.task["project"]["assets"]), 4)
        self.assertEqual([r["asset_name"] for r in first["results"]], [r["asset_name"] for r in second["results"]])
        self.assertTrue(all(Path(entry["images"][0]).read_bytes() == b"new image" for entry in self.task["project"]["assets"]))
        self.assertFalse(list(self.root.rglob("*.tmp")))

    def test_portrait_crop_uses_template_layout_when_panel_gaps_are_ambiguous(self):
        sheet, portrait = self.root / "ambiguous.png", self.root / "front.png"
        Image.new("RGB", (900, 450), "gray").save(sheet)
        crop_portrait_reference(sheet, portrait)
        with Image.open(portrait) as image:
            self.assertEqual(image.size, (200, 450))


if __name__ == "__main__":
    unittest.main()
