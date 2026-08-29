import json, os

proj = r'c:\proyectos\SPACE LAIR'
with open(os.path.join(proj, 'data', 'autopilot', 'jobs', 'book_72.json'), encoding='utf-8') as f:
    j = json.load(f)

print("job_id:", j['job_id'])
print("status:", j['status'])
print("error:", j['error'])
print("current_phase:", j['current_phase'])
print()

# Find quality_gate phase
for p in j['phases']:
    if p['id'] == 'quality_gate':
        print("=== QUALITY GATE phase ===")
        print("status:", p['status'])
        print("error:", p.get('error'))
        print("attempts:", p.get('attempts'))
        metrics = p.get('metrics', {})
        print("metrics keys:", list(metrics.keys()) if isinstance(metrics, dict) else type(metrics))
        # Print all metrics
        for k, v in metrics.items():
            if k in ('book_checks', 'chapter_checks', 'structure_checks'):
                print(f"\n{k}:")
                for item in v:
                    if isinstance(item, dict):
                        print(f"  status={item.get('status')} origin_phase={item.get('origin_phase')} message={item.get('message')!r}")
                    else:
                        print(f"  {item!r}")
            elif k in ('overall_status',):
                print(f"{k}: {v!r}")
            else:
                print(f"{k}: {json.dumps(v, ensure_ascii=False)[:500] if isinstance(v, (dict, list)) else repr(v)}")

print("\n=== IMAGE_GEN phase ===")
for p in j['phases']:
    if p['id'] == 'image_gen':
        print("status:", p['status'])
        metrics = p.get('metrics', {})
        if 'subs' in j:
            pass
        # Print image_gen sub results
        subs = p.get('subs', {})
        if subs and isinstance(subs.get('chapters'), dict):
            for cid, sub in subs['chapters'].items():
                print(f"  chapter {cid}: status={sub.get('status')}")
                sm = sub.get('metrics', {})
                if isinstance(sm, dict) and 'results' in sm:
                    results = sm['results']
                    ok = sum(1 for r in results if r.get('status') == 'ok')
                    print(f"    image results: {len(results)}, ok={ok}")

print("\n=== DOCX phase ===")
for p in j['phases']:
    if p['id'] == 'docx':
        print("status:", p['status'])
        print("error:", p.get('error'))
        print("attempts:", p.get('attempts'))

print("\n=== Job data ===")
print("data:", json.dumps(j.get('data', {}), ensure_ascii=False)[:500])
