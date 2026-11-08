import json
import os
import sys

fixture = json.loads(open(os.environ["FAKE_GOG_FIXTURE"]).read())
args = sys.argv[1:]
if "--readonly" not in args:
    sys.stderr.write("refusing to run without --readonly\n")
    sys.exit(2)
words = [arg for arg in args if not arg.startswith("-")]
if "search" in words:
    print(json.dumps(fixture["search"]))
elif "get" in words:
    thread = fixture.get("threads", {}).get(words[-1])
    if thread is None:
        sys.stderr.write("Error: Requested entity was not found.\n")
        sys.exit(1)
    print(json.dumps(thread))
else:
    sys.exit(2)
