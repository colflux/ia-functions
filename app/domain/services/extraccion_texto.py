"""Extrae el texto de los archivos que se suben desde el chat.

Formatos aceptados: PDF, Word (.docx), Excel (.xlsx), CSV y texto plano.
Los formatos antiguos de Office (.doc, .xls) no se leen: hay que guardarlos
en el formato actual antes de subirlos. Un PDF escaneado no trae texto; hay
que pasarlo antes por reconocimiento de texto (OCR).
"""
import csv
import io
from pathlib import Path

from docx import Document
from openpyxl import load_workbook
from pypdf import PdfReader

class FormatoNoSoportado(ValueError):
    """El archivo no es de un formato que se pueda leer."""


def extraer_texto(nombre: str, contenido: bytes) -> str:
    extension = Path(nombre).suffix.lower()
    if extension == ".pdf":
        return _pdf(contenido)
    if extension == ".docx":
        return _docx(contenido)
    if extension == ".xlsx":
        return _xlsx(contenido)
    if extension == ".csv":
        return _csv(contenido)
    if extension in (".txt", ".md"):
        return _decodificar(contenido)
    raise FormatoNoSoportado(
        f"No se pueden leer archivos {extension or 'sin extensión'}. "
        "Formatos aceptados: PDF, Word (.docx), Excel (.xlsx), CSV, texto (.txt) "
        "e imágenes (JPG, PNG o WebP)."
    )


def _decodificar(contenido: bytes) -> str:
    try:
        return contenido.decode("utf-8-sig")
    except UnicodeDecodeError:
        return contenido.decode("latin-1")


def _pdf(contenido: bytes) -> str:
    lector = PdfReader(io.BytesIO(contenido))
    return "\n\n".join(pagina.extract_text() or "" for pagina in lector.pages)


def _docx(contenido: bytes) -> str:
    documento = Document(io.BytesIO(contenido))
    partes = [parrafo.text for parrafo in documento.paragraphs if parrafo.text.strip()]
    for tabla in documento.tables:
        for fila in tabla.rows:
            partes.append("\t".join(celda.text.strip() for celda in fila.cells))
    return "\n".join(partes)


def _xlsx(contenido: bytes) -> str:
    libro = load_workbook(io.BytesIO(contenido), read_only=True, data_only=True)
    partes = []
    for hoja in libro.worksheets:
        partes.append(f"## {hoja.title}")
        for fila in hoja.iter_rows(values_only=True):
            valores = ["" if valor is None else str(valor) for valor in fila]
            if any(valores):
                partes.append("\t".join(valores))
    libro.close()
    return "\n".join(partes)


def _csv(contenido: bytes) -> str:
    texto = _decodificar(contenido)
    try:
        dialecto = csv.Sniffer().sniff(texto[:5000], delimiters=",;\t")
    except csv.Error:
        dialecto = csv.excel
    filas = csv.reader(io.StringIO(texto), dialecto)
    return "\n".join("\t".join(fila) for fila in filas if any(fila))
