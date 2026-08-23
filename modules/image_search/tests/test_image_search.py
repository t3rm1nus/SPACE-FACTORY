"""Tests del módulo image_search (sin depender de un SearXNG real).

Mockea todas las llamadas HTTP (requests.get) para cubrir:
  - éxito completo
  - SearXNG no responde (timeout)
  - 0 resultados
  - fallo de descarga de una imagen (debe seguir con las demás)
"""

import io
import json
import os

import pytest
from PIL import Image

from core.schemas import ImageGenerateOutput, validate_output
from modules import image_search
from modules.image_search import main as image_search_main


@pytest.fixture(autouse=True)
def _isolate_fs(tmp_path, monkeypatch):
    monkeypatch.setenv("IMAGE_STORAGE_ROOT", str(tmp_path))
    monkeypatch.setattr(image_search_main, "SEARXNG_URL", "http://searxng.test:8081")
    yield


class _FakeResp:
    """Respuesta HTTP simulada mínima."""

    def __init__(self, content=None, json_data=None):
        self.content = content
        self._json = json_data

    def raise_for_status(self):
        return None

    def json(self):
        if self._json is None:
            raise ValueError("sin cuerpo JSON")
        return self._json


class _HttpError(Exception):
    """Simula un error/tiempo de espera en red."""


def _png_bytes(width=64, height=64):
    buf = io.BytesIO()
    Image.new("RGB", (width, height), (200, 30, 30)).save(buf, format="PNG")
    return buf.getvalue()


def _search_results(n, start=1):
    return [
        {
            "url": f"https://example.com/page{i}",
            "title": f"Imagen {i}",
            "img_src": f"https://cdn.example.com/img{i}.png",
            "thumbnail_src": f"https://cdn.example.com/thumb{i}.png",
            "engine": ["bing images", "google images"],
            "resolution": "64x64",
        }
        for i in range(start, start + n)
    ]


def _mock_requests(monkeypatch, results=None, search_error=None, download_errors=()):
    """Instala un fake de requests.get controlado por los parámetros."""
    download_errors = set(download_errors)

    def fake_get(url, **kwargs):
        if "/search" in url:
            if search_error is not None:
                raise search_error
            return _FakeResp(json_data={"query": "q", "results": results or []})
        if url in download_errors:
            raise _HttpError("connection error")
        return _FakeResp(content=_png_bytes())

    monkeypatch.setattr(image_search_main.requests, "get", fake_get)


def _payload(**overrides):
    payload = {
        "book_id": 1,
        "chapter_number": 2,
        "chapter_title": "La Caverna de los Ecos",
        "chapter_text": "Un largo texto del capítulo para ilustrar la escena.",
        "num_images": 3,
        "language": "es",
    }
    payload.update(overrides)
    return payload


def test_ok_completo(tmp_path, monkeypatch):
    _mock_requests(monkeypatch, results=_search_results(3))
    out = image_search.search_chapter_images(_payload(num_images=3))
    # El shape es plug-compatible con image_generator (validate_output pasa).
    validated = ImageGenerateOutput(**validate_output("generate_image", out))

    assert validated.requested == 3
    assert validated.generated == 3
    assert validated.failed == 0
    assert validated.skipped == 0
    assert len(validated.results) == 3
    # Mismo patrón de ruta que image_generator.
    assert validated.images_dir == str(tmp_path / "books" / "1" / "chapters" / "2" / "images")
    assert all(r.status == "ok" for r in validated.results)
    assert all(os.path.isfile(r.image_path) for r in validated.results)

    # Metadatos por imagen con trazabilidad web (en el out crudo; validate_output
    # descarta los campos extra al hacer model_dump, por lo que la inspección
    # de trazabilidad se hace sobre la respuesta real del módulo).
    first = out["results"][0]
    assert first["provider"] == "searxng"
    assert first["source_type"] == "web_search"
    assert first["source_url"] == "https://cdn.example.com/img1.png"
    assert "bing images" in first["engine"]
    assert first["resolution"] == "64x64"
    assert first["license"] is None  # explícito, sin inventar

    metadata_path = os.path.join(out["images_dir"], f"{first['image_id']}.metadata.json")
    assert os.path.isfile(metadata_path)
    with open(metadata_path, "r", encoding="utf-8") as f:
        md = json.load(f)
    assert md["source_type"] == "web_search"
    assert md["source_url"] == "https://cdn.example.com/img1.png"
    assert md["license"] is None
    assert md["status"] == "ok"


