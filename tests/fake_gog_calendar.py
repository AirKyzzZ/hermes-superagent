import json
import os
import sys

fixture = json.loads(open(os.environ["FAKE_GOG_CALENDAR_FIXTURE"]).read())
args = sys.argv[1:]


def value(flag):
    return args[args.index(flag) + 1] if flag in args else ""


def refuse(text):
    sys.stderr.write(text + "\n")
    sys.exit(2)


if not {"--readonly", "--json", "--no-input"} <= set(args):
    refuse("refusing to run without --readonly, --json and --no-input")
if value("--account") != "owner@example.com":
    refuse("expected the account")
start = args.index("calendar") + 1
command, rest = args[start], args[start + 1 :]
if command == "calendars":
    if "--all" not in rest:
        refuse("expected every page of calendars")
    if fixture.get("calendars_fail"):
        sys.stderr.write("Google API error (403 forbidden): no access\n")
        sys.exit(6)
    print(json.dumps(fixture["calendars"]))
elif command == "events":
    if "--all" in rest or "--all-pages" not in rest or value("--max") != "250":
        refuse("expected one calendar, every page, 250 a page")
    if value("--days") != str(fixture.get("days", 3)):
        refuse("wrong window")
    if value("--timezone") != fixture.get("timezone", "Europe/Paris"):
        refuse("expected the primary calendar's timezone")
    calendar = rest[0]
    if calendar in fixture.get("failing", []):
        sys.stderr.write("Google API error (404 notFound): Not Found\n")
        sys.exit(5)
    print(json.dumps(fixture["events"].get(calendar, {"events": [], "nextPageToken": ""})))
else:
    refuse("unknown command")
