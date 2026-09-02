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
import requests
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


def _png_bytes(width=800, height=600):
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
    assert first["resolution"] == "800x600"
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


def test_dedupe_cross_chapter_por_hash(tmp_path, monkeypatch):
    """§17 #30 (P1a, book_72): la misma imagen física (mismos bytes) ya usada
    en OTRO capítulo del mismo libro se descarta SIN consumir slot de error y
    el slot sigue con el siguiente candidato. El mismo contenido en el PROPIO
    capítulo se permite (re-ejecución/overwrite)."""
    same = _png_bytes(640, 480)   # 4:3, dentro del quality check
    other = _png_bytes(800, 533)  # contenido distinto (bytes distintos), ~3:2

    def fake_get(url, **kwargs):
        if "/search" in url:
            return _FakeResp(json_data={"results": _search_results(2)})
        return _FakeResp(content=same if url.endswith("img1.png") else other)

    monkeypatch.setattr(image_search_main.requests, "get", fake_get)

    # Capítulo 1: descarga img1.png (contenido A). El registro queda con 1 hash.
    out1 = image_search.search_chapter_images(_payload(chapter_number=1, num_images=1))
    v1 = ImageGenerateOutput(**validate_output("generate_image", out1))
    assert v1.generated == 1 and v1.failed == 0

    # Capítulo 2: primer candidato es el MISMO contenido A → descartado sin
    # error ni slot; el segundo candidato (contenido B) ocupa el slot.
    out2 = image_search.search_chapter_images(_payload(chapter_number=2, num_images=1))
    v2 = ImageGenerateOutput(**validate_output("generate_image", out2))
    assert v2.generated == 1
    assert v2.failed == 0  # el duplicado NO consumió slot de error
    assert v2.results[0].status == "ok"
    assert "chapters" + os.sep + "2" + os.sep in v2.results[0].image_path

    # Registro del libro: 2 contenidos únicos (A del cap 1, B del cap 2).
    registry = image_search_main._load_content_hashes(1)
    assert len(registry) == 2

    # Re-ejecución del PROPIO capítulo 1 con el mismo contenido: permitida.
    out1b = image_search.search_chapter_images(_payload(chapter_number=1, num_images=1))
    v1b = ImageGenerateOutput(**validate_output("generate_image", out1b))
    assert v1b.generated == 1 and v1b.failed == 0


def test_query_prefiere_chapter_search_topic(tmp_path, monkeypatch):
    """§17 #30 (P1b): con chapter_search_topic en el payload (heading del
    outline del capítulo), la query a SearXNG usa ESE tema en vez del título
    genérico; sin el campo, cae al comportamiento histórico (chapter_title)."""
    captured: list[dict] = []

    def fake_get(url, **kwargs):
        if "/search" in url:
            captured.append(dict(kwargs.get("params") or {}))
        return _FakeResp(json_data={"results": []})

    monkeypatch.setattr(image_search_main.requests, "get", fake_get)

    image_search.search_chapter_images(
        _payload(num_images=1, chapter_search_topic="La era del realidad virtual (2016)")
    )
    assert captured and "La era del realidad virtual (2016)" in captured[0]["q"]

    captured.clear()
    image_search.search_chapter_images(_payload(num_images=1))
    assert captured and "La Caverna de los Ecos" in captured[0]["q"]


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


def test_icon_library_denylist_bloquea_jsdelivr(tmp_path, monkeypatch):
    """Denylist de librerías de iconos: una URL de cdn.jsdelivr.net (devicons,
    lucide-static, etc. — SVG de librerías dev) se descarta por denylist ANTES
    de evaluarse como no-raster y SIN descargarse ni ocupar slot de intento
    (evidencia real: book_72, déficit de imágenes por slots consumidos por
    iconos de cdn.jsdelivr.net)."""
    results = [
        {
            "url": "https://github.com/devicons/devicons",
            "title": "Devicon",
            "img_src": "https://cdn.jsdelivr.net/gh/devicons/devicon/icons/python/python-original.svg",
            "engine": "bing images",
            "resolution": "512x512",
        },
        {
            "url": "https://example.com/page/ilustracion",
            "title": "Imagen ilustración capítulo",
            "img_src": "https://cdn.example.com/img_ok.png",
            "engine": "google images",
            "resolution": "1024x768",
        },
    ]

    non_raster_checked: list[str] = []
    downloaded: list[str] = []
    real_is_non_raster = image_search_main._is_non_raster

    def spy_non_raster(url):
        non_raster_checked.append(url)
        return real_is_non_raster(url)

    def fake_get(url, **kwargs):
        if "/search" in url:
            return _FakeResp(json_data={"results": results})
        downloaded.append(url)
        return _FakeResp(content=_png_bytes())

    monkeypatch.setattr(image_search_main.requests, "get", fake_get)
    monkeypatch.setattr(image_search_main, "_is_non_raster", spy_non_raster)

    out = image_search.search_chapter_images(_payload(num_images=2))
    validated = ImageGenerateOutput(**validate_output("generate_image", out))

    # El resultado de jsdelivr NO aparece en results (ni ok ni error): descartado
    # por denylist, sin ocupar slot.
    assert not any(
        "jsdelivr" in (r.get("source_url") or "") for r in out["results"]
    )
    # El slot 1 lo ocupa la imagen legítima; el slot 2 queda sin resultados.
    assert validated.results[0].status == "ok"
    assert validated.results[0].image_path.endswith("img_01_web.png")
    assert validated.results[1].status == "error"
    assert validated.results[1].error == "no_results"

    # Descartado por denylist ANTES del check no-raster y de la descarga.
    assert not any("jsdelivr" in u for u in non_raster_checked)
    assert not any("jsdelivr" in u for u in downloaded)


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


