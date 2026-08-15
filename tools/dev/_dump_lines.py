"""Volcado genérico temporal de líneas (investigación). Uso: dump <relpath> <a> <b>"""
import sys, os
_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
path = sys.argv[1]
a = int(sys.argv[2]) if len(sys.argv) > 2 else 1
b = int(sys.argv[3]) if len(sys.argv) > 3 else a + 999
full = os.path.join(_ROOT, path) if not os.path.isabs(path) else path
with open(full, "r", encoding="utf-8") as f:
    lines = f.readlines()
for i in range(a - 1, min(b, len(lines))):
    print(f"{i+1:5d}| {lines[i]}", end="")
