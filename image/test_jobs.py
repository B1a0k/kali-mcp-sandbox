import os
import sys
import tempfile
import time
import unittest
import threading
from unittest.mock import patch
from collections import namedtuple
from jobs import Jobs, MAX_OUTPUT

class JobStoreTests(unittest.TestCase):
    """Persistent state and wait contracts also run on the Windows development host."""
    def test_restart_marks_unfinished_work_interrupted(self):
        with tempfile.TemporaryDirectory() as root:
            jobs = Jobs(root)
            job_id = 'a' * 32
            jobs.db.execute("INSERT INTO jobs VALUES(?,?,?,'running',NULL,?,NULL)", (job_id, 'request', 'fingerprint', time.time()))
            jobs.db.commit()
            jobs.db.close()
            recovered = Jobs(root)
            self.assertEqual(recovered.read(job_id)['state'], 'interrupted')
            self.assertEqual(recovered.health()['activeJobs'], 0)
            recovered.db.close()

    def test_wait_wakes_on_completion_without_holding_database_lock(self):
        with tempfile.TemporaryDirectory() as root:
            jobs = Jobs(root)
            job_id = 'b' * 32
            jobs.db.execute("INSERT INTO jobs VALUES(?,?,?,'running',NULL,?,NULL)", (job_id, 'request', 'fingerprint', time.time()))
            jobs.db.commit()
            def complete():
                time.sleep(.05)
                with jobs.lock:
                    jobs._finish(job_id, 'succeeded', 0)
            worker = threading.Thread(target=complete)
            worker.start()
            self.assertEqual(jobs.read(job_id, wait_seconds=2)['state'], 'succeeded')
            worker.join()
            with self.assertRaises(ValueError):
                jobs.read(job_id, wait_seconds=100)
            jobs.db.close()

@unittest.skipUnless(sys.platform == "linux", "worker limits and process groups target the Linux guest; host persistence tests run on every OS")
class JobTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.jobs = Jobs(self.tmp.name + "/jobs")

    def tearDown(self):
        self.jobs.close()
        deadline = time.monotonic() + 8
        while self.jobs.processes and time.monotonic() < deadline:
            time.sleep(.02)
        self.jobs.db.close()
        self.tmp.cleanup()

    def finished(self, job):
        for _ in range(300):
            result = self.jobs.read(job["jobId"])
            if result["state"] not in ("queued", "running"):
                return result
            time.sleep(.02)
        self.fail("job did not finish")

    def test_idempotent_and_exit_status(self):
        job = self.jobs.submit("echo useful; exit 2", "same")
        self.assertEqual(self.jobs.submit("echo useful; exit 2", "same")["jobId"], job["jobId"])
        self.assertEqual(self.jobs.health()["jobs"][0]["requestId"], "same")
        result = self.finished(job)
        self.assertEqual(result["state"], "failed")
        self.assertIn("useful", result["output"])
        with self.assertRaises(ValueError):
            self.jobs.submit("echo different", "same")

    def test_history_over_256_does_not_require_a_new_environment(self):
        self.jobs.db.executemany("INSERT INTO jobs VALUES(?,?,?,'succeeded',0,?,?)",
            [(f"{i:032x}", f"historical-{i}", 'fingerprint', time.time(), time.time()) for i in range(300)])
        self.jobs.db.commit()
        result = self.finished(self.jobs.submit("printf still-usable", "after-history"))
        self.assertEqual(result["state"], "succeeded")
        self.assertEqual(result["output"], "still-usable")
        self.assertEqual(self.jobs.db.execute("SELECT count(*) FROM jobs").fetchone()[0], 301)

    def test_low_disk_rejects_new_work_but_keeps_idempotency_and_results(self):
        request = self.jobs.submit("printf retained", "retained")
        self.finished(request)
        Usage = namedtuple('Usage', 'total used free')
        with patch('jobs.shutil.disk_usage', return_value=Usage(1000, 999, 1)):
            with self.assertRaisesRegex(RuntimeError, "nearly full"):
                self.jobs.submit("printf new", "new")
            self.assertEqual(self.jobs.submit("printf retained", "retained")["jobId"], request["jobId"])
        self.assertEqual(self.jobs.read(request["jobId"])["output"], "retained")

    def test_cancel_one_of_two_jobs_preserves_the_other(self):
        a = self.jobs.submit("sleep 30", "conversation-a")
        b = self.jobs.submit("printf before; sleep 2; printf after", "conversation-b")
        self.assertEqual(self.jobs.cancel(a["jobId"])["state"], "cancelled")
        result = self.finished(b)
        self.assertEqual(result["state"], "succeeded")
        self.assertEqual(result["output"], "beforeafter")

    def test_timeout_is_not_success(self):
        result = self.finished(self.jobs.submit("echo partial; sleep 20", timeout=1))
        self.assertEqual(result["state"], "timed_out")

    def test_cancel_and_path_validation(self):
        job = self.jobs.submit("sleep 20")
        self.assertEqual(self.jobs.cancel(job["jobId"])["state"], "cancelled")
        with self.assertRaises(ValueError):
            self.jobs.read("../../etc/passwd")

    def test_output_bounded(self):
        job = self.jobs.submit(["python3", "-c", "print('x'*6000000)"])
        self.finished(job)
        self.assertLessEqual((self.jobs.root / (job["jobId"] + ".stdout")).stat().st_size, MAX_OUTPUT)

    def test_small_output_arrives_while_process_is_running(self):
        job = self.jobs.submit("printf first; sleep 3", timeout=10)
        result = self.jobs.read(job['jobId'], wait_seconds=2)
        self.assertEqual(result['output'], 'first')
        self.assertEqual(result['state'], 'running')

if __name__ == "__main__":
    unittest.main()
