"""Medición Fase 4 (VLM): latencia moondream-local + prueba de contención.

Protocolo §11: read-only diagnosis. No toca código de producción.
Uso: python tools/measure_vlm_latency.py
"""
import base64
import json
import time
import urllib.request
from pathlib import Path

BASE = "http://localhost:11434"
MODEL = "moondream-local"
IMAGES = [
    Path(r"data\images\books\40\chapters\1\images\img_01_web.png"),
    Path(r"data\images\books\40\chapters\1\images\img_02_web.jpg"),
    Path(r"data\images\books\40\chapters\2\images\img_01_web.jpg"),
]
TOPIC = "El Nacimiento del Doom (Doom 1993)"
PROMPT = f"¿Es esta imagen relevante para el tema {TOPIC}? Responde sí/no y una frase breve."


def generate(model, prompt, images_b64=None, timeout=600, num_predict=64):
    payload = {"model": model, "prompt": prompt, "stream": False,
               "options": {"num_predict": num_predict}}
    if images_b64:
        payload["images"] = images_b64
    req = urllib.request.Request(
        f"{BASE}/api/generate",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"})
    t0 = time.perf_counter()
    with urllib.request.urlopen(req, timeout=timeout) as r:
        body = json.loads(r.read())
    dt = time.perf_counter() - t0
    return dt, body


def ollama_ps():
    try:
        with urllib.request.urlopen(f"{BASE}/api/ps", timeout=10) as r:
            return json.loads(r.read())
    except Exception as e:
        return {"error": str(e)}


def main():
    print("== Estado inicial ==")
    print(json.dumps(ollama_ps(), indent=2, ensure_ascii=False))

    results = []
    for i, p in enumerate(IMAGES, 1):
        b64 = base64.b64encode(p.read_bytes()).decode()
        dt, body = generate(MODEL, PROMPT, [b64])
        results.append((i, str(p), dt, body.get("response", "")[:200],
                        body.get("total_duration", 0) / 1e9,
                        body.get("eval_count", 0)))
        print(f"\n-- Inferencia {i} ({p.name}): {dt:.2f}s "
              f"(ollama total {results[-1][4]:.2f}s, tokens {results[-1][5]})")
        print(f"   Respuesta: {results[-1][3]!r}")

    print("\n== Resumen Paso 3 ==")
    for i, p, dt, resp, tot, tok in results:
        print(f"  #{i} {p}: {dt:.2f}s | {tok} tokens")

    print("\n== Paso 4: contención (qwen2.5-coder:7b en paralelo) ==")
    import threading
    qwen_out = {}

    def qwen_job():
        t = time.perf_counter()
        _, b = generate("qwen2.5-coder:7b",
                        "Escribe 3 frases sobre la historia de los videojuegos.",
                        timeout=900, num_predict=128)
        qwen_out["dt"] = time.perf_counter() - t
        qwen_out["resp"] = b.get("response", "")[:150]
        qwen_out["eval_count"] = b.get("eval_count", 0)

    th = threading.Thread(target=qwen_job)
    th.start()
    time.sleep(0.5)  # deja que qwen cargue primero
    b64 = base64.b64encode(IMAGES[0].read_bytes()).decode()
    dt4, _ = generate(MODEL, PROMPT, [b64])
    th.join()
    print(f"  4ª inferencia moondream (con qwen cargando/generando): {dt4:.2f}s")
    print(f"  qwen2.5-coder:7b total: {qwen_out.get('dt', 0):.2f}s "
          f"({qwen_out.get('eval_count', 0)} tokens)")

    print("\n== Estado final (ps) ==")
    print(json.dumps(ollama_ps(), indent=2, ensure_ascii=False))

    # Guardar evidencia
    out = {"step3": [{"n": i, "image": p, "wall_s": dt, "tokens": tok}
                     for i, p, dt, resp, tot, tok in results],
           "step4": {"moondream_s": dt4,
                     "qwen_total_s": qwen_out.get("dt"),
                     "qwen_tokens": qwen_out.get("eval_count")}}
    Path("data/dev_ops/vlm_latency_report.json").write_text(
        json.dumps(out, indent=2, ensure_ascii=False), encoding="utf-8")
    print("\nEvidencia guardada en data/dev_ops/vlm_latency_report.json")


if __name__ == "__main__":
    main()
