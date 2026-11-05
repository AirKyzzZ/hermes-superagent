import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

BASE = "https://collectionapi.metmuseum.org/public/collection"
UA = {"User-Agent": "hermes-superagent-art/0.1"}


def get(url):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.load(resp)


def main():
    out_path = sys.argv[1]
    queries = sys.argv[2:]
    seen = {}
    missing = []
    for q in queries:
        qs = urllib.parse.urlencode(
            {"q": q, "hasImages": "true", "isPublicDomain": "true", "limit": 40, "offset": 0}
        )
        data = get(f"{BASE}/v1.1/search?{qs}")
        ids = data.get("objectIDs") or []
        print(f"{q!r}: total={data['total']} got={len(ids)}", file=sys.stderr)
        for oid in ids:
            if oid in seen or oid in missing:
                continue
            try:
                obj = get(f"{BASE}/v1/objects/{oid}")
            except urllib.error.HTTPError as err:
                if err.code != 404:
                    raise
                missing.append(oid)
                print(f"404 for object {oid}", file=sys.stderr)
                continue
            seen[oid] = {
                "id": oid,
                "title": obj.get("title"),
                "dept": obj.get("department"),
                "pd": obj.get("isPublicDomain"),
                "medium": obj.get("medium"),
                "date": obj.get("objectDate"),
                "img": obj.get("primaryImage"),
                "n_extra": len(obj.get("additionalImages") or []),
                "url": obj.get("objectURL"),
                "query": q,
            }
            time.sleep(0.06)
            with open(out_path, "w") as fh:
                json.dump(list(seen.values()), fh, indent=1)
    print(len(seen), "objects,", len(missing), "missing", file=sys.stderr)


main()