# ---------------------------------------------------------------------------
# §17 #30 — paginación con presupuesto (pageno, budget, techo de páginas)
# ---------------------------------------------------------------------------

def test_paginacion_rellena_cupo_con_pagina_2(tmp_path, monkeypatch, caplog):
    """§17 #30: si la página 1 no alcanza el cupo, se pide la página 2 con el
    param nativo `pageno` y los candidatos nuevos rellenan los slots restantes
    (no se rinde en el primer lote)."""
    captured: list[dict] = []

    def fake_get(url, **kwargs):
        if "/search" in url:
            params = dict(kwargs.get("params") or {})
            captured.append(params)
            if params.get("pageno") == "1":
                return _FakeResp(json_data={"results": _search_results(1, start=1)})
            return _FakeResp(json_data={"results": _search_results(1, start=2)})
        return _FakeResp(content=_png_bytes())

    monkeypatch.setattr(image_search_main.requests, "get", fake_get)

    out = image_search.search_chapter_images(_payload(num_images=2))
    validated = ImageGenerateOutput(**validate_output("generate_image", out))

    # Dos llamadas a SearXNG: página 1 y página 2.
    assert [p.get("pageno") for p in captured] == ["1", "2"]
    assert validated.requested == 2
    assert validated.generated == 2
    assert validated.failed == 0
    urls = {r["source_url"] for r in out["results"] if r["status"] == "ok"}
    assert urls == {
        "https://cdn.example.com/img1.png",
        "https://cdn.example.com/img2.png",
    }


def test_paginacion_corta_cuando_pagina_no_aporta_nuevos(tmp_path, monkeypatch):
    """§17 #30: si una página solo devuelve candidatos ya vistos (dedupe por
    img_src/url de página), no hay más resultados → se corta sin bucle infinito
    y el cupo pendiente queda como error no_results."""
    captured: list[dict] = []

    def fake_get(url, **kwargs):
        if "/search" in url:
            captured.append(dict(kwargs.get("params") or {}))
            return _FakeResp(json_data={"results": _search_results(1, start=1)})
        return _FakeResp(content=_png_bytes())

    monkeypatch.setattr(image_search_main.requests, "get", fake_get)

    out = image_search.search_chapter_images(_payload(num_images=3))
    validated = ImageGenerateOutput(**validate_output("generate_image", out))

    # Página 1 aporta 1 candidato; página 2 repite el mismo → fin.
    assert [p.get("pageno") for p in captured] == ["1", "2"]
    assert validated.requested == 3
    assert validated.generated == 1
    assert validated.failed == 2
    assert [r.error for r in validated.results if r.status == "error"] == [
        "no_results",
        "no_results",
    ]


def test_paginacion_respeta_techo_de_paginas(tmp_path, monkeypatch):
    """§17 #30: con candidatos nuevos en cada página pero cupo inalcanzable, el
    bucle corta al llegar a IMAGE_SEARCH_MAX_PAGES (techo duro)."""
    monkeypatch.setattr(image_search_main, "IMAGE_SEARCH_MAX_PAGES", 2)
    captured: list[dict] = []

    def fake_get(url, **kwargs):
        if "/search" in url:
            params = dict(kwargs.get("params") or {})
            captured.append(params)
            n = int(params.get("pageno") or "1")
            # Cada página trae un candidato NUEVO (start=n) → nunca se corta
            # por 'sin nuevos', solo puede cortar el techo de páginas.
            return _FakeResp(json_data={"results": _search_results(1, start=n)})
        return _FakeResp(content=_png_bytes())

    monkeypatch.setattr(image_search_main.requests, "get", fake_get)

    out = image_search.search_chapter_images(_payload(num_images=5))
    validated = ImageGenerateOutput(**validate_output("generate_image", out))

    # Solo 2 páginas (techo=2), cada una con su pageno secuencial.
    assert [p.get("pageno") for p in captured] == ["1", "2"]
    assert validated.generated == 2
    assert validated.requested == 5
    assert validated.failed == 3


