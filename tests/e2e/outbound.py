import sys
from pathlib import Path

path, channel, text = sys.argv[1:4]
with Path(path).open("a", encoding="utf-8") as sent:
    sent.write(f"{channel}\t{text}\n")
