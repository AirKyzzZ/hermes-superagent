import json
import os
import sys
import urllib.parse

fixture = json.loads(open(os.environ["FAKE_GLAB_FIXTURE"]).read())
args = [arg for arg in sys.argv[1:] if arg != "--include"]
if args[:1] != ["api"]:
    sys.exit(2)
target = args[-1]
path, _, query = target.partition("?")
fields = dict(urllib.parse.parse_qsl(query))
page, size = int(fields.pop("page", 1)), int(fields.pop("per_page", 20))
key = path + ("?" + urllib.parse.urlencode(fields) if fields else "")
headers = ["HTTP/2.0 200 OK", "Content-Type: application/json"]
if fixture.get("cwd_log"):
    with open(fixture["cwd_log"], "a") as log:
        log.write(os.getcwd() + "\n")
if key in fixture.get("fail", []):
    sys.stderr.write("HTTP 502\n")
    sys.exit(1)
if path == "user":
    body = {"username": fixture.get("username", "owner")}
elif path.startswith("projects/"):
    states = fixture.get("states", {})
    ref = urllib.parse.unquote(path[len("projects/") :])
    ref = ref.replace("/issues/", "#").replace("/merge_requests/", "!")
    if ref not in states:
        sys.stderr.write("HTTP 404\n")
        sys.exit(1)
    body = states[ref] if isinstance(states[ref], dict) else {"state": states[ref]}
else:
    entries = fixture.get("lists", {}).get(key, [])
    total = fixture.get("totals", {}).get(key, len(entries))
    if page > 1 and key in fixture.get("shift", []):
        total -= 1
    body = entries[(page - 1) * size : page * size]
    if key not in fixture.get("no_total", []):
        headers.append(f"X-Total: {total}")
    headers.append(f"X-Next-Page: {page + 1 if page * size < len(entries) else ''}")
print("\r\n".join(headers) + "\r\n\r\n" + json.dumps(body))
