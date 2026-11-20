"""Check that cuts use the source video and keep their order on screen."""
import os
import sys
import tempfile
import wave
from array import array
from pathlib import Path

from fastapi import HTTPException

sys.path.insert(0, ".")


def main(data):
    os.environ.update(PEAKCUT_DATA=data, DATABASE_URL="", PEAKCUT_ACCOUNTS="0")
    from studio import auth, ctx, db, jobs, media, server

    db.init()
    with ctx.as_user(auth.local_user().id):
        job = jobs.create({"type": "file", "name": "Source"}, {}, enqueue=False)
        source = Path(job["dir"]) / "source.mp4"
        media.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "color=red:s=180x320:d=3",
                   "-f", "lavfi", "-i", "color=blue:s=180x320:d=3",
                   "-itsoffset", "0.75", "-f", "lavfi", "-i", "sine=duration=6",
                   "-filter_complex", "[0:v][1:v]concat=n=2:v=1:a=0[v]", "-map", "[v]", "-map", "2:a",
                   "-c:v", "libx264", "-c:a", "aac", "-t", "6", str(source)])
        clip = {"id": "edit", "status": "done", "start": 0.0, "end": 3.0, "title": "Clip", "description": "",
                "hook": "", "hashtags": [], "score": 90, "style": "clean"}
        job.update(status="done", source_path=str(source), duration=6.0, width=180, height=320, clips=[clip])

        for invalid in ([], [[-1, 3]], [[4, 3], [0, 3]], [[0, 9]], [[0, 1]], [[0, "inf"]]):
            try:
                server.rerender(job["id"], "edit", {"segments": invalid})
            except HTTPException as error:
                assert error.status_code == 400, error
            else:
                raise AssertionError(f"Invalid sequence was accepted: {invalid}")
        print("PASS invalid sequences don't change the clip")

        segments = [[3, 5], [0, 1.5], [3, 4]]
        response = server.rerender(job["id"], "edit", {"segments": segments})
        assert response["clips"][0]["segments"] == segments
        try:
            server.rerender(job["id"], "edit", {"segments": [[0, 3]]})
        except HTTPException as error:
            assert error.status_code == 409
        else:
            raise AssertionError("An edit overwrote a render already in the queue")
        words = [{"w": "Red", "s": 0.25, "e": 0.6}, {"w": "Blue", "s": 3.25, "e": 3.6}]
        jobs.render_one(job, clip, words)
        assert clip["status"] == "done", clip.get("error")
        output = Path(job["dir"]) / "clips" / clip["file"]
        assert abs(media.probe(output)["duration"] - 4.5) < 0.12
        for mark, dominant in [(0.9, "u"), (2.8, "v"), (4.0, "u")]:
            stats = media.run(["ffmpeg", "-v", "info", "-ss", str(mark), "-i", str(output), "-frames:v", "1",
                               "-vf", "signalstats,metadata=print:file=-", "-f", "null", "-"])
            levels = {key: float(next(line.split("=")[-1] for line in stats.splitlines()
                                      if line.startswith(f"lavfi.signalstats.{key.upper()}AVG="))) for key in "uv"}
            assert levels[dominant] > 200, (mark, levels)
        print("PASS remote-in-time, earlier and repeated source ranges render in the requested order")

        silence = Path(data) / "silence.wav"
        media.run(["ffmpeg", "-v", "error", "-ss", "2.2", "-t", "0.2", "-i", str(output), "-vn",
                   "-ac", "1", "-c:a", "pcm_s16le", str(silence)])
        with wave.open(str(silence)) as wav:
            samples = array("h", wav.readframes(wav.getnframes()))
        assert samples and max(map(abs, samples)) < 50, "The second segment lost the silence before its audio starts"
        print("PASS the audio keeps its original offset after a cut")

        ass = output.with_suffix(".ass").read_text(encoding="utf-8")
        for word, timestamp in [("BLUE", "0:00:00.25"), ("RED", "0:00:02.25"), ("BLUE", "0:00:03.75")]:
            assert any(f",{timestamp}," in line and word in line.upper() for line in ass.splitlines()), (word, ass)
        print("PASS captions follow each repeated or reordered range")

        preview = server.clip_source(job["id"], "edit")
        assert preview.path == source and preview.headers["content-type"] == "video/mp4"
        with ctx.as_user("abcdef0123456789"):
            try:
                server.clip_source(job["id"], "edit")
            except HTTPException as error:
                assert error.status_code == 404
            else:
                raise AssertionError("Another account could see the original file")
        print("PASS only the owner can preview the original")

        job.update(kind="live", source_path=None)
        stream = output.parent / "edit.src.ts"
        media.run(["ffmpeg", "-v", "error", "-i", str(source), "-c", "copy", str(stream)])
        clip.update(src=str(stream), src_offset=30.0, src_end=36.0)
        assert server.clip_source_info(job["id"], "edit")["preview"] == [30, 36]
        preview = server.clip_source(job["id"], "edit")
        assert preview.headers["content-type"] == "video/mp4" and Path(preview.path).is_file()
        assert abs(media.probe(Path(preview.path))["duration"] - 6) < 0.12
        print("PASS a saved live recording opens in a browser-compatible container")
        server.rerender(job["id"], "edit", {"segments": [[33, 35], [30, 31.5]]})
        jobs.render_one(job, clip, [dict(w, s=w["s"] + 30, e=w["e"] + 30) for w in words])
        assert clip["status"] == "done", clip.get("error")
        assert abs(media.probe(output)["duration"] - 3.5) < 0.12
        print("PASS a saved source piece uses the original recording's times")


if __name__ == "__main__":
    with tempfile.TemporaryDirectory(prefix="peakcut-edit-test-") as data:
        try:
            main(data)
        finally:
            from studio import db
            db.engine.dispose()