def test_presupuesto_agota_a_mitad_de_pagina_corta_limpio(tmp_path, monkeypatch, caplog):
    """§17 #30 — bug paginación: el presupuesto solo se comprobaba entre páginas,
    no dentro del loop de candidatos. Si una página trae varios candidatos y el
    presupuesto se agota a mitad, el bucle debe cortar limpio (no esperar a
    procesar todos los candidatos restantes de esa página) y pasar directo al
    relleno final con `no_results`.

    Simulamos un reloj que hace avanzar el tiempo tras el primer candidato, de
    modo que el chequeo ANTES del 2º candidato ya supera el budget.
    """
    monkeypatch.setattr(image_search_main, "IMAGE_SEARCH_TOTAL_TIME_BUDGET", 300.0)
    # Página con 3 candidatos válidos (.png, ejemplo.com no denylisted).
    results = _search_results(3)
    downloaded: list[str] = []

    # Reloj artificial: [start, while-check(no corta), cand1-check(procesa),
    # cand2-check(agota)]; tras agotar la lista, se queda en el último valor.
    clock_vals = [0.0, 0.0, 100.0, 400.0]
    _idx = {"i": 0}

    def fake_monotonic():
        i = _idx["i"]
        _idx["i"] += 1
        if i < len(clock_vals):
            return clock_vals[i]
        return clock_vals[-1]

    monkeypatch.setattr(image_search_main.time, "monotonic", fake_monotonic)

    def fake_get(url, **kwargs):
        if "/search" in url:
            return _FakeResp(json_data={"query": "q", "results": results})
        downloaded.append(url)
        return _FakeResp(content=_png_bytes())

    monkeypatch.setattr(image_search_main.requests, "get", fake_get)

    out = image_search.search_chapter_images(_payload(num_images=3))
    validated = ImageGenerateOutput(**validate_output("generate_image", out))

    # Solo 1 imagen descargada (el 2º candidato se corta ANTES de descargar y el
    # 3º nunca se procesa). Sin el fix, se habrían intentado procesar los 3.
    assert len(downloaded) == 1
    assert validated.requested == 3
    assert validated.generated == 1
    assert validated.failed == 2
    # Slots no cubiertos rellenados con no_results (no error parcial de descarga).
    assert [r.error for r in validated.results if r.status == "error"] == [
        "no_results",
        "no_results",
    ]
    # Log de corte a mitad de página presente.
    assert any("a mitad de página" in r.message for r in caplog.records)


def test_descarta_svg_sin_descargar_ni_gastar_http(tmp_path, monkeypatch):
    """§17 #30 — antes de _download_image se descarta por extensión .svg (y
    cualquiera de _NON_RASTER_EXTENSIONS) SIN hacer la petición HTTP. La URL
    .svg queda como error no-raster y NUNCA aparece en la lista de descargas.

    Nota (denylist de librerías de iconos): cdn.jsdelivr.net ya se descarta
    ANTES, por _ICON_LIBRARY_DENYLIST (ver
    test_icon_library_denylist_bloquea_jsdelivr); aquí se usa un dominio NO
    denylisted para ejercitar específicamente la ruta no-raster.
    """
    results = [
        {
            "url": "https://example.com/svg-page",
            "title": "Icono",
            "img_src": "https://icons.example.com/lucide-static/icons/arrow.svg",
            "engine": "bing images",
            "resolution": "64x64",
        },
        {
            "url": "https://example.com/png-page",
            "title": "Imagen",
            "img_src": "https://cdn.example.com/img1.png",
            "engine": "bing images",
            "resolution": "64x64",
        },
    ]
    downloaded: list[str] = []

    def fake_get(url, **kwargs):
        if "/search" in url:
            return _FakeResp(json_data={"query": "q", "results": results})
        downloaded.append(url)
        return _FakeResp(content=_png_bytes())

    monkeypatch.setattr(image_search_main.requests, "get", fake_get)

    out = image_search.search_chapter_images(_payload(num_images=2))
    validated = ImageGenerateOutput(**validate_output("generate_image", out))

    # La URL .svg no se descarga (0 HTTP hacia ella); solo se descarga el .png.
    assert "https://icons.example.com/lucide-static/icons/arrow.svg" not in downloaded
    assert downloaded == ["https://cdn.example.com/img1.png"]
    assert validated.generated == 1
    assert validated.failed == 1
    assert validated.results[0].status == "error"
    assert "non-raster extension" in (validated.results[0].error or "")
    assert validated.results[1].status == "ok"