def test_searxng_timeout_devuelve_error_sin_excepcion(tmp_path, monkeypatch):
    _mock_requests(monkeypatch, search_error=_HttpError("timeout"))
    out = image_search.search_chapter_images(_payload(num_images=3))
    validated = ImageGenerateOutput(**validate_output("generate_image", out))

    assert validated.requested == 3
    assert validated.generated == 0
    assert validated.failed == 3
    assert validated.skipped == 0
    assert all(r.status == "error" for r in validated.results)
    # El directorio se crea (igual que image_generator) pero NO se escribe ningún
    # archivo de imagen ni de metadatos.
    assert os.listdir(validated.images_dir) == []


def test_cero_resultados(tmp_path, monkeypatch):
    _mock_requests(monkeypatch, results=[])
    out = image_search.search_chapter_images(_payload(num_images=2))
    validated = ImageGenerateOutput(**validate_output("generate_image", out))

    assert validated.requested == 2
    assert validated.generated == 0
    assert validated.failed == 2
    assert all(r.status == "error" for r in validated.results)


def test_descarga_falla_pero_sigue_con_las_demas(tmp_path, monkeypatch):
    results = _search_results(3)
    # La primera descarga falla; el resto debe completarse.
    _mock_requests(
        monkeypatch,
        results=results,
        download_errors={"https://cdn.example.com/img1.png"},
    )
    out = image_search.search_chapter_images(_payload(num_images=3))
    validated = ImageGenerateOutput(**validate_output("generate_image", out))

    assert validated.requested == 3
    assert validated.generated == 2
    assert validated.failed == 1
    assert validated.results[0].status == "error"
    assert validated.results[0].error == "download_failed"
    assert validated.results[1].status == "ok"
    assert validated.results[2].status == "ok"
    assert os.path.isfile(validated.results[1].image_path)
    assert os.path.isfile(validated.results[2].image_path)


def test_fallback_query_desde_texto_cuando_no_hay_titulo(monkeypatch):
    captured = {}

    def fake_get(url, **kwargs):
        if "/search" in url:
            captured["params"] = kwargs.get("params")
            return _FakeResp(json_data={"results": []})
        return _FakeResp(content=_png_bytes())

    monkeypatch.setattr(image_search_main.requests, "get", fake_get)
    image_search.search_chapter_images(
        _payload(chapter_title=None, chapter_text="Hola mundo esto es el texto")
    )
    assert "Hola" in captured["params"]["q"]
    assert "mundo" in captured["params"]["q"]
    assert captured["params"]["categories"] == "images"
    assert captured["params"]["format"] == "json"


