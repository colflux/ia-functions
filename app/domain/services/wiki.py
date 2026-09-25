"""La wiki del proyecto (https://colflux.github.io/context/) como fuente del asistente.

La wiki es un sitio MkDocs Material, que publica todo su texto en
search/search_index.json. De ahí se arma el texto de cada página. Ese índice no
trae los enlaces, así que los PDF se buscan en el HTML de cada página y se
descargan y leen aparte. Cada página y cada PDF queda
en el índice con su URL como fuente, en la colección de documentos, así que
buscar_documentos los encuentra junto a los archivos subidos.

Se sincroniza como mucho una vez cada `intervalo` segundos, en segundo plano:
solo se recalculan las páginas cuyo texto cambió, y las que desaparecieron de
la wiki se retiran del índice. Si la wiki no responde, no se borra nada.
"""
import json
import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from html.parser import HTMLParser
from urllib.parse import urljoin, urlparse

from app.domain.ports.vector_store import VectorStore
from app.domain.services.extraccion_texto import FormatoNoSoportado, extraer_texto
from app.domain.services.rag_service import RagService

logger = logging.getLogger("uvicorn.error")

INDICE = "search/search_index.json"
INTERVALO = 24 * 3600
BLOQUES = {"p", "li", "tr", "br", "h1", "h2", "h3", "h4", "h5", "h6", "pre", "blockquote", "div"}


@dataclass
class Resumen:
    paginas: int = 0
    pdfs: int = 0
    actualizadas: list[str] = field(default_factory=list)
    retiradas: list[str] = field(default_factory=list)
    errores: list[str] = field(default_factory=list)


class _TextoPlano(HTMLParser):
    """HTML → texto, con saltos de línea entre bloques y los enlaces a PDF
    anotados aparte."""

    def __init__(self) -> None:
        super().__init__()
        self.partes: list[str] = []
        self.pdfs: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag in BLOQUES:
            self.partes.append("\n")
        if tag == "li":
            self.partes.append("- ")
        if tag in ("td", "th"):
            self.partes.append(" | ")
        href = dict(attrs).get("href") or ""
        if tag == "a" and urlparse(href).path.lower().endswith(".pdf"):
            self.pdfs.append(href)

    def handle_endtag(self, tag):
        if tag in BLOQUES:
            self.partes.append("\n")

    def handle_data(self, data):
        self.partes.append(data)

    def texto(self) -> str:
        lineas = (" ".join(l.split()) for l in "".join(self.partes).splitlines())
        return "\n".join(l for l in lineas if l)


def _plano(html: str) -> tuple[str, list[str]]:
    lector = _TextoPlano()
    lector.feed(html or "")
    return lector.texto(), lector.pdfs


def paginas_del_indice(indice: dict, url_base: str) -> dict[str, str]:
    """Texto de cada página: {url: texto}."""
    titulos: dict[str, str] = {}
    secciones: dict[str, list[str]] = {}
    for doc in indice.get("docs", []):
        ruta, _, ancla = (doc.get("location") or "").partition("#")
        url = urljoin(url_base, ruta)
        texto, _ = _plano(doc.get("text", ""))
        titulo = " ".join((doc.get("title") or "").split())
        if not ancla:
            titulos[url] = titulo
            secciones.setdefault(url, []).insert(0, texto)
        else:
            secciones.setdefault(url, []).append(f"{titulo}\n{texto}".strip())
    paginas = {}
    for url, partes in secciones.items():
        cuerpo = "\n\n".join(p for p in partes if p)
        if cuerpo:
            titulo = titulos.get(url) or url
            paginas[url] = f"Wiki de COLFLUX — {titulo}\n{url}\n\n{cuerpo}"
    return paginas


class SincronizadorWiki:
    def __init__(self, rag: RagService, store: VectorStore, url_base: str,
                 descargar: Callable[[str], bytes], intervalo: int = INTERVALO) -> None:
        self._rag = rag
        self._store = store
        self._url_base = url_base.rstrip("/") + "/"
        self._descargar = descargar
        self._intervalo = intervalo
        self._proxima = 0.0
        self._candado = threading.Lock()

    def sincronizar_si_toca(self) -> None:
        """No bloquea: si ya toca, sincroniza en un hilo aparte."""
        with self._candado:
            if time.monotonic() < self._proxima:
                return
            self._proxima = time.monotonic() + self._intervalo
        threading.Thread(target=self._en_segundo_plano, daemon=True).start()

    def _en_segundo_plano(self) -> None:
        try:
            self.sincronizar()
        except Exception:
            logger.exception("WIKI no se pudo sincronizar")

    def sincronizar(self) -> Resumen:
        resumen = Resumen()
        indice = json.loads(self._descargar(self._url_base + INDICE))
        paginas = paginas_del_indice(indice, self._url_base)
        if not paginas:
            raise ValueError("el índice de la wiki no trae páginas")
        documentos = dict(paginas)
        pdfs: dict[str, str] = {}  # url del PDF → página que lo enlaza
        paginas_leidas = True
        for url in paginas:
            try:
                _, enlaces = _plano(self._descargar(url).decode("utf-8", "replace"))
            except OSError as exc:
                resumen.errores.append(f"{url}: {exc}")
                paginas_leidas = False
                continue
            for enlace in enlaces:
                pdfs.setdefault(urljoin(url, enlace), url)
        for url_pdf, url_pagina in pdfs.items():
            try:
                texto = extraer_texto(url_pdf.rsplit("/", 1)[-1], self._descargar(url_pdf)).strip()
            except (FormatoNoSoportado, OSError, ValueError) as exc:
                resumen.errores.append(f"{url_pdf}: {exc}")
                continue
            if texto:
                documentos[url_pdf] = f"Wiki de COLFLUX — PDF enlazado desde {url_pagina}\n{url_pdf}\n\n{texto}"
                resumen.pdfs += 1
        resumen.paginas = len(paginas)

        for url, texto in documentos.items():
            if self._rag.actualizar_fuente(url, texto) is not None:
                resumen.actualizadas.append(url)
        # Un fallo de descarga no debe borrar lo que ya estaba indexado: un PDF
        # que no se pudo leer sigue vigente mientras esté enlazado, y si alguna
        # página no se pudo leer no se sabe qué PDF enlaza, así que no se
        # retira ninguno.
        vigentes = set(documentos) | set(pdfs)
        for fuente in self._store.fuentes(self._url_base):
            es_pdf = urlparse(fuente).path.lower().endswith(".pdf")
            if fuente not in vigentes and (paginas_leidas or not es_pdf):
                self._store.borrar_fuente(fuente)
                resumen.retiradas.append(fuente)
        logger.info("WIKI %d páginas, %d PDF; actualizadas %d, retiradas %d, errores %d",
                    resumen.paginas, resumen.pdfs, len(resumen.actualizadas),
                    len(resumen.retiradas), len(resumen.errores))
        return resumen
