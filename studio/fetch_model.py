"""The speech-to-text model: downloaded once, by itself, the first time it is needed.

Nothing has to be run by hand. (python -m studio.fetch_model still works, for getting it ahead of time.) It is
about 480 MB. It is unpacked next to where it will live and moved into place only when it is whole, so a download
that was cut off can never leave half a model behind."""
import shutil
import tarfile
import threading
import urllib.request

from . import config

RELEASE = "https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models"
_lock = threading.Lock()
state = {"doing": False, "percent": 0, "error": ""}


def ready(model_dir=None) -> bool:
    """Is the whole model there: the word list and all three parts?"""
    d = model_dir or config.ASR_MODEL_DIR
    if not (d / "tokens.txt").is_file():
        return False
    return all(any(d.glob(f"{part}*.onnx")) for part in ("encoder", "decoder", "joiner"))


def _progress(blocks, size, total):
    if total > 0:
        state["percent"] = min(99, int(blocks * size * 100 / total))


def ensure(model_dir=None) -> None:
    """Make sure the model is on this computer, downloading it if it is not. One download at a time: anything else
    that needs the model meanwhile waits for it here."""
    d = model_dir or config.ASR_MODEL_DIR
    if ready(d):
        return
    with _lock:
        if ready(d):
            return
        state.update(doing=True, percent=0, error="")
        print("\n  Downloading the speech model (one time, about 480 MB)...\n", flush=True)
        config.MODELS_DIR.mkdir(parents=True, exist_ok=True)
        tmp, part = config.MODELS_DIR / "speech-model.tar.bz2", config.MODELS_DIR / "speech-model.unpacking"
        try:
            urllib.request.urlretrieve(f"{RELEASE}/{d.name}.tar.bz2", tmp, _progress)
            shutil.rmtree(part, ignore_errors=True)
            part.mkdir()
            with tarfile.open(tmp, "r:bz2") as t:
                for m in t.getmembers():  # only plain files and folders that stay inside the unpacking folder
                    if (m.isfile() or m.isdir()) and not m.name.startswith(("/", "..")) and ".." not in m.name.split("/"):
                        t.extract(m, part)
            got = part / d.name
            if not got.is_dir():
                raise OSError("the download did not contain the model")
            shutil.rmtree(d, ignore_errors=True)        # a half-finished one from before
            got.rename(d)
            print("  Speech model ready.\n", flush=True)
        except Exception as e:  # noqa: BLE001
            state["error"] = str(e)[-200:]
            raise RuntimeError("PeakCut couldn't download its speech model (about 480 MB, needed once). Check the "
                               "internet connection and free disk space, then try again.") from e
        finally:
            state["doing"] = False
            tmp.unlink(missing_ok=True)
            shutil.rmtree(part, ignore_errors=True)


def main():
    if ready():
        print("Speech model already present.")
        return
    ensure()


if __name__ == "__main__":
    main()
