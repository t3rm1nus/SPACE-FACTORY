import json, os
proj = r'c:\proyectos\SPACE LAIR'
with open(os.path.join(proj, 'data', 'autopilot', 'jobs', 'book_72.json'), encoding='utf-8') as f:
    j = json.load(f)
print('status:', j['status'])
print('updated_at:', j['updated_at'])
print('current_phase:', j.get('current_phase'))
print('error:', j.get('error'))
data = j.get('data', {})
print('image_search_ratio:', data.get('image_search_ratio', 'N/A'))
print('num_images:', data.get('num_images', 'N/A'))
for p in j.get('phases', []):
    print(f'  phase {p["id"]}: status={p["status"]} attempts={p.get("attempts",0)} error={p.get("error",None)}')
    subs = p.get('subs')
    if subs and isinstance(subs, dict) and isinstance(subs.get('chapters'), dict):
        for cid, sub in subs['chapters'].items():
            print(f'    sub chapter {cid}: status={sub.get("status")} attempts={sub.get("attempts",0)}')