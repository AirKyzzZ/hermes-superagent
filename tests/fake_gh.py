import json
import os
import sys

fixture = json.loads(open(os.environ["FAKE_GH_FIXTURE"]).read())
args = sys.argv[1:]
if args[:1] != ["api"]:
    sys.exit(2)
path = args[1]
fields = dict(arg.split("=", 1) for arg in args[2:] if "=" in arg)
if path == "search/issues":
    query = fields["q"]
    if query in fixture.get("fail", []):
        sys.stderr.write("HTTP 502\n")
        sys.exit(1)
    items = fixture["search"].get(query, [])
    size, page = int(fields["per_page"]), int(fields["page"])
    total = fixture.get("totals", {}).get(query, len(items))
    print(
        json.dumps(
            {
                "total_count": total,
                "incomplete_results": query in fixture.get("incomplete", []),
                "items": items[(page - 1) * size : page * size],
            }
        )
    )
elif path.startswith("repos/"):
    key = path[len("repos/") :].replace("/issues/", "#")
    if key not in fixture.get("issues", {}):
        sys.stderr.write("HTTP 404\n")
        sys.exit(1)
    print(json.dumps({"state": fixture["issues"][key]}))
else:
    sys.exit(2)
