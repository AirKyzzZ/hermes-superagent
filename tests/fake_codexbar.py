import json
import os
import sys

fixture = json.loads(open(os.environ["FAKE_CODEXBAR_FIXTURE"]).read())
args = sys.argv[1:]
if args[:3] != ["usage", "--format", "json"]:
    sys.stderr.write("expected usage --format json\n")
    sys.exit(2)
provider = args[args.index("--provider") + 1] if "--provider" in args else ""
if fixture.get("broken"):
    print("not json")
elif provider in fixture.get("fail", []):
    sys.stderr.write("codexbar crashed\n")
    sys.exit(1)
elif provider:
    print(json.dumps(fixture["providers"].get(provider, [])))
else:
    print(json.dumps(fixture["enabled"]))
    if isinstance(fixture["enabled"], list) and any(
        "error" in entry for entry in fixture["enabled"] if isinstance(entry, dict)
    ):
        sys.exit(1)