def test_download_invalid_image_content_marks_error(tmp_path, monkeypatch):
    """Contenido descargado no-imagen (ej. HTML de error) -> status=error, sin escribir .png.

    Regresión del bug real book_id=31: 5 archivos ~4KB/430B persistidos con
    status="ok" que PIL no podía abrir (probablemente HTML de error o contenido
    truncado). El fix valida los bytes descargados con PIL ANTES de escribir el
    archivo ni de marcar status="ok".
    """
    results = _search_results(1)

    def fake_get(url, **kwargs):
        if "/search" in url:
            return _FakeResp(json_data={"results": results})
        # Descarga devuelve contenido NO-imagen (HTML de error), no una imagen válida.
        return _FakeResp(content=b"<html>error</html>")

    monkeypatch.setattr(image_search_main.requests, "get", fake_get)
    out = image_search.search_chapter_images(_payload(num_images=1))
    validated = ImageGenerateOutput(**validate_output("generate_image", out))

    # (a) status=error (no "ok")
    assert validated.requested == 1
    assert validated.generated == 0
    assert validated.failed == 1
    assert validated.skipped == 0
    assert validated.results[0].status == "error"
    assert "invalid image content" in (validated.results[0].error or "")

    # (b) NO se escribe ningún archivo .png en el directorio de destino.
    assert os.listdir(validated.images_dir) == []


    # (c) la función no lanza excepción — al retornar (y validar shape) queda confirmado.


def test_denylist_bloquea_dominio_y_no_ocupa_slot(tmp_path, monkeypatch):
    """§17 #5 — denylist de dominios: un resultado con img_src en scribd.com
    (editorial/repositorio con copyright) se descarta SIN ser descargado ni
    ocupar slot; un resultado legítimo se procesa normalmente.

    - requested == generated + failed (consistencia de conteos).
    - solo la imagen legítima aparece en results.
    - _download_image NUNCA se llama con la URL denylisted.
    """
    # Item 1: página fuente denylisted vía `url`; item 2: legítimo.
    results = [
        {
            "url": "https://www.scribd.com/document/12345/portada-libro",
            "title": "Portada editorial sospechosa",
            "img_src": "https://cdn.scribd.com/img/12345.png",
            "engine": "bing images",
            "resolution": "1024x768",
        },
        {
            "url": "https://example.com/page/ilustracion",
            "title": "Imagen ilustración capítulo",
            "img_src": "https://cdn.example.com/img2.png",
            "engine": "google images",
            "resolution": "64x64",
        },
    ]

    downloaded: list[str] = []

    def fake_get(url, **kwargs):
        if "/search" in url:
            return _FakeResp(json_data={"results": results})
        downloaded.append(url)
        return _FakeResp(content=_png_bytes())

    monkeypatch.setattr(image_search_main.requests, "get", fake_get)

    out = image_search.search_chapter_images(_payload(num_images=2))
    validated = ImageGenerateOutput(**validate_output("generate_image", out))

    # Consistencia de conteos: el denylisted NO ocupa slot.
    assert validated.requested == 2
    assert validated.generated == 1
    assert validated.failed == 1
    assert validated.skipped == 0

    # Solo la imagen legítima está en results: el resultado denylisted se
    # descarta sin ocupar slot, por lo que el único "ok" es la imagen legítima
    # (slot 1) y el slot 2 (sin más resultados) queda en error "no_results".
    assert len(validated.results) == 2
    assert validated.results[0].status == "ok"
    assert validated.results[1].status == "error"  # slot 2 sin más resultados útiles
    assert validated.results[1].error == "no_results"
    ok_results = [r for r in validated.results if r.status == "ok"]
    assert len(ok_results) == 1
    assert ok_results[0].image_path.endswith("img_01_web.png")
    # source_url es un campo "extra" (ImageMetadata lo ignora al validar); se
    # verifica sobre el dict raw que sí lo conserva.
    raw_ok = [r for r in out["results"] if r.get("status") == "ok"][0]
    assert raw_ok["source_url"] == "https://cdn.example.com/img2.png"
    # El resultado denylisted no aparece en results en absoluto (ni como ok ni como error).
    assert not any("scribd.com" in (r.get("source_url") or "") for r in out["results"])

    # La URL denylisted (img_src ni página fuente) nunca fue descargada.
    assert "https://cdn.scribd.com/img/12345.png" not in downloaded
    assert "https://www.scribd.com/document/12345/portada-libro" not in downloaded
    # La única descarga es la imagen legítima.
    assert downloaded == ["https://cdn.example.com/img2.png"]
