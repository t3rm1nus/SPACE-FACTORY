import sys, os
p = sys.argv[1]
lines = open(p, encoding="utf-8", errors="replace").read().splitlines()
a = int(sys.argv[2]); b = int(sys.argv[3])
for i in range(a-1, min(b, len(lines))):
    print(f"{i+1}|{lines[i]}")