def test_cupo_corta_a_mitad_de_pagina_con_exceso_de_candidatos(tmp_path, monkeypatch):
    """§17 #30 — bug del exceso (book_72, 4ª prueba): una página de SearXNG
    puede traer MUCHOS más candidatos que ``requested`` (~2400 en una sola
    request de duckduckgo images). Sin guard de cupo dentro del ``for``, el
    bucle consumía TODA la página aunque ``slot`` ya alcanzó el cupo → 2448
    resultados para requested=5, con "ok" válidos colándose entre fallos.

    Reproducción exacta: 1 página con 50 candidatos TODOS válidos/rápidos y
    requested=5 → corta en cuanto slot==requested, sin procesar los 45
    restantes de esa página (≤5 descargas, ≤5 entradas de resultado).
    """
    results = _search_results(50)
    downloaded: list[str] = []

    def fake_get(url, **kwargs):
        if "/search" in url:
            return _FakeResp(json_data={"query": "q", "results": results})
        downloaded.append(url)
        return _FakeResp(content=_png_bytes())

    monkeypatch.setattr(image_search_main.requests, "get", fake_get)

    out = image_search.search_chapter_images(_payload(num_images=5))
    validated = ImageGenerateOutput(**validate_output("generate_image", out))

    # Cupo exacto: 5 descargas y exactamente 5 entradas de resultado (sin el
    # fix, habrían sido 50 descargas y 50 entradas con 50 "ok").
    assert len(downloaded) == 5
    assert len(validated.results) == 5
    assert validated.requested == 5
    assert validated.generated == 5
    assert validated.failed == 0
    assert all(r.status == "ok" for r in validated.results)
    # Sin paginación extra: el cupo se llenó con la primera página.
    assert [r.image_id for r in validated.results] == [
        "img_01_web", "img_02_web", "img_03_web", "img_04_web", "img_05_web",
    ]


# ---------------------------------------------------------------------------
# §17 #48 Fase 1 — Cambio B (keywords salientes) y Cambio C (quality check)
# ---------------------------------------------------------------------------
def test_extract_salient_keywords_devuelve_keywords_razonables():
    texto = (
        "El estilo Imperio surgió en la Francia napoleónica. "
        "El estilo Imperio se caracteriza por columnas romanas. "
        "Napoleón Bonaparte impulsó el estilo Imperio en las artes decorativas. "
        "Otros estilos menores no repiten entidad alguna."
    )
    kws = image_search_main._extract_salient_keywords(texto, "es")
    assert kws, "debe extraer al menos una keyword de un texto con entidades"
    assert all(len(kw) >= 4 for kw in kws)
    assert len(kws) <= 4
    joined = " ".join(kws).lower()
    assert "imperio" in joined


def test_extract_salient_keywords_vacio_con_texto_invalido():
    assert image_search_main._extract_salient_keywords(None, "es") == []
    assert image_search_main._extract_salient_keywords("", "es") == []
    assert image_search_main._extract_salient_keywords("   ", "en") == []
    # Texto sin entidades claras (todo minúsculas): sin candidatos → vacío.
    assert image_search_main._extract_salient_keywords(
        "esto no contiene nombres propios relevantes solo palabras comunes", "es"
    ) == []


def test_search_query_anade_keywords_sin_duplicar_topic():
    texto = (
        "Antonio Gaudi diseno la Sagrada Familia con influencias goticas. "
        "Antonio Gaudi tambien trabajo en el Park Guell de Barcelona. "
        "La Sagrada Familia sigue en construccion hoy."
    )
    query = image_search_main._search_query(
        "Estilos arquitectónicos destacados",
        texto,
        search_topic="El estilo Imperio",
        book_topic="Historia de la arquitectura",
    )
    qwords = [w.lower() for w in query.split()]
    # no duplicados dentro de la query
    assert len(qwords) == len(set(qwords))
    # base intacta al principio y keywords añadidas al final
    assert query.startswith("El estilo Imperio")
    lowered = query.lower()
    assert "gaudi" in lowered or "familia" in lowered
    # acotada a IMAGE_QUERY_MAX_WORDS
    assert len(qwords) <= image_search_main.IMAGE_QUERY_MAX_WORDS


def _png_bytes_sized(width: int, height: int) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (width, height), color=(120, 30, 90)).save(buf, format="PNG")
    return buf.getvalue()


def test_passes_quality_check_rechaza_pequena_y_alargada():
    # thumbnail pequeño: rechaza
    assert image_search_main._passes_quality_check(_png_bytes_sized(200, 150)) is False
    # banner alargado (ratio 4:1 > 3.0): rechaza
    assert image_search_main._passes_quality_check(_png_bytes_sized(1200, 300)) is False
    # corrupta/no-imagen: falla el check sin excepción
    assert image_search_main._passes_quality_check(b"no es una imagen") is False
    assert image_search_main._passes_quality_check(None) is False
    assert image_search_main._passes_quality_check(b"") is False


