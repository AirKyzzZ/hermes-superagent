import json
import pathlib
import sys
import time
import urllib.request

BASE = "https://collectionapi.metmuseum.org/public/collection/v1/objects"
UA = {"User-Agent": "hermes-superagent-art/0.1"}


def fetch(url):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=60) as resp:
        return resp.read()


def main():
    root = pathlib.Path(sys.argv[1])
    for oid in sys.argv[2:]:
        dest = root / f"met-{oid}"
        dest.mkdir(parents=True, exist_ok=True)
        meta_path = dest / "object.json"
        if not meta_path.exists():
            meta_path.write_bytes(fetch(f"{BASE}/{oid}"))
            time.sleep(1.0)
        obj = json.loads(meta_path.read_text())
        if obj.get("isPublicDomain") is not True:
            raise SystemExit(f"{oid} is not public domain")
        urls = [obj["primaryImage"], *obj.get("additionalImages", [])]
        for i, url in enumerate(urls):
            if not url:
                continue
            target = dest / f"view-{i}.jpg"
            if target.exists():
                continue
            target.write_bytes(fetch(url))
            time.sleep(1.0)
        print(oid, obj["title"], len(urls), "images", file=sys.stderr)


main()
