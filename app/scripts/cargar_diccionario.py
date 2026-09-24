"""Carga el diccionario de campo desde su Excel y reemplaza al que hay en la base.

Uso, dentro del contenedor del asistente:

    python -m app.scripts.cargar_diccionario /ruta/diccionario.xlsx            # muestra qué haría
    python -m app.scripts.cargar_diccionario /ruta/diccionario.xlsx --aplicar  # lo carga

Lee la hoja «Diccionario de términos» (columnas Punto, Observación de campo,
Definición ecológica, Variable asociada, Variable secundaria, Regla cuantitativa,
Interpretación). Cada fila es un término y queda como un fragmento con fuente
«diccionario-NN <observación>». Las demás hojas (semáforo, conversiones, sin
datos, preguntas) no se cargan: son para el equipo.

El reemplazo es seguro: primero se calculan los vectores de todo el diccionario
nuevo; solo si eso sale bien se borra el anterior y se escribe el nuevo, en una
única transacción. Los diccionarios subidos desde el chat (fuente
documentos/diccionario/...) no se tocan.
"""
import sys

import openpyxl
from pgvector.utils import Vector

from app.bootstrap.container import get_embedding_provider
from app.db.session import get_connection

HOJA = "Diccionario de términos"
COLUMNAS = ["Punto", "Observación de campo", "Definición ecológica", "Variable asociada",
            "Variable secundaria", "Regla cuantitativa", "Interpretación"]
VACIOS = {"", "ninguna disponible", "none", "-"}
COLECCION = "dictionary"


def leer(ruta: str) -> list[tuple[str, str]]:
    libro = openpyxl.load_workbook(ruta, read_only=True, data_only=True)
    hoja = libro[HOJA]
    filas = hoja.iter_rows(values_only=True)
    encabezado = [str(c or "").strip() for c in next(filas)]
    faltan = [c for c in COLUMNAS if c not in encabezado]
    if faltan:
        sys.exit(f"A la hoja «{HOJA}» le faltan columnas: {', '.join(faltan)}")
    indice = {c: encabezado.index(c) for c in COLUMNAS}
    terminos = []
    for fila in filas:
        valores = {c: str(fila[i] or "").strip() for c, i in indice.items()}
        if not valores["Observación de campo"]:
            continue
        partes = [f"{c}: {valores[c].rstrip('.')}." for c in COLUMNAS if valores[c].lower() not in VACIOS]
        numero = len(terminos) + 1
        terminos.append((f"diccionario-{numero:02d} {valores['Observación de campo']}", " ".join(partes)))
    return terminos


def main() -> None:
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    ruta, aplicar = sys.argv[1], "--aplicar" in sys.argv
    terminos = leer(ruta)
    print(f"{len(terminos)} términos en «{HOJA}».")
    for fuente, contenido in terminos[:3]:
        print(f"  {fuente}\n    {contenido[:220]}…")
    con_regla = sum("Regla cuantitativa:" in c for _, c in terminos)
    print(f"{con_regla} traen regla cuantitativa.")

    with get_connection() as conn:
        antes = conn.execute(
            "SELECT count(*) FROM document_chunks WHERE collection = %s AND source LIKE 'diccionario-%%'",
            (COLECCION,),
        ).fetchone()[0]
    print(f"En la base hay ahora {antes} fragmentos de diccionario (se reemplazarán).")
    if not aplicar:
        print("No se cambió nada. Para cargarlo, repite el comando con --aplicar.")
        return

    vectores = get_embedding_provider().embed([c for _, c in terminos])
    with get_connection() as conn:
        with conn.transaction():
            conn.execute("DELETE FROM document_chunks WHERE collection = %s AND source LIKE 'diccionario-%%'",
                         (COLECCION,))
            with conn.cursor() as cur:
                cur.executemany(
                    "INSERT INTO document_chunks (source, content, collection, embedding) VALUES (%s, %s, %s, %s)",
                    [(f, c, COLECCION, Vector(v)) for (f, c), v in zip(terminos, vectores)],
                )
        despues = conn.execute(
            "SELECT count(*) FROM document_chunks WHERE collection = %s AND source LIKE 'diccionario-%%'",
            (COLECCION,),
        ).fetchone()[0]
    print(f"Listo: {antes} fragmentos anteriores reemplazados por {despues}.")


if __name__ == "__main__":
    main()