def test_passes_quality_check_acepta_imagen_normal():
    assert image_search_main._passes_quality_check(_png_bytes_sized(800, 600)) is True
    # ratio 16:9 dentro del rango
    assert image_search_main._passes_quality_check(_png_bytes_sized(1024, 576)) is True
    # ratio vertical 2:3 (0.667) dentro del rango
    assert image_search_main._passes_quality_check(_png_bytes_sized(400, 600)) is True


# ---------------------------------------------------------------------------
# §17 #48 Fase 2 — ranking de candidatos (_score_candidate + best-first)
# ---------------------------------------------------------------------------
def _cand(img: str, title: str, resolution: str = "1024x768") -> dict:
    return {
        "url": f"https://example.com/page/{img}",
        "title": title,
        "img_src": f"https://cdn.example.com/{img}.png",
        "engine": "bing images",
        "resolution": resolution,
    }


def test_score_candidate_solapamiento_keywords():
    """El candidato cuyo texto solapa más keywords recibe mayor score."""
    keywords = ["caverna", "ecos", "subterraneo"]
    mejor = _cand("a.png", "La Caverna de los Ecos — mapa del subterraneo")
    peor = _cand("b.png", "Foto genérica de stock sin relación")
    s_mejor = image_search_main._score_candidate(mejor, keywords)
    s_peor = image_search_main._score_candidate(peor, keywords)
    assert s_mejor > s_peor
    # Sin keywords (fail-safe de Cambio B): componentes neutros, no excepción
    # (kw 0.5*2.0=1.0 + res 1.0 para 1024x768 + ar 1.0 para 4:3).
    assert image_search_main._score_candidate(peor, []) == 2.5


def test_loop_selecciona_mayor_score_no_primero(tmp_path, monkeypatch):
    """Con 2+ candidatos válidos, el loop best-first descarga primero el de
    mayor score (aunque NO sea el primero de la lista de SearXNG)."""
    # Primero en la lista: score bajo (sin keywords en el título, ratio 3:1
    # fuera del rango fotográfico). Segundo: score alto (keywords + 4:3).
    results = [
        _cand("img_low", "Foto genérica de stock", resolution="1200x400"),
        _cand("img_high", "La Caverna de los Ecos ilustración", resolution="1024x768"),
    ]
    downloaded: list[str] = []

    def fake_get(url, **kwargs):
        if "/search" in url:
            return _FakeResp(json_data={"results": results})
        downloaded.append(url)
        return _FakeResp(content=_png_bytes(640, 480))

    monkeypatch.setattr(image_search_main.requests, "get", fake_get)

    out = image_search.search_chapter_images(_payload(num_images=1))
    validated = ImageGenerateOutput(**validate_output("generate_image", out))
    assert validated.generated == 1 and validated.failed == 0
    # El aceptado es el de mayor score (img_high), no el primero de la lista.
    ok = validated.results[0]
    assert ok.status == "ok"
    raw_ok = [r for r in out["results"] if r.get("status") == "ok"][0]
    assert "img_high" in (raw_ok.get("source_url") or "")
    assert downloaded and "img_high" in downloaded[0]


def test_loop_candidato_unico_no_rompe(tmp_path, monkeypatch):
    """Con un solo candidato válido el comportamiento es idéntico al
    pre-ranking: se descarga y se acepta."""
    results = [_cand("img_only", "La Caverna de los Ecos — ilustración única")]

    def fake_get(url, **kwargs):
        if "/search" in url:
            return _FakeResp(json_data={"results": results})
        return _FakeResp(content=_png_bytes(640, 480))

    monkeypatch.setattr(image_search_main.requests, "get", fake_get)

    out = image_search.search_chapter_images(_payload(num_images=1))
    validated = ImageGenerateOutput(**validate_output("generate_image", out))
    assert validated.generated == 1 and validated.failed == 0
    assert validated.results[0].status == "ok"


# ---------------------------------------------------------------------------
# §17 #48 Fase 3 — resiliencia / rate-limiting de SearXNG
# ---------------------------------------------------------------------------
class _RateLimitResp:
    """Respuesta HTTP 429: raise_for_status lanza HTTPError con .response."""

    status_code = 429

    def raise_for_status(self):
        raise image_search_main.requests.exceptions.HTTPError(
            "429 Client Error", response=self
        )

    def json(self):  # pragma: no cover - no se debe llegar aquí
        return {"results": []}


