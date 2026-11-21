"""The log: plain text by default, JSON when asked, errors keep their traceback."""
import io, json, logging, os, sys
os.environ["PEAKCUT_LOG_JSON"] = "1"
sys.path.insert(0, ".")
from studio import logs
fails = []
def check(n, ok, x=""):
    print(("PASS " if ok else "FAIL ") + n + (f"  {x}" if x else "")); ok or fails.append(n)
root = logs.setup()
buf = io.StringIO(); root.handlers[0].stream = buf
log = logs.get("test")
log.info("hello %s", "there")
try:
    1 / 0
except ZeroDivisionError:
    log.exception("it broke")
lines = buf.getvalue().strip().split("\n")
first = json.loads(lines[0]); rest = json.loads("\n".join(lines[1:]))
check("a line says what, how serious and where", (first["msg"], first["level"], first["where"]) == ("hello there", "INFO", "peakcut.test"), str(first))
check("an error keeps its traceback", "ZeroDivisionError" in rest["error"] and rest["level"] == "ERROR")
check("setting it up twice adds nothing", logs.setup() is root and len(root.handlers) == 1)
print("ALL PASSED" if not fails else f"{len(fails)} failed"); sys.exit(1 if fails else 0)
