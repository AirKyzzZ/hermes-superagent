import json
import os
import sys

fixture = json.loads(open(os.environ["FAKE_WACLI_FIXTURE"]).read())
args = sys.argv[1:]
if args[:3] != ["--json", "messages", "list"]:
    sys.exit(2)
chat = args[args.index("--chat") + 1]
if chat in fixture.get("fail", []):
    print(json.dumps({"success": False, "data": None, "error": "store is locked"}))
    sys.exit(1)
print(json.dumps(fixture["chats"].get(chat, {"success": True, "data": [], "error": None})))
