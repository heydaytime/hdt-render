import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from hdt_render import jobs


def write_output_renderer(**kwargs):
    Path(kwargs["output_path"]).write_bytes(b"mp4")


def noop_renderer(**_kwargs):
    return None


class RenderJobQueueTests(unittest.TestCase):
    def make_queue(self, renderer=write_output_renderer):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        patcher = patch("hdt_render.jobs.render_mp4", renderer)
        patcher.start()
        self.addCleanup(patcher.stop)
        return jobs.RenderJobQueue(Path(temp.name))

    def enqueue(self, queue, client_request_id="request-1"):
        return queue.enqueue(
            headline="Test Headline",
            script="This is the script.",
            narration=io.BytesIO(b"wav-data"),
            width=1920,
            height=1080,
            fps=30,
            use_nvenc=True,
            client_request_id=client_request_id,
        )

    def test_enqueue_persists_job_and_completes_in_background(self):
        queue = self.make_queue()

        job, created = self.enqueue(queue)
        self.assertTrue(created)
        self.assertEqual(job["status"], "queued")
        self.assertEqual(job["clientRequestId"], "request-1")

        job_dir = queue.root / job["id"]
        self.assertTrue((job_dir / "job.json").exists())
        self.assertEqual((job_dir / "script.txt").read_text(encoding="utf-8"), "This is the script.")
        self.assertEqual((job_dir / "narration.wav").read_bytes(), b"wav-data")

        completed = queue.wait_for_completion(job["id"])
        self.assertEqual(completed["status"], "completed")
        self.assertEqual(queue.output_path(job["id"]).read_bytes(), b"mp4")
        self.assertEqual(json.loads((job_dir / "job.json").read_text(encoding="utf-8"))["status"], "completed")

    def test_client_request_id_returns_existing_non_failed_job(self):
        queue = self.make_queue()

        first, created = self.enqueue(queue, "same-request")
        self.assertTrue(created)
        duplicate, duplicate_created = self.enqueue(queue, "same-request")

        self.assertFalse(duplicate_created)
        self.assertEqual(duplicate["id"], first["id"])
        queue.wait_for_completion(first["id"])

    def test_failed_idempotency_key_can_create_new_job(self):
        queue = self.make_queue(noop_renderer)

        first, created = self.enqueue(queue, "retryable-request")
        self.assertTrue(created)
        failed = queue.wait_for_completion(first["id"])
        self.assertEqual(failed["status"], "failed")

        second, second_created = self.enqueue(queue, "retryable-request")
        self.assertTrue(second_created)
        self.assertNotEqual(second["id"], first["id"])
        queue.wait_for_completion(second["id"])

    def test_list_jobs_filters_by_client_request_id(self):
        queue = self.make_queue()

        first, _ = self.enqueue(queue, "a")
        second, _ = self.enqueue(queue, "b")

        filtered = queue.list_jobs("a")
        self.assertEqual([job["id"] for job in filtered], [first["id"]])
        self.assertIn(second["id"], [job["id"] for job in queue.list_jobs()])
        queue.wait_for_completion(first["id"])
        queue.wait_for_completion(second["id"])

    def test_startup_requeues_abandoned_rendering_job_once(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        job_dir = root / "render_abandoned"
        job_dir.mkdir()
        (job_dir / "script.txt").write_text("Script", encoding="utf-8")
        (job_dir / "narration.wav").write_bytes(b"wav")
        (job_dir / "job.json").write_text(json.dumps({
            "id": "render_abandoned",
            "status": "rendering",
            "stage": "Rendering MP4",
            "progress": 50,
            "clientRequestId": "abandoned",
            "headline": "Headline",
            "scriptHash": jobs.script_hash("Script"),
            "width": 1920,
            "height": 1080,
            "fps": 30,
            "useNvenc": True,
            "attempts": 1,
            "error": None,
            "createdAt": jobs.utc_now(),
            "updatedAt": jobs.utc_now(),
            "startedAt": jobs.utc_now(),
            "completedAt": None,
        }), encoding="utf-8")

        with patch("hdt_render.jobs.render_mp4", write_output_renderer):
            queue = jobs.RenderJobQueue(root)
            completed = queue.wait_for_completion("render_abandoned")

        self.assertEqual(completed["status"], "completed")
        self.assertEqual(completed["attempts"], 2)

    def test_startup_fails_repeatedly_abandoned_rendering_job(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        job_dir = root / "render_stale"
        job_dir.mkdir()
        (job_dir / "job.json").write_text(json.dumps({
            "id": "render_stale",
            "status": "rendering",
            "stage": "Rendering MP4",
            "progress": 50,
            "clientRequestId": "stale",
            "headline": "Headline",
            "scriptHash": jobs.script_hash("Script"),
            "width": 1920,
            "height": 1080,
            "fps": 30,
            "useNvenc": True,
            "attempts": 2,
            "error": None,
            "createdAt": jobs.utc_now(),
            "updatedAt": jobs.utc_now(),
            "startedAt": jobs.utc_now(),
            "completedAt": None,
        }), encoding="utf-8")

        queue = jobs.RenderJobQueue(root)
        loaded = queue.get_job("render_stale")

        self.assertEqual(loaded["status"], "failed")
        self.assertEqual(loaded["error"], "server_restarted_during_render")


if __name__ == "__main__":
    unittest.main()
