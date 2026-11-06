import json
import sys
from pathlib import Path

tasks = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
if len(sys.argv) > 2:
    found = next((task for task in tasks["tasks"] if task["id"] == sys.argv[2]), {})
    print(json.dumps({"status": found.get("status")}))
else:
    shown = [task for task in tasks["tasks"] if task["status"] == "open"]
    print(json.dumps(shown[:2] if tasks.get("broken") else shown))
