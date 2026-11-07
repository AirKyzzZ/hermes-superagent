import json
import os
import sys

fixture = json.loads(open(os.environ["FAKE_WACLI_FIXTURE"]).read())
args = sys.argv[1:]
if args[:2] != ["--read-only", "--json"]:
    sys.stderr.write("the connector must run wacli read-only with JSON\n")
    sys.exit(2)
if args[2:3] == ["doctor"]:
    print(json.dumps(fixture["doctor"]))
    sys.exit(0)
if args[2:4] != ["messages", "list"]:
    sys.exit(2)
chat = args[args.index("--chat") + 1]
if chat in fixture.get("fail", []):
    sys.stderr.write(json.dumps({"success": False, "data": None, "error": "store is locked"}) + "\n")
    sys.exit(1)
empty = {"success": True, "data": {"messages": None, "fts": False, "page": {}, "source": {}}, "error": None}
print(json.dumps(fixture["chats"].get(chat, empty)))