def test_searxng_429_backoff_respeta_max_retries(monkeypatch):
    """HTTP 429 dispara backoff exponencial con jitter y respeta
    SEARXNG_MAX_RETRIES sin bucle infinito."""
    calls = {"n": 0}
    sleeps: list[float] = []

    def fake_get(url, **kwargs):
        calls["n"] += 1
        return _RateLimitResp()

    monkeypatch.setattr(image_search_main.requests, "get", fake_get)
    monkeypatch.setattr(image_search_main.time, "sleep", lambda s: sleeps.append(s))

    results, status = image_search_main._searxng_fetch("q", pageno=1)
    assert results == [] and status == "rate_limited"
    # Exactamente SEARXNG_MAX_RETRIES intentos, sin bucle infinito.
    assert calls["n"] == image_search_main.SEARXNG_MAX_RETRIES
    # Backoff exponencial: cada espera crece respecto a la anterior.
    assert len(sleeps) == image_search_main.SEARXNG_MAX_RETRIES - 1
    assert all(b >= a for a, b in zip(sleeps, sleeps[1:]))


def test_429_agotado_se_distingue_de_0_resultados_real(tmp_path, monkeypatch):
    """Tras agotar reintentos por 429, el slot del shortfall queda marcado
    'rate_limited' (≠ 'no_results' de un 0-resultados real)."""

    def fake_get(url, **kwargs):
        return _RateLimitResp() if "/search" in url else _FakeResp(content=_png_bytes())

    monkeypatch.setattr(image_search_main.requests, "get", fake_get)
    monkeypatch.setattr(image_search_main.time, "sleep", lambda s: None)

    out = image_search.search_chapter_images(_payload(num_images=2))
    validated = ImageGenerateOutput(**validate_output("generate_image", out))
    assert validated.generated == 0 and validated.failed == 2
    failed_errors = [r.get("error") for r in out["results"] if r.get("status") != "ok"]
    assert failed_errors and all(e == "rate_limited" for e in failed_errors)


def test_error_conexion_no_reintenta_en_bucle(monkeypatch):
    """Error de conexión/servidor caído: falla rápido (1 intento, 0 reintentos)."""
    calls = {"n": 0}

    def fake_get(url, **kwargs):
        calls["n"] += 1
        raise image_search_main.requests.exceptions.ConnectionError("server down")

    monkeypatch.setattr(image_search_main.requests, "get", fake_get)
    monkeypatch.setattr(image_search_main.time, "sleep", lambda s: None)

    results, status = image_search_main._searxng_fetch("q", pageno=1)
    assert results == [] and status == "error"
    assert calls["n"] == 1  # fail fast: sin bucle de reintentos

# ---------------------------------------------------------------------------
# §17 #48 Fase 4 — verificación semántica VLM (moondream-local vía Ollama)
# ---------------------------------------------------------------------------

def _mock_ollama_post(monkeypatch, responses=(), error=None):
    """Instala un fake de requests.post (Ollama) que registra payloads y
    devuelve respuestas secuenciales o lanza error."""
    calls: list[dict] = []
    it = iter(list(responses))

    def fake_post(url, **kwargs):
        calls.append({"url": url, **kwargs})
        if error is not None:
            raise error
        return _FakeResp(json_data={"response": next(it, "SI")})

    monkeypatch.setattr(image_search_main.requests, "post", fake_post)
    return calls


def test_vlm_disabled_default_no_network_call_and_always_true(monkeypatch):
    """(1) Con VLM_VERIFICATION_ENABLED=0 (DEFAULT): _verify_image_relevance
    NO hace ninguna llamada de red y devuelve True siempre. Garantiza que el
    comportamiento pre-Fase-4 es idéntico byte-a-byte."""
    monkeypatch.delenv("VLM_VERIFICATION_ENABLED", raising=False)
    calls = _mock_ollama_post(monkeypatch, error=AssertionError("NO debe llamarse"))

    assert image_search_main.VLM_VERIFICATION_ENABLED is False
    assert image_search_main._verify_image_relevance(_png_bytes(), "Doom", ["doom"]) is True
    assert image_search_main._verify_image_relevance(None, "", None) is True
    assert calls == []  # cero llamadas a Ollama


