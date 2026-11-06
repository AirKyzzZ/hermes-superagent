import json
import os
import sys

fixture = json.loads(open(os.environ["FAKE_GH_FIXTURE"]).read())
args = [arg for arg in sys.argv[1:] if arg != "--include"]
include = "--include" in sys.argv
if args[:1] != ["api"]:
    sys.exit(2)
path = args[1]
fields = dict(arg.split("=", 1) for arg in args[2:] if "=" in arg)
headers = ["HTTP/2.0 200 OK", "Content-Type: application/json"]
if path == "search/issues":
    query = fields["q"]
    if query in fixture.get("fail", []):
        sys.stderr.write("HTTP 502\n")
        sys.exit(1)
    items = fixture["search"].get(query, [])
    size, page = int(fields["per_page"]), int(fields["page"])
    total = fixture.get("totals", {}).get(query, len(items))
    if page > 1 and query in fixture.get("shift", []):
        total -= 1
    if query in fixture.get("sso", []):
        headers.append("X-GitHub-SSO: partial-results; organizations=21955855")
    body = {
        "total_count": total,
        "incomplete_results": query in fixture.get("incomplete", []),
        "items": items[(page - 1) * size : page * size],
    }
elif path == "user":
    body = {"login": fixture.get("login", "owner")}
elif path.startswith("repos/") and path.endswith("/comments"):
    key = path[len("repos/") : -len("/comments")].replace("/issues/", "#")
    found = fixture.get("comments", {}).get(key, [])
    size, page = int(fields.get("per_page", 30)), int(fields.get("page", 1))
    body = found[(page - 1) * size : page * size]
elif path.startswith("repos/"):
    key = path[len("repos/") :].replace("/issues/", "#")
    if key not in fixture.get("issues", {}):
        sys.stderr.write("HTTP 404\n")
        sys.exit(1)
    issue = fixture["issues"][key]
    body = issue if isinstance(issue, dict) else {"state": issue}
else:
    sys.exit(2)
print(("\r\n".join(headers) + "\r\n\r\n" if include else "") + json.dumps(body))
