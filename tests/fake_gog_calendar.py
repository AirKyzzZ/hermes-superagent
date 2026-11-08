import json
import os
import sys

fixture = json.loads(open(os.environ["FAKE_GOG_CALENDAR_FIXTURE"]).read())
args = sys.argv[1:]
if not {"--readonly", "--json", "--no-input"} <= set(args):
    sys.stderr.write("refusing to run without --readonly, --json and --no-input\n")
    sys.exit(2)
start = args.index("calendar") + 1
command, rest = args[start], args[start + 1 :]
if command == "calendars":
    if fixture.get("calendars_fail"):
        sys.stderr.write("Google API error (403 forbidden): no access\n")
        sys.exit(6)
    print(json.dumps(fixture["calendars"]))
elif command == "events":
    if "--all" in rest or "--all-pages" not in rest or "--days" not in rest:
        sys.stderr.write("expected one calendar, --all-pages and --days\n")
        sys.exit(2)
    calendar = rest[0]
    if calendar in fixture.get("failing", []):
        sys.stderr.write("Google API error (404 notFound): Not Found\n")
        sys.exit(5)
    print(json.dumps(fixture["events"].get(calendar, {"events": [], "nextPageToken": ""})))
else:
    sys.exit(2)