def test_vlm_enabled_si_acepta_candidato(monkeypatch):
    """(2) Flag activo + respuesta 'SI' del VLM (mockeado) → True y el
    candidato se acepta en el flujo completo."""
    monkeypatch.setattr(image_search_main, "VLM_VERIFICATION_ENABLED", True)
    monkeypatch.setattr(image_search_main, "VLM_BASE_URL", "http://ollama.test:11434")
    calls = _mock_ollama_post(monkeypatch, responses=["SI"])

    assert image_search_main._verify_image_relevance(_png_bytes(), "Doom", ["doom"]) is True
    assert len(calls) == 1
    assert calls[0]["url"] == "http://ollama.test:11434/api/generate"
    payload = calls[0]["json"]
    assert payload["model"] == image_search_main.VLM_MODEL_NAME
    assert payload["images"]  # base64 presente (payload multimodal)
    assert "Doom" in payload["prompt"]

    # Flujo completo: 1 candidato, VLM dice SI → aceptado.
    def fake_get(url, **kwargs):
        if "/search" in url:
            return _FakeResp(json_data={"query": "q", "results": _search_results(1)})
        return _FakeResp(content=_png_bytes())

    monkeypatch.setattr(image_search_main.requests, "get", fake_get)
    out = image_search.search_chapter_images(_payload(num_images=1))
    assert out["generated"] == 1
    assert out["results"][0]["status"] == "ok"
    # §17 #48 Fase 4 — trazabilidad VLM persistida en el meta aceptado:
    # flag activo → vlm_checked=True; candidato único aceptado → tried >= 1.
    assert out["results"][0].get("vlm_checked") is True
    assert out["results"][0].get("vlm_candidates_tried", 0) >= 1


def test_vlm_no_descarta_y_prueba_siguiente_candidato(monkeypatch):
    """(3) Respuesta 'NO' → False: el loop descarta ESE candidato (sin ocupar
    slot ni marcar error) y prueba el siguiente del pool ordenado por score."""
    monkeypatch.setattr(image_search_main, "VLM_VERIFICATION_ENABLED", True)
    # Primer candidato → NO, segundo → SI.
    calls = _mock_ollama_post(monkeypatch, responses=["NO", " Sí, es relevante. "])
    downloaded: list[str] = []

    def fake_get(url, **kwargs):
        if "/search" in url:
            return _FakeResp(json_data={"query": "q", "results": _search_results(2)})
        downloaded.append(url)
        return _FakeResp(content=_png_bytes())

    monkeypatch.setattr(image_search_main.requests, "get", fake_get)

    out = image_search.search_chapter_images(_payload(num_images=1))
    validated = ImageGenerateOutput(**validate_output("generate_image", out))

    # 2 llamadas VLM (NO → descarta 1º, SI → acepta 2º); solo se descarga el
    # 2º candidato y el slot se llena con status ok (skip-and-continue).
    assert len(calls) == 2
    assert len(downloaded) == 2
    assert validated.generated == 1
    assert validated.results[0].status == "ok"
    # El aceptado es el 2º candidato del pool (el 1º fue descartado por el VLM).
    assert out["results"][0].get("source_url") == "https://cdn.example.com/img2.png"


def test_vlm_timeout_error_fail_open(monkeypatch):
    """(4) Timeout/error de Ollama con flag activo → fail-open (True): no
    rompe la fase ni descarta candidatos que ya pasaron los filtros previos."""
    monkeypatch.setattr(image_search_main, "VLM_VERIFICATION_ENABLED", True)
    _mock_ollama_post(monkeypatch, error=requests.exceptions.Timeout("ollama timeout"))
    downloaded: list[str] = []

    def fake_get(url, **kwargs):
        if "/search" in url:
            return _FakeResp(json_data={"query": "q", "results": _search_results(1)})
        downloaded.append(url)
        return _FakeResp(content=_png_bytes())

    monkeypatch.setattr(image_search_main.requests, "get", fake_get)

    # Unit: fail-open True.
    assert image_search_main._verify_image_relevance(_png_bytes(), "Doom", None) is True

    # Flujo completo: el candidato se acepta pese al fallo del VLM.
    out = image_search.search_chapter_images(_payload(num_images=1))
    validated = ImageGenerateOutput(**validate_output("generate_image", out))
    assert validated.generated == 1
    assert validated.results[0].status == "ok"
# ---------------------------------------------------------------------------
# §17 imagenes — entity_keywords (siglas/consolas con dígito o "/")
# ---------------------------------------------------------------------------
def test_extract_entity_keywords_detecta_siglas_y_consolas():
    texto = (
        "La SNES superó en ventas a la NES y al Sega Genesis. "
        "El PS2 dominó la década y la Xbox Series X/S llegó después. "
        "También hubo Wii U y N64 en el mercado. "
        "SNES y PS2 se repitieron en varias generaciones."
    )
    entities = image_search_main._extract_entity_keywords(
        texto, "es", max_keywords=12
    )
    joined = " ".join(entities).upper()
    assert "SNES" in joined
    assert "PS2" in joined
    assert "NES" in joined
    assert "N64" in joined
    assert "XBOX SERIES X" in joined or "XBOX SERIES X/S" in joined


