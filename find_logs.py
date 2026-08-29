import os, glob, time, subprocess

proj = r'c:\proyectos\SPACE LAIR'
now = time.time()

# 1. Find all files modified in the last 60 minutes in project root (not recursing deeply)
print("=== Files modified in last 60 min (project root, 2 levels) ===")
for root, dirs, files in os.walk(proj):
    depth = root[len(proj):].count(os.sep)
    if depth > 2:
        dirs[:] = []
        continue
    dirs[:] = [d for d in dirs if d not in ('__pycache__', '.git', 'node_modules', 'venv310', 'venv', 'data')]
    for fn in files:
        fp = os.path.join(root, fn)
        try:
            m = os.path.getmtime(fp)
            if now - m < 3600:
                rel = os.path.relpath(fp, proj)
                print(f"  {rel} ({now-m:.0f}s ago, {os.path.getsize(fp)} bytes)")
        except OSError:
            pass

# 2. Also check data/ directory
print("\n=== Files modified in last 60 min (data/ dir) ===")
data_dir = os.path.join(proj, 'data')
if os.path.isdir(data_dir):
    for fn in os.listdir(data_dir):
        fp = os.path.join(data_dir, fn)
        if os.path.isfile(fp):
            try:
                m = os.path.getmtime(fp)
                if now - m < 3600:
                    print(f"  data/{fn} ({now-m:.0f}s ago, {os.path.getsize(fp)} bytes)")
            except OSError:
                pass

# 3. Try to get process command line via PowerShell
print("\n=== Process info for PID 26436 ===")
try:
    result = subprocess.run(
        ['powershell', '-NoProfile', '-Command', 
         'Get-WmiObject Win32_Process -Filter "ProcessId=26436" | Select-Object -ExpandProperty CommandLine'],
        capture_output=True, text=True, timeout=10
    )
    print(f"  stdout: {result.stdout.strip()}")
    print(f"  stderr: {result.stderr.strip()}")
except Exception as e:
    print(f"  Error: {e}")

# 4. Search for "shortfall" in ALL .log files
print("\n=== Searching all .log files for 'shortfall' ===")
for lf in sorted(glob.glob(os.path.join(proj, '*.log')), key=os.path.getmtime, reverse=True):
    with open(lf, 'r', encoding='utf-8', errors='replace') as f:
        txt = f.read()
    if 'shortfall' in txt.lower():
        print(f"  FOUND in {os.path.basename(lf)}")
        for line in txt.splitlines():
            if 'shortfall' in line.lower():
                print(f"    {line[:300]}")
    else:
        sz = os.path.getsize(lf)
        mt = os.path.getmtime(lf)
        print(f"  {os.path.basename(lf)}: {sz} bytes, modified {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(mt))}, no shortfall")

# 5. Check stdout of process via /proc equivalent on Windows
print("\n=== Checking if server_e2e_out.log was updated ===")
log = os.path.join(proj, 'server_e2e_out.log')
mt = os.path.getmtime(log)
print(f"  Last modified: {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(mt))} ({now-mt:.0f}s ago)")
print(f"  Size: {os.path.getsize(log)} bytes")

# 6. Try reading last 5 lines
with open(log, 'r', encoding='utf-8', errors='replace') as f:
    lines = f.readlines()
print(f"  Total lines: {len(lines)}")
print("  Last 3 lines:")
for line in lines[-3:]:
    print(f"    {line.rstrip()[:200]}")
