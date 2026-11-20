"""Queue a batch without contacting a platform or touching real clips."""
import os
import sys
import tempfile
import time
import unittest

sys.path.insert(0, ".")
os.environ.update(PEAKCUT_DATA=tempfile.mkdtemp(prefix="pc-posts-"), DATABASE_URL="",
                  PEAKCUT_ACCOUNTS="1", PEAKCUT_VERIFY_EMAIL="0")

from fastapi.testclient import TestClient
from studio import accounts, auth, creators, ctx, db, jobs, posting, server


class BatchPosts(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        db.init()
        cls.owner, _ = auth._create("poster@example.com", "Poster", password_hash="x")
        cls.other, _ = auth._create("other@example.com", "Other", password_hash="x")
        cls.client = TestClient(server.app, base_url="http://localhost", follow_redirects=False)
        cls.client.cookies.set(auth.COOKIE, auth.start_session(cls.owner.id))
        with ctx.as_user(cls.other.id):
            accounts._put({"id": "foreign", "platform": "youtube", "name": "Other channel", "added": 0})
            cls.foreign = jobs.create({"type": "file", "name": "Other video"}, {}, enqueue=False)
            cls.foreign["clips"] = [{"id": "private", "status": "done"}]
            jobs.save(cls.foreign)

    def setUp(self):
        self.user = ctx.as_user(self.owner.id)
        self.user.__enter__()
        posting._posts.clear()
        for aid in ("youtube", "instagram"):
            accounts._put({"id": aid, "platform": aid, "name": aid, "added": 0})
        creator = creators.create({"name": "Creator", "post_gap_min": 30, "daily_cap": 10})
        self.job = jobs.create({"type": "file", "name": "Video"}, {}, enqueue=False, creator=creator["id"])
        self.job["clips"] = [{"id": cid, "status": status, "title": cid}
                             for cid, status in (("one", "done"), ("two", "done"), ("busy", "rendering"))]
        jobs.save(self.job)
        self.pairs = [[self.job["id"], "one"], [self.job["id"], "two"]]

    def tearDown(self):
        self.user.__exit__(None, None, None)

    def post(self, **body):
        return self.client.post("/api/posts/bulk", json={"clips": self.pairs, "accounts": ["youtube"], **body},
                                headers={"Origin": "http://localhost"})

    def test_batch_queues_each_clip_for_each_account_once(self):
        version = ctx.version()
        response = self.post(clips=self.pairs + self.pairs[:1], accounts=["youtube", "instagram", "youtube"])
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json(), {"queued": 4, "clips": 2, "skipped": 0})
        self.assertEqual(len(posting._posts), 4)
        self.assertGreater(ctx.version(), version)
        self.assertEqual(len(jobs.get(self.job["id"])["clips"][0]["posts"]), 2)
        retry = self.post(accounts=["youtube", "instagram"])
        self.assertEqual(retry.json(), {"queued": 0, "clips": 0, "skipped": 2})

    def test_queue_spacing_and_post_now(self):
        self.assertEqual(self.post().status_code, 200)
        times = [p["due_at"] for p in posting._posts]
        self.assertGreaterEqual(times[1] - times[0], 1800)
        posting._posts.clear()
        before = time.time()
        self.assertEqual(self.post(now=True).status_code, 200)
        self.assertTrue(all(before <= p["due_at"] <= time.time() for p in posting._posts))

    def test_invalid_batches_have_no_partial_effect(self):
        invalid = [
            ({"accounts": []}, 400), ({"accounts": ["foreign"]}, 400),
            ({"accounts": "youtube"}, 400), ({"accounts": ["youtube"] * 21}, 400),
            ({"clips": []}, 400), ({"clips": "one"}, 400),
            ({"clips": [None]}, 400), ({"clips": [[[], "one"]]}, 400),
            ({"clips": self.pairs * 76}, 400),
            ({"clips": self.pairs + [[self.job["id"], "busy"]]}, 404),
            ({"clips": self.pairs + [[self.foreign["id"], "private"]]}, 404),
        ]
        for body, expected in invalid:
            with self.subTest(body=body):
                response = self.post(**body)
                self.assertEqual(response.status_code, expected, response.text)
                self.assertEqual(posting._posts, [])

    def test_previously_queued_clip_does_not_block_the_rest(self):
        self.assertEqual(self.post(clips=self.pairs[:1]).status_code, 200)
        response = self.post()
        self.assertEqual(response.json(), {"queued": 1, "clips": 1, "skipped": 1})


if __name__ == "__main__":
    unittest.main(verbosity=2)
