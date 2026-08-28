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


def test_search_query_includes_language_param_for_english_book(
    tmp_path, monkeypatch
):
    """§17 #24: con language='en' la request a SearXNG incluye el param nativo
    `language=en`; con 'es' o sin language, comportamiento histórico (sin filtro).
    """
    captured: list[dict] = []

    def fake_get(url, **kwargs):
        if "/search" in url:
            captured.append(dict(kwargs.get("params") or {}))
            return _FakeResp(json_data={"query": "q", "results": _search_results(1)})
        return _FakeResp(content=_png_bytes())

    monkeypatch.setattr(image_search_main.requests, "get", fake_get)

    # EN: el param de idioma debe estar presente y ser "en"
    image_search.search_chapter_images(_payload(language="en"))
    assert captured[-1].get("language") == "en"
    assert captured[-1]["q"]  # la query sigue presente

    # ES: comportamiento histórico — SIN param de idioma
    image_search.search_chapter_images(_payload(language="es"))
    assert "language" not in captured[-1]

    # Sin language: comportamiento histórico — SIN param de idioma
    p = _payload()
    p.pop("language")
    image_search.search_chapter_images(p)
    assert "language" not in captured[-1]

    # Unit: _searxng_search sin language → sin param (default histórico)
    captured.clear()
    image_search_main._searxng_search("query de prueba")
    assert "language" not in captured[-1]
    image_search_main._searxng_search("query de prueba", language="en")
    assert captured[-1].get("language") == "en"


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


