"""Transcripción de los audios de los grupos con Whisper abierto (faster-whisper,
en la CPU del servidor, sin pagar a nadie).

Vocabulario: Whisper acepta un contexto corto (unas 220 piezas de texto), así
que de glosario_campo.txt solo van siempre los términos marcados con *. Si la
transcripción trae una palabra parecida a otro término del glosario («turbeira»
y «turbera», «un dado» e «inundado»), se transcribe otra vez sugiriendo esos
términos. No se reemplaza texto a mano: es Whisper, oyendo el audio otra vez,
quien decide si la palabra sugerida es la que se dijo.

Los audios se transcriben de uno en uno en un hilo aparte, para no frenar el
registro de mensajes. El modelo se carga la primera vez que hace falta y se
suelta tras un rato sin audios, para no ocupar memoria si nadie habla.
"""
import difflib
import io
import logging
import os
import queue
import re
import threading
import time
import unicodedata
from pathlib import Path
from typing import Callable

logger = logging.getLogger("telegram_bot.transcriptor")

LIBERAR_TRAS_S = 15 * 60
GLOSARIO = Path(os.environ.get("WHISPER_GLOSARIO", Path(__file__).with_name("glosario_campo.txt")))
INTRO = "Descripciones de campo en páramos, humedales, sabanas inundables y morichales de Colombia: "
MAX_CONTEXTO = 700          # caracteres; Whisper corta el contexto a unas 220 piezas
MAX_SUGERIDOS = 25
PARECIDO_MINIMO = 0.8
MIN_LETRAS = 5              # palabras más cortas se parecen a demasiadas cosas


def _plano(texto: str) -> str:
    return unicodedata.normalize("NFKD", texto.lower()).encode("ascii", "ignore").decode()


def leer_glosario(ruta: Path) -> tuple[list[str], list[str]]:
    """(términos que van siempre, todos los términos)."""
    siempre, todos = [], []
    try:
        lineas = ruta.read_text(encoding="utf-8").splitlines()
    except OSError:
        logger.warning("No se encontró el glosario %s; Whisper va sin vocabulario", ruta)
        return [], []
    for linea in lineas:
        linea = linea.strip()
        if not linea or linea.startswith("#"):
            continue
        fijo = linea.startswith("*")
        termino = linea.lstrip("*").strip()
        if termino:
            todos.append(termino)
            if fijo:
                siempre.append(termino)
    return siempre, todos


def contexto(terminos: list[str], prefijo: str = "") -> str:
    texto = prefijo + INTRO
    for t in terminos:
        if len(texto) + len(t) + 2 > MAX_CONTEXTO:
            break
        texto += t + ", "
    return texto.rstrip(", ") + "."


def parecidos(transcripcion: str, terminos: list[str]) -> list[str]:
    """Términos del glosario que no aparecen tal cual en la transcripción pero
    se parecen a una palabra (o a dos palabras seguidas, por «un dado» →
    «inundado») de ella."""
    palabras = re.findall(r"[a-z]+", _plano(transcripcion))
    piezas = set(palabras) | {a + b for a, b in zip(palabras, palabras[1:])}
    piezas = {p for p in piezas if len(p) >= MIN_LETRAS}
    texto = " ".join(palabras)
    encontrados = []
    for termino in terminos:
        plano = _plano(termino)
        if " " in plano or len(plano) < MIN_LETRAS or re.search(rf"\b{plano}\b", texto):
            continue
        if any(difflib.SequenceMatcher(None, plano, p).ratio() >= PARECIDO_MINIMO for p in piezas):
            encontrados.append(termino)
    return encontrados[:MAX_SUGERIDOS]


class Transcriptor:
    def __init__(self, modelo: str, carpeta: str, hilos_cpu: int,
                 al_terminar: Callable[[int, str | None, str], None]) -> None:
        self._nombre_modelo = modelo
        self._carpeta = carpeta
        self._hilos_cpu = hilos_cpu
        self._al_terminar = al_terminar
        self._cola: queue.Queue[tuple[int, Callable[[], bytes]]] = queue.Queue()
        self._en_cola: set[int] = set()
        self._candado = threading.Lock()
        self._modelo = None
        self._siempre, self._todos = leer_glosario(GLOSARIO)
        logger.info("Glosario: %d términos (%d siempre en el contexto)", len(self._todos), len(self._siempre))
        threading.Thread(target=self._trabajar, name="whisper", daemon=True).start()

    def encolar(self, id_: int, audio: Callable[[], bytes]) -> None:
        """`audio` devuelve los bytes cuando llega su turno (así un audio que
        se reencola al reiniciar se descarga solo cuando se va a transcribir)."""
        with self._candado:
            if id_ in self._en_cola:
                return
            self._en_cola.add(id_)
        self._cola.put((id_, audio))

    def _cargar(self):
        if self._modelo is None:
            from faster_whisper import WhisperModel  # import pesado: solo si hay audios
            inicio = time.monotonic()
            self._modelo = WhisperModel(self._nombre_modelo, device="cpu", compute_type="int8",
                                        cpu_threads=self._hilos_cpu, download_root=self._carpeta)
            logger.info("Whisper %s cargado en %.0f s", self._nombre_modelo, time.monotonic() - inicio)
        return self._modelo

    def _pasada(self, audio: bytes, contexto_: str) -> str:
        segmentos, _ = self._cargar().transcribe(io.BytesIO(audio), language="es", beam_size=5,
                                                 vad_filter=True, condition_on_previous_text=False,
                                                 initial_prompt=contexto_ or None)
        return " ".join(s.text.strip() for s in segmentos).strip()

    def transcribir(self, audio: bytes) -> str:
        texto = self._pasada(audio, contexto(self._siempre) if self._siempre else "")
        sugeridos = parecidos(texto, self._todos)
        if not sugeridos:
            return texto
        segunda = self._pasada(audio, contexto([*sugeridos, *self._siempre], "Palabras que pueden aparecer: "
                                               + ", ".join(sugeridos) + ". "))
        logger.info("Segunda pasada sugiriendo %s", sugeridos)
        return segunda or texto

    def _trabajar(self) -> None:
        while True:
            try:
                id_, audio = self._cola.get(timeout=LIBERAR_TRAS_S)
            except queue.Empty:
                if self._modelo is not None:
                    self._modelo = None
                    logger.info("Whisper liberado tras %s min sin audios", LIBERAR_TRAS_S // 60)
                continue
            try:
                inicio = time.monotonic()
                texto = self.transcribir(audio())
                estado = "lista" if texto else "vacia"
                logger.info("Audio %s transcrito en %.0f s (%s caracteres)", id_, time.monotonic() - inicio, len(texto))
            except Exception:
                logger.exception("No se pudo transcribir el audio %s", id_)
                texto, estado = None, "fallida"
            try:
                self._al_terminar(id_, texto, estado)
            except Exception:
                logger.exception("No se pudo guardar la transcripción del audio %s", id_)
            finally:
                with self._candado:
                    self._en_cola.discard(id_)
