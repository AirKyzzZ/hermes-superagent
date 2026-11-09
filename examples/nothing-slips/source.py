import json
import sys
from pathlib import Path

state = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
name = sys.argv[2]
items = state["sources"][name]
if len(sys.argv) > 3:
    found = next((item for item in items if item["id"] == sys.argv[3]), {})
    print(json.dumps({"status": found.get("status")}))
else:
    shown = [item for item in items if item["status"] == "open"]
    print(json.dumps(shown[:2] if name in state["broken"] else shown))