def test_search_query_diferencia_capitulos_por_siglas():
    base_topic = "Historia de los videojuegos, desde el pong hasta..."
    texto_snes = (
        "La SNES superó a la NES en ventas. "
        "La SNES relanzó la saga de Mario. "
        "Muchos niños pidieron una SNES en Navidad."
    )
    texto_ps2 = (
        "El PS2 dominó la generación. "
        "El PS2 vendió más que cualquier consola. "
        "La gente seguía comprando el PS2."
    )
    q_snes = image_search_main._search_query(
        "Titulo", texto_snes, search_topic=base_topic, book_topic="Historia de los videojuegos"
    ).upper()
    q_ps2 = image_search_main._search_query(
        "Titulo", texto_ps2, search_topic=base_topic, book_topic="Historia de los videojuegos"
    ).upper()
    # las queries finales ya no son idénticas (caso book_90)
    assert q_snes != q_ps2
    assert "SNES" in q_snes
    assert "PS2" in q_ps2


def test_extract_entity_keywords_regresion_sin_siglas():
    texto = (
        "El estilo Imperio surgió en la Francia napoleónica. "
        "Napoleón Bonaparte impulsó el estilo Imperio."
    )
    entities = image_search_main._extract_entity_keywords(texto, "es")
    assert entities == []
    # las keywords genéricas Title-Case siguen igual que antes
    gen = image_search_main._extract_salient_keywords(texto, "es")
    joined = " ".join(gen).lower()
    assert "imperio" in joined


def test_entity_keywords_caso_real_book90_snes():
    # Fragmento con el capítulo SNES de book_90 (SNES repetida muchas veces).
    texto = (
        "La SNES o Super Nintendo Entertainment System fue lanzada en 1990. "
        "La SNES compitió contra el Sega Genesis y demostró su potencia. "
        "Muchos desarrolladores apostaron por la SNES por su hardware. "
        "La SNES y la NES convivieron durante años. "
        "El catálogo de la SNES incluye títulos legendarios. "
        "La SNES sigue siendo recordada como una de las mejores consolas."
    )
    entities = image_search_main._extract_entity_keywords(texto, "es")
    joined = "|".join(entities).upper()
    # SNES es la entidad más frecuente → debería aparecer al principio
    assert "SNES" in joined
    assert entities and "SNES" == entities[0].upper().split()[0]


def test_entity_keywords_filtra_numeros_romanos_aislados():
    # Romanos aislados (token exacto del patrón 1) NO se emiten. Un romano que
    # el patrón 2 capture como parte de un nombre propio con sufijo de letra
    # suelta (p.ej. "Mega Man X") sí se conserva.
    texto_sueltos = (
        "VII fue un título importante y VII repitió. "
        "También salió un II que no recordamos. "
        "En cambio XI no tuvo éxito."
    )
    entities = image_search_main._extract_entity_keywords(
        texto_sueltos, "es", max_keywords=12
    )
    tok_set = {t.upper() for t in entities}
    assert not (tok_set & {"VII", "II", "XI"}), (
        "numerales romanos aislados no deben emitirse: %r" % entities
    )
    # Romiano fusionado en nombre propio con sufijo de letra suelta se conserva
    texto_compuesto = (
        "Mega Man X es un clásico. Mega Man X tuvo éxito en SNES."
    )
    comp = image_search_main._extract_entity_keywords(
        texto_compuesto, "es", max_keywords=12
    )
    joined = "|".join(comp).upper()
    assert "MEGA MAN X" in joined


def test_entity_keywords_camelcase_playstation2_completo():
    # "PlayStation" (camelCase) no debe partirse en "Station 2": debe emitirse
    # como un solo token "PlayStation 2".
    texto = (
        "El PlayStation 2 dominó la generación. "
        "El PlayStation 2 fue la consola más vendida. "
        "La gente seguía comprando el PlayStation 2."
    )
    entities = image_search_main._extract_entity_keywords(
        texto, "es", max_keywords=12
    )
    tok_set = set(entities)
    assert "PlayStation 2" in tok_set, "debe emitirse 'PlayStation 2' completo, no %r" % entities
    assert not any(t == "Station 2" for t in tok_set), (
        "no debe partirse en 'Station 2': %r" % entities
    )
    # camelCase simple sin sufijo numérico cae en la extracción genérica normal
    # (genéricas), no en entity_keywords (que exige sufijo) — sin regresión.
    texto_cube = (
        "El GameCube fue una consola de Nintendo. El GameCube tenía mandos."
    )
    gen_cube = image_search_main._extract_salient_keywords(texto_cube, "es")
    ent_cube = image_search_main._extract_entity_keywords(
        texto_cube, "es", max_keywords=12
    )
    joined_gen = " ".join(gen_cube).lower()
    assert "cube" in joined_gen or "gamecube" in joined_gen
    assert not any("GameCube" in t for t in ent_cube)
