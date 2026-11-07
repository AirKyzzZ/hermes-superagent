import json
import os
import sys

fixture = json.loads(open(os.environ["FAKE_TD_FIXTURE"]).read())
args = sys.argv[1:]
if args[:2] == ["project", "list"]:
    if fixture.get("projects_fail"):
        sys.stderr.write("Error: network\n")
        sys.exit(1)
    print(json.dumps({"results": fixture.get("projects", []), "nextCursor": None}))
elif args[:2] == ["task", "list"]:
    if "--all" not in args or "--full" not in args:
        sys.stderr.write("the connector must ask for every task with every field\n")
        sys.exit(2)
    print(json.dumps({"results": fixture.get("tasks", []), "nextCursor": fixture.get("next_cursor")}))
elif args[:2] == ["task", "view"]:
    wanted = args[2].removeprefix("id:")
    task = fixture.get("views", {}).get(wanted)
    if task is None:
        sys.stderr.write(f"Error: Task not found: {wanted}\n")
        sys.exit(1)
    print(json.dumps(task))
else:
    sys.exit(2)
