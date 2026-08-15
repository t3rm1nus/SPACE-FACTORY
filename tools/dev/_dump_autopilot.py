"""Volcado temporal de líneas del motor Autopilot (investigación)."""
import sys
path = "tools/dev/autopilot.py"
with open(path, "r", encoding="utf-8") as f:
    lines = f.readlines()
a = int(sys.argv[1]) if len(sys.argv) > 1 else 92
b = int(sys.argv[2]) if len(sys.argv) > 2 else 479
for i in range(a - 1, min(b, len(lines))):
    print(f"{i+1:5d}| {lines[i]}", end="")
