import json, os, sys, time, urllib.request, urllib.error

proj = r'c:\proyectos\SPACE LAIR'
BASE = "http://127.0.0.1:8080"
LOG = os.path.join(proj, "server_e2e_out.log")

def log_size():
    try:
        return os.path.getsize(LOG)
    except OSError:
        return 0

def read_log_since(size_before):
    """Lee el log desde size_before hasta el final."""
    try:
        sz = os.path.getsize(LOG)
        if sz <= size_before:
            return ""
        with open(LOG, 'r', encoding='utf-8', errors='replace') as f:
            f.seek(size_before)
            return f.read()
    except Exception as e:
        return f"[error reading log: {e}]"

def get_json(url):
    req = urllib.request.Request(url, method='GET')
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode('utf-8'))

def post_json(url, body):
    data = json.dumps(body).encode('utf-8')
    req = urllib.request.Request(url, data=data, method='POST')
    req.add_header('Content-Type', 'application/json')
    with urllib.request.urlopen(req, timeout=120) as resp:
        return json.loads(resp.read().decode('utf-8'))

# 1. Estado previo al reset
print("=" * 60)
print("PASO 0: Estado previo al reset")
print("=" * 60)
size_before = log_size()
print(f"Log size antes del reset: {size_before} bytes")

# Verificar job actual
job = get_json(f"{BASE}/api/books/72/autopilot")
print(f"Status actual del job: {job.get('status')}")
print(f"Current phase: {job.get('current_phase')}")
print(f"Error actual: {job.get('error')}")

# Verificar image_search_ratio en BD
import sqlite3
conn = sqlite3.connect(os.path.join(proj, 'data', 'space_lair.db'))
conn.row_factory = sqlite3.Row
r = conn.execute("SELECT id, title, image_count, image_search_ratio FROM books WHERE id=72").fetchone()
if r:
    print(f"BD book 72: image_count={r['image_count']}, image_search_ratio={r['image_search_ratio']}")
conn.close()

# 2. POST reset
print("\n" + "=" * 60)
print("PASO 1: POST /api/books/72/autopilot/reset")
print("=" * 60)
print("Body: {\"from_phase\": \"image_gen\"}")
reset_result = post_json(f"{BASE}/api/books/72/autopilot/reset", {"from_phase": "image_gen"})
print(f"Status del job tras reset: {reset_result.get('status')}")
print(f"Current phase tras reset: {reset_result.get('current_phase')}")

# Mostrar fases tras reset
for p in reset_result.get('phases', []):
    print(f"  {p['id']}: {p['status']}")

# 3. Polling
print("\n" + "=" * 60)
print("PASO 2: Polling GET /api/books/72/autopilot")
print("=" * 60)
max_polls = 480  # 480 * 10s = 80 min
poll_interval = 10
final_status = None
for i in range(max_polls):
    time.sleep(poll_interval)
    job = get_json(f"{BASE}/api/books/72/autopilot")
    status = job.get('status')
    current = job.get('current_phase')
    print(f"[{i+1}] poll: status={status} current_phase={current}", flush=True)
    if status in ('COMPLETED', 'FAILED'):
        final_status = status
        break
else:
    print("TIMEOUT: job no termino en el tiempo esperado")
    final_status = "TIMEOUT"

# 4. Análisis post-ejecución
print("\n" + "=" * 60)
print("PASO 3: Análisis post-ejecución")
print("=" * 60)
job = get_json(f"{BASE}/api/books/72/autopilot")
print(f"Status final: {job.get('status')}")
print(f"Current phase: {job.get('current_phase')}")
print(f"Error: {job.get('error')}")
print(f"docx_path: {job.get('docx_path')}")

# Mostrar fases finales
for p in job.get('phases', []):
    print(f"  {p['id']}: status={p['status']} attempts={p.get('attempts',0)} error={p.get('error')}")

# 5. Buscar "shortfall" en logs nuevos
print("\n--- Log lines with 'shortfall' (esta ejecución) ---")
new_log = read_log_since(size_before)
shortfall_lines = [l for l in new_log.splitlines() if 'shortfall' in l.lower()]
if shortfall_lines:
    for l in shortfall_lines:
        print(l)
else:
    print("(ninguna)")

# 6. Mensaje del check de imágenes en quality_gate
print("\n--- Quality gate: image check ---")
qc_phase = None
for p in job.get('phases', []):
    if p['id'] == 'quality_gate':
        qc_phase = p
        break
if qc_phase:
    metrics = qc_phase.get('metrics', {})
    print(f"quality_gate status: {qc_phase.get('status')}")
    print(f"quality_gate error: {qc_phase.get('error')}")
    book_checks = metrics.get('book_checks', [])
    for chk in book_checks:
        origin = chk.get('origin_phase', '?')
        status = chk.get('status', '?')
        msg = chk.get('message', '')
        print(f"  check [{origin}]: status={status} message={msg!r}")
else:
    print("quality_gate phase not found")

# 7. Verificar DOCX
print("\n--- DOCX verification ---")
docx_path = job.get('docx_path')
if final_status == 'COMPLETED' and docx_path:
    if os.path.isfile(docx_path):
        size = os.path.getsize(docx_path)
        print(f"docx exists: {docx_path}")
        print(f"docx size: {size} bytes")
    else:
        print(f"docx_path en job: {docx_path}")
        print(f"docx NO existe en disco")
else:
    # Try to find docx file
    docx_dir = os.path.join(proj, 'output', 'docx')
    candidates = []
    if os.path.isdir(docx_dir):
        for name in os.listdir(docx_dir):
            if name.startswith(f'book_72_') and name.endswith('.docx'):
                candidates.append(os.path.join(docx_dir, name))
    if candidates:
        for c in candidates:
            print(f"docx found: {c} ({os.path.getsize(c)} bytes)")
    else:
        print("No docx file found for book 72")

print("\n" + "=" * 60)
print(f"ESTADO FINAL: {final_status}")
print("=" * 60)
