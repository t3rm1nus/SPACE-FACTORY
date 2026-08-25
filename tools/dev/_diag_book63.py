import sqlite3, json
con = sqlite3.connect('data/space_lair.db')
con.row_factory = sqlite3.Row
cur = con.cursor()
for num in [8, 9, 1, 3]:
    r = cur.execute("SELECT outline FROM chapters WHERE book_id=62 AND number=?", (num,)).fetchone()
    outline = json.loads(r['outline']) if r['outline'] else {}
    print(f"Cap {num}:")
    print(f"  outline keys: {list(outline.keys())}")
    sections = outline.get("sections", [])
    print(f"  sections count: {len(sections)}")
    print(f"  section[0]: {json.dumps(sections[0])[:100] if sections else 'None'}")
    print(f"  title/objective: {outline.get('title','')[:80]}")
    print()
con.close()







