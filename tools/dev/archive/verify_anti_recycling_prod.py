"""Verificación puntual (SOLO-LECTURA para producción) del fix anti-reciclaje.

Ejercicio directo de la rama de rechazo de core/autopilot.py (líneas ~609-646,
WARNING 'NO asociada ... sin anclaje temático') invocando run_job() REAL con:

  - fuente existente id=640 (https://es.wikipedia.org/wiki/Latveria),
    persistida para book_40 (capítulos 176,177,178);
  - book_id destino = 45 ("La Saga Doom: Historia del FPS que lo Cambió Todo").

Aislamiento: opera sobre una COPIA temporal de data/space_lair.db
(SPACE_LAIR_DB_PATH) y un BookJobStore en carpeta temporal. Producción queda
intacta; este script no modifica código ni datos reales.
"""

import json
import logging
import os
import shutil
import sqlite3
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROD_DB = os.path.join(ROOT, "data", "space_lair.db")

tmp = tempfile.mkdtemp(prefix="verify_antirecycl_")
db_copy = os.path.join(tmp, "space_lair_copy.db")
shutil.copy2(PROD_DB, db_copy)
os.environ["SPACE_LAIR_DB_PATH"] = db_copy
sys.path.insert(0, ROOT)

# Imports REALES (después de fijar el env para que la BD sea la copia).
from core.autopilot import (  # noqa: E402
    JOB_COMPLETED,
    BookJobStore,
    PhaseResult,
    create_job,
    run_job,
)

LATVERIA_URL = "https://es.wikipedia.org/wiki/Latveria"


class CollectHandler(logging.Handler):
    def __init__(self):
        super().__init__(level=logging.WARNING)
        self.records = []

    def emit(self, record):
        self.records.append(record.getMessage())


handler = CollectHandler()
logging.getLogger().addHandler(handler)


def executor(phase, job):
    """Stub mínimo: research propone EXACTAMENTE la fuente Latveria."""
    if phase["id"] == "research":
        return PhaseResult(
            ok=True,
            metrics={
                "status": "PASS",
                "sources": [
                    {"url": LATVERIA_URL, "title": "Latveria",
                     "source_type": "web_wikipedia", "relevance": 6},
                ],
                "source_count": 1,
                "stored_sources": [],
            },
            module="research",
        )
    return PhaseResult(ok=True, metrics={"deterministic_used": True},
                       module=phase["capability"])


store = BookJobStore(directory=os.path.join(tmp, "jobs"))
job = create_job(store, book_id=45)
print(f"job_id={job['job_id']} book_id={job['book_id']}")
run_job(job, store, executor, max_attempts=1, sleep_fn=lambda _s: None)
print(f"job_status={job['status']} (esperado {JOB_COMPLETED})")

print("\n--- WARNINGs capturados durante run_job ---")
for msg in handler.records:
    print("WARNING:", msg)

con = sqlite3.connect(db_copy)
con.row_factory = sqlite3.Row
row = con.execute(
    "select id,url,chapter_ids from sources where id=640"
).fetchone()
print("\n--- estado en BD (copia) ---")
print(dict(row))
b45 = [r[0] for r in con.execute("select id from chapters where book_id=45")]
leak = json.loads(row["chapter_ids"] or "[]")
print(f"capitulos_book_45={b45}")
print(f"contaminacion_book_45={'SI' if set(leak) & set(b45) else 'NO'}")
con.close()

shutil.rmtree(tmp, ignore_errors=True)
