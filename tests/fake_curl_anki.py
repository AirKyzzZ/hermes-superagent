import json
import os
import sys

fixture = json.loads(open(os.environ["FAKE_ANKI_FIXTURE"]).read())
if "--data-binary" not in sys.argv or sys.argv[sys.argv.index("--data-binary") + 1] != "@-":
    sys.stderr.write("expected the request on stdin\n")
    sys.exit(2)
if "exit" in fixture:
    sys.stderr.write("curl: (7) Failed to connect\n")
    sys.exit(fixture["exit"])
if "body" in fixture:
    print(fixture["body"])
    sys.exit(0)
request = json.loads(sys.stdin.read())
action = request["action"]
if request["version"] != 6 or action not in ("findCards", "getDecks", "getNumCardsReviewedToday"):
    sys.stderr.write(f"refusing {action}, it isn't read-only\n")
    sys.exit(2)
if action == "findCards" and request["params"] != {"query": "is:due"}:
    sys.stderr.write("expected the is:due query\n")
    sys.exit(2)
if action == "getDecks" and not request["params"]["cards"]:
    sys.stderr.write("expected card ids\n")
    sys.exit(2)
if action in fixture.get("errors", {}):
    print(json.dumps({"result": None, "error": fixture["errors"][action]}))
else:
    print(json.dumps({"result": fixture[action], "error": None}))