def test_topic_filter_descarta_candidato_no_anclado(tmp_path, monkeypatch):
    """§17 #11 — con topic del libro presente, un candidato sin relación temática
    (caso real tipo book_43 'historia polar': comicvine.gamespot.com) se descarta
    ANTES de descargar y NO ocupa slot, igual que el denylist."""
    results = [
        {
            "url": "https://comicvine.gamespot.com/comic-page",
            "title": "Portada de cómic de superhéroes",
            "img_src": "https://comicvine.gamespot.com/img/cover.png",
            "engine": "google images",
            "resolution": "1024x768",
        },
        {
            "url": "https://example.com/historia-polar-expedicion",
            "title": "Historia polar de las expediciones al Antártico",
            "img_src": "https://cdn.example.com/exp.png",
            "engine": "bing images",
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

    out = image_search.search_chapter_images(
        _payload(num_images=2, topic="historia polar")
    )
    validated = ImageGenerateOutput(**validate_output("generate_image", out))

    # El candidato comicvine se descarta: el único 'ok' es la imagen temática.
    ok_results = [r for r in validated.results if r.status == "ok"]
    assert len(ok_results) == 1
    assert ok_results[0].image_path.endswith("img_01_web.png")
    # La URL de comicvine NUNCA fue descargada ni aparece en results.
    assert "https://comicvine.gamespot.com/img/cover.png" not in downloaded
    assert not any("comicvine" in (r.get("source_url") or "") for r in out["results"])


def test_topic_none_no_bloquea(tmp_path, monkeypatch):
    """§17 #11 — topic ausente/None NO filtra nada: comportamiento idéntico al
    actual (libros ya generados sin topic)."""
    results = [
        {
            "url": "https://example.com/page{0}".format(i),
            "title": "Imagen {0}".format(i),
            "img_src": "https://cdn.example.com/img{0}.png".format(i),
            "engine": "bing images",
            "resolution": "64x64",
        }
        for i in range(1, 4)
    ]
    downloaded: list[str] = []

    def fake_get(url, **kwargs):
        if "/search" in url:
            return _FakeResp(json_data={"results": results})
        downloaded.append(url)
        return _FakeResp(content=_png_bytes())

    monkeypatch.setattr(image_search_main.requests, "get", fake_get)

    # Sin 'topic' en el payload (compatibilidad total).
    out = image_search.search_chapter_images(_payload(num_images=3))
    validated = ImageGenerateOutput(**validate_output("generate_image", out))

    assert validated.requested == 3
    assert validated.generated == 3
    assert validated.failed == 0
    # Las 3 imágenes legítimas se descargaron sin ningún filtrado por tema.
    assert len(downloaded) == 3
    assert len(downloaded) == 3


# ---------------------------------------------------------------------------
# §17 #28 — capabilities ES/EN nativas (_has_anchor_keyword_img + routing)
# ---------------------------------------------------------------------------
_TOPIC_ES_CAFE = "Todo sobre el café, descubrimientos, tipos, cafe en el mundo"
_TOPIC_EN_CAFE = "Everything about coffee, discoveries, types of coffee in the world"

_CAND_EN_COFFEE = {
    "title": "What are the main types of coffee drinks around the world?",
    "snippet": "https://unsplash.com/photos/a-cup-of-coffee-sitting-on-top-of-a-white-counter",
    "content": "https://images.unsplash.com/photo-coffee-cup.jpg",
}


def test_anchor_img_en_anchors_book67_candidate():
    """book_67 (§17 #28): el candidato inglés on-topic ANCLA con topic EN nativo."""
    assert (
        image_search_main._has_anchor_keyword_img(_TOPIC_EN_CAFE, _CAND_EN_COFFEE, "en")
        is True
    )


def test_anchor_img_es_no_anchora_candidato_ingles():
    """Cada idioma busca lo suyo: el mismo candidato inglés NO ancla con topic ES."""
    assert (
        image_search_main._has_anchor_keyword_img(_TOPIC_ES_CAFE, _CAND_EN_COFFEE, "es")
        is False
    )


def test_anchor_img_es_anchora_candidato_espanol():
    """Regresión variante ES: candidatos en español siguen anclando con topic ES."""
    cand_es = {
        "title": "Los principales tipos de café del mundo",
        "snippet": "https://es.wikipedia.org/wiki/Cafe",
        "content": "https://cdn.example.com/cafe-tipos-mundo.png",
    }
    assert (
        image_search_main._has_anchor_keyword_img(_TOPIC_ES_CAFE, cand_es, "es") is True
    )


def test_anchor_img_comicvine_regression_both_capabilities():
    """§17 #11 regresión con el helper propio: el candidato off-topic tipo
    comicvine.gamespot.com ('historia polar' vs 'Portada de cómic de
    superhéroes') queda descartado en AMBAS variantes; el on-topic pasa."""
    off_topic = {
        "title": "Portada de cómic de superhéroes",
        "snippet": "https://comicvine.gamespot.com/comic-page",
        "content": "https://comicvine.gamespot.com/img/cover.png",
    }
    on_topic = {
        "title": "Historia polar: expediciones al Antártico",
        "snippet": "https://example.com/historia-polar-expedicion",
        "content": "https://cdn.example.com/exp.png",
    }
    # Off-topic: descartado en ES y EN.
    assert image_search_main._has_anchor_keyword_img("historia polar", off_topic, "es") is False
    assert (
        image_search_main._has_anchor_keyword_img("polar history", off_topic, "en") is False
    )
    # On-topic ES con texto español ancla; EN exige candidato en inglés nativo.
    assert image_search_main._has_anchor_keyword_img("historia polar", on_topic, "es") is True
    on_topic_en = {
        "title": "A visual history of polar expedition gear",
        "snippet": "https://example.com/polar-history-expedition",
        "content": "https://cdn.example.com/polar-expedition.png",
    }
    assert (
        image_search_main._has_anchor_keyword_img(
            "polar history expedition", on_topic_en, "en"
        )
        is True
    )


def test_execute_routes_es_en_capabilities(tmp_path, monkeypatch):
    """Las nuevas capabilities fijan idioma nativo: query EN desde title_en y
    param language=en en SearXNG para *_en; ES mantiene comportamiento histórico."""
    captured: list[dict] = []

    def fake_get(url, **kwargs):
        if "/search" in url:
            captured.append(dict(kwargs.get("params") or {}))
            return _FakeResp(json_data={"query": "q", "results": _search_results(2)})
        return _FakeResp(content=_png_bytes())

    monkeypatch.setattr(image_search_main.requests, "get", fake_get)

    payload_en = _payload(num_images=2, title_en="The Echo Cave")
    out_en = image_search.execute(payload_en, capability="search_chapter_images_en")
    assert captured[-1].get("language") == "en"
    assert captured[-1]["q"] == "The Echo Cave"  # query nativa EN, no el título ES
    assert out_en["language"].startswith("en")
    # Shape validable; slots pueden venir como skip si el fichero ya existía
    # (storage compartido entre corridas), así que NO se exige todo "ok".
    ImageGenerateOutput(**validate_output("generate_image", out_en))
    assert len(out_en["results"]) == 2

    payload_es = _payload(num_images=2)
    out_es = image_search.execute(payload_es, capability="search_chapter_images_es")
    assert "language" not in captured[-1]  # ES: sin param de idioma (histórico)
    assert captured[-1]["q"] == "La Caverna de los Ecos"
    assert out_es["language"].startswith("es")

    # Capability legacy intacta: idioma viene del payload.
    image_search.search_chapter_images(_payload(num_images=1))


def test_module_json_registers_new_capabilities():
    import json
    from pathlib import Path

    cfg_path = Path(image_search_main.__file__).parent / "module.json"
    caps = set(json.loads(cfg_path.read_text(encoding="utf-8"))["capabilities"])
    assert {"search_chapter_images", "search_chapter_images_es", "search_chapter_images_en"} <= caps


def test_pipeline_en_anchors_and_es_discards_english_candidates(tmp_path, monkeypatch):
    """book_67 end-to-end (mock): con topic EN el candidato unsplash de café ancla;
    la variante ES con topic español lo descarta sin descargar nada."""
    results = [
        {
            "url": "https://unsplash.com/photos/a-cup-of-coffee-sitting-on-top-of-a-white-counter",
            "title": "What are the main types of coffee drinks around the world?",
            "img_src": "https://images.unsplash.com/photo-coffee-cup.png",
            "engine": "google images",
        },
        {
            "url": "https://www.pexels.com/photo/anonymous-barista-pouring-water-into-filter/",
            "title": "Anonymous barista pouring water into a paper filter",
            "img_src": "https://images.pexels.com/photos/barista-filter.png",
            "engine": "bing images",
        },
    ]
    downloaded: list[str] = []

    def fake_get(url, **kwargs):
        if "/search" in url:
            return _FakeResp(json_data={"query": "q", "results": results})
        downloaded.append(url)
        return _FakeResp(content=_png_bytes())

    monkeypatch.setattr(image_search_main.requests, "get", fake_get)

    # Variante EN: título/tema en inglés → el candidato de tipos de café ancla.
    payload_en = _payload(
        num_images=2,
        language="en",
        title_en="Coffee types around the world",
        topic_en=_TOPIC_EN_CAFE,
    )
    out_en = image_search.execute(payload_en, capability="search_chapter_images_en")
    ok_urls_en = {r["source_url"] for r in out_en["results"] if r["status"] == "ok"}
    assert ok_urls_en == {"https://images.unsplash.com/photo-coffee-cup.png"}

    # Variante ES: mismo resultado SearXNG, topic en español → nada se descarga.
    payload_es = _payload(num_images=2, topic=_TOPIC_ES_CAFE)
    out_es = image_search.execute(payload_es, capability="search_chapter_images_es")
    assert not [r for r in out_es["results"] if r["status"] == "ok"]
    # La única descarga sigue siendo la legítima de la fase EN; ES no añadió ninguna.
    assert downloaded == ["https://images.unsplash.com/photo-coffee-cup.png"]


def test_failopen_en_when_topic_en_empty_even_with_spanish_title_en(tmp_path, monkeypatch):
    """§17 #28 fix 2026-08-26 (segundo bug book_67): variante EN con
    topic_en="" y title_en con texto ESPAÑOL (fallback §17 #21) → el ancla EN
    usa SOLO topic_en, queda vacía y el filtro entra en FAIL-OPEN: el candidato
    inglés on-topic NO se descarta y se descarga con normalidad."""
    results = [
        {
            "url": "https://unsplash.com/photos/a-cup-of-coffee-sitting-on-top-of-a-white-counter",
            "title": "What are the main types of coffee drinks around the world?",
            "img_src": "https://images.unsplash.com/photo-coffee-cup.png",
            "engine": "google images",
        },
        {
            "url": "https://www.pexels.com/photo/anonymous-barista-pouring-water-into-filter/",
            "title": "Anonymous barista pouring water into a paper filter",
            "img_src": "https://images.pexels.com/photos/barista-filter.png",
            "engine": "bing images",
        },
    ]
    downloaded: list[str] = []

    def fake_get(url, **kwargs):
        if "/search" in url:
            return _FakeResp(json_data={"query": "q", "results": results})
        downloaded.append(url)
        return _FakeResp(content=_png_bytes())

    monkeypatch.setattr(image_search_main.requests, "get", fake_get)

    payload_en = _payload(
        num_images=2,
        language="en",
        topic_en="",  # sin nativo EN real (book_67: title_en/description_en NULL)
        # title_en con fallback ES de §17 #21 — NUNCA debe usarse como ancla EN:
        title_en="Todo sobre el café... - Parte 1",
    )
    out_en = image_search.execute(payload_en, capability="search_chapter_images_en")
    ok_urls_en = {r["source_url"] for r in out_en["results"] if r["status"] == "ok"}
    # Fail-open real: ambos candidatos pasan y se descargan.
    assert ok_urls_en == {
        "https://images.unsplash.com/photo-coffee-cup.png",
        "https://images.pexels.com/photos/barista-filter.png",
    }
    assert len(downloaded) == 2