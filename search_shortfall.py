import os, glob, json

proj = r'c:\proyectos\SPACE LAIR'
lines = []

# 1. Check current log file size
log = os.path.join(proj, 'server_e2e_out.log')
sz = os.path.getsize(log)
lines.append(f"server_e2e_out.log size: {sz} bytes")

# 2. Search for "shortfall" in server_e2e_out.log
with open(log, 'r', encoding='utf-8', errors='replace') as f:
    content = f.read()
lines.append(f"server_e2e_out.log total chars: {len(content)}")
for i, line in enumerate(content.splitlines()):
    if 'shortfall' in line.lower():
        lines.append(f"  LINE {i}: {line[:300]}")
lines.append(f"'shortfall' found in server_e2e_out.log: {'shortfall' in content.lower()}")

# 3. Search for "image_gen" or "quality_gate" or "image_check" in the last part of the log
lines.append("\n--- Last 30 lines of log ---")
all_lines = content.splitlines()
for line in all_lines[-30:]:
    lines.append(line)

# 4. Search all .log files for shortfall
lines.append("\n--- All .log files in project root ---")
for lf in sorted(glob.glob(os.path.join(proj, '*.log')), key=os.path.getmtime, reverse=True):
    fsz = os.path.getsize(lf)
    with open(lf, 'r', encoding='utf-8', errors='replace') as f:
        txt = f.read()
    found_sf = 'shortfall' in txt.lower()
    has_image = 'image_gen' in txt
    lines.append(f"  {os.path.basename(lf)}: size={fsz}, shortfall={found_sf}, has_image_gen={has_image}")

with open(os.path.join(proj, 'shortfall_results.txt'), 'w', encoding='utf-8') as f:
    f.write('\n'.join(lines))
print("Done - results in shortfall_results.txt")

