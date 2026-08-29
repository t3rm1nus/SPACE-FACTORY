import sqlite3, os, json
proj = r'c:\proyectos\SPACE LAIR'
db = os.path.join(proj, 'data', 'space_lair.db')
conn = sqlite3.connect(db)
conn.row_factory = sqlite3.Row
# Find image_search_ratio column
cols = [r[1] for r in conn.execute("PRAGMA table_info(books)").fetchall()]
print("books columns:", cols)
r = conn.execute("SELECT id, title, image_count, image_search_ratio, layout_config FROM books WHERE id=72").fetchone()
if r:
    for k in r.keys():
        print(f"  {k}: {r[k]}")
else:
    print("NO BOOK 72")
conn.close()
