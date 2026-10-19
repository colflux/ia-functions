"""Grupos de observación: lo que se habla en un grupo de Telegram sobre un sitio.

Un grupo se registra con el comando /observacion <sitio> (por ejemplo
«Paramox»). Desde ese momento el bot guarda en silencio todo lo que se dice en
él: textos, fotos (en el bucket) y audios (en el bucket por GRUPOS_DIAS_AUDIO
días, 5 por defecto, y su transcripción en la base para siempre).

La transcripción de Whisper se puede revisar y corregir desde el grupo; la
corrección se guarda aparte y no reemplaza la original, para poder medir qué
tanto se equivoca Whisper.

Cada mensaje se asocia a una foto, porque la pregunta del estudio es cuántas
descripciones cualitativas hacen falta para relacionar lo que la gente percibe
con los datos tomados un día concreto:
  - una foto se asocia a sí misma;
  - una respuesta a una foto, a esa foto;
  - una respuesta a otro mensaje, a la foto de ese mensaje;
  - lo demás, a la última foto enviada en el grupo (o a ninguna si aún no hay).

Las tablas viven en la base del asistente y se crean la primera vez que se usan.
"""
import logging
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any

from psycopg.types.json import Jsonb

from app.db.session import get_connection

logger = logging.getLogger("uvicorn.error")

DIAS_AUDIO = 5
# consulta: un mensaje dirigido al bot (una pregunta, no una descripción).
TIPOS = ("texto", "foto", "audio", "ubicacion", "consulta", "otro")
# Una descripción toma la última ubicación que su autor compartió en el grupo
# si es de las últimas horas.
HORAS_UBICACION = 12

_TABLAS = (
    """
    CREATE TABLE IF NOT EXISTS grupos_observacion (
        chat_id BIGINT PRIMARY KEY,
        nombre TEXT NOT NULL DEFAULT '',
        sitio TEXT NOT NULL,
        activo BOOLEAN NOT NULL DEFAULT true,
        registrado_por TEXT NOT NULL DEFAULT '',
        creado TIMESTAMPTZ NOT NULL DEFAULT now()
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS observaciones_grupo (
        id SERIAL PRIMARY KEY,
        chat_id BIGINT NOT NULL REFERENCES grupos_observacion(chat_id),
        message_id BIGINT NOT NULL,
        fecha TIMESTAMPTZ NOT NULL,
        autor_id BIGINT,
        autor_nombre TEXT NOT NULL DEFAULT '',
        tipo TEXT NOT NULL,
        texto TEXT NOT NULL DEFAULT '',
        transcripcion TEXT,
        estado_transcripcion TEXT,
        archivo TEXT,
        tipo_archivo TEXT,
        duracion_s INTEGER,
        respuesta_a BIGINT,
        foto_message_id BIGINT,
        creado TIMESTAMPTZ NOT NULL DEFAULT now(),
        UNIQUE (chat_id, message_id)
    )
    """,
    "ALTER TABLE observaciones_grupo ADD COLUMN IF NOT EXISTS transcripcion_corregida TEXT",
    "ALTER TABLE observaciones_grupo ADD COLUMN IF NOT EXISTS corregida_por TEXT",
    "ALTER TABLE observaciones_grupo ADD COLUMN IF NOT EXISTS corregida_el TIMESTAMPTZ",
    # Atributos del sitio (atributos_sitio.py): los que dice cada descripción y,
    # en las fotos, los que ve la IA. Y el día y sitio reales de la foto, para
    # comparar después con las mediciones.
    "ALTER TABLE observaciones_grupo ADD COLUMN IF NOT EXISTS atributos JSONB",
    "ALTER TABLE observaciones_grupo ADD COLUMN IF NOT EXISTS ia_descripcion TEXT",
    "ALTER TABLE observaciones_grupo ADD COLUMN IF NOT EXISTS ia_atributos JSONB",
    "ALTER TABLE observaciones_grupo ADD COLUMN IF NOT EXISTS dato_sitio TEXT",
    "ALTER TABLE observaciones_grupo ADD COLUMN IF NOT EXISTS dato_fecha DATE",
    "ALTER TABLE observaciones_grupo ADD COLUMN IF NOT EXISTS dato_luz TEXT",
    # Si el filtro cree que el mensaje describe la foto. No se borra nada por
    # esto: se marca, para revisarlo y decidir después.
    "ALTER TABLE observaciones_grupo ADD COLUMN IF NOT EXISTS es_descripcion BOOLEAN",
    # Dónde estaba quien envió el mensaje: la ubicación que compartió en el
    # grupo (o el GPS de la foto si se envió como archivo).
    "ALTER TABLE observaciones_grupo ADD COLUMN IF NOT EXISTS latitud DOUBLE PRECISION",
    "ALTER TABLE observaciones_grupo ADD COLUMN IF NOT EXISTS longitud DOUBLE PRECISION",
    "ALTER TABLE observaciones_grupo ADD COLUMN IF NOT EXISTS precision_m DOUBLE PRECISION",
    "ALTER TABLE observaciones_grupo ADD COLUMN IF NOT EXISTS origen_ubicacion TEXT",
    "CREATE INDEX IF NOT EXISTS observaciones_grupo_foto_idx ON observaciones_grupo (chat_id, foto_message_id)",
    "CREATE INDEX IF NOT EXISTS observaciones_grupo_fecha_idx ON observaciones_grupo (fecha)",
)
_tablas_listas = False


def _conexion():
    global _tablas_listas
    conn = get_connection()
    if not _tablas_listas:
        for sql in _TABLAS:
            conn.execute(sql)
        _tablas_listas = True
    return conn


@dataclass
class MensajeGrupo:
    chat_id: int
    message_id: int
    fecha: datetime
    tipo: str
    autor_id: int | None = None
    autor_nombre: str = ""
    texto: str = ""
    respuesta_a: int | None = None
    duracion_s: int | None = None
    tipo_archivo: str | None = None
    atributos: dict | None = None
    ia_descripcion: str | None = None
    ia_atributos: dict | None = None
    es_descripcion: bool | None = None
    latitud: float | None = None
    longitud: float | None = None
    precision_m: float | None = None
    origen_ubicacion: str | None = None


def _json(valor: dict | None):
    return Jsonb(valor) if valor else None


class ObservacionesGrupo:
    # ── grupos ────────────────────────────────────────────────────────────
    def registrar_grupo(self, chat_id: int, nombre: str, sitio: str, registrado_por: str) -> None:
        with _conexion() as conn:
            conn.execute(
                "INSERT INTO grupos_observacion (chat_id, nombre, sitio, registrado_por) VALUES (%s, %s, %s, %s) "
                "ON CONFLICT (chat_id) DO UPDATE SET nombre = EXCLUDED.nombre, sitio = EXCLUDED.sitio, "
                "activo = true, registrado_por = EXCLUDED.registrado_por",
                (chat_id, nombre, sitio, registrado_por),
            )
        logger.info("GRUPO registrado chat=%s sitio=%s por=%s", chat_id, sitio, registrado_por)

    def pausar_grupo(self, chat_id: int) -> bool:
        with _conexion() as conn:
            return conn.execute("UPDATE grupos_observacion SET activo = false WHERE chat_id = %s",
                                (chat_id,)).rowcount > 0

    def grupos(self) -> list[dict[str, Any]]:
        with _conexion() as conn:
            filas = conn.execute(
                "SELECT g.chat_id, g.nombre, g.sitio, g.activo, g.creado, count(o.id) "
                "FROM grupos_observacion g LEFT JOIN observaciones_grupo o USING (chat_id) "
                "GROUP BY g.chat_id ORDER BY g.creado"
            ).fetchall()
        return [{"chat_id": f[0], "nombre": f[1], "sitio": f[2], "activo": f[3],
                 "creado": f[4].isoformat(timespec="minutes"), "mensajes": f[5]} for f in filas]

    def grupo_activo(self, chat_id: int) -> dict[str, Any] | None:
        with _conexion() as conn:
            fila = conn.execute("SELECT sitio, nombre FROM grupos_observacion WHERE chat_id = %s AND activo",
                                (chat_id,)).fetchone()
        return {"sitio": fila[0], "nombre": fila[1]} if fila else None

    # ── mensajes ──────────────────────────────────────────────────────────
    def guardar(self, m: MensajeGrupo, archivo: str | None) -> dict[str, Any] | None:
        """Guarda un mensaje y lo asocia a una foto. Devuelve None si el grupo
        no está registrado o está pausado (el mensaje no se guarda)."""
        if m.tipo not in TIPOS:
            m.tipo = "otro"
        with _conexion() as conn:
            if not conn.execute("SELECT 1 FROM grupos_observacion WHERE chat_id = %s AND activo",
                                (m.chat_id,)).fetchone():
                return None
            foto = self._foto_asociada(conn, m)
            if m.latitud is None and m.tipo in ("texto", "audio") and m.autor_id:
                ultima = conn.execute(
                    "SELECT latitud, longitud, precision_m FROM observaciones_grupo WHERE chat_id = %s "
                    "AND autor_id = %s AND tipo = 'ubicacion' AND fecha BETWEEN %s AND %s "
                    "ORDER BY fecha DESC LIMIT 1",
                    (m.chat_id, m.autor_id, m.fecha - timedelta(hours=HORAS_UBICACION), m.fecha),
                ).fetchone()
                if ultima:
                    m.latitud, m.longitud, m.precision_m = ultima
                    m.origen_ubicacion = "compartida antes por la persona"
            # Un audio queda pendiente aunque no haya bucket: el bot lo transcribe
            # desde memoria. Solo los que tienen archivo se pueden reintentar.
            estado = "pendiente" if m.tipo == "audio" else None
            fila = conn.execute(
                "INSERT INTO observaciones_grupo (chat_id, message_id, fecha, autor_id, autor_nombre, tipo, texto, "
                "estado_transcripcion, archivo, tipo_archivo, duracion_s, respuesta_a, foto_message_id, "
                "atributos, ia_descripcion, ia_atributos, es_descripcion, latitud, longitud, precision_m, "
                "origen_ubicacion) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) "
                "ON CONFLICT (chat_id, message_id) DO NOTHING RETURNING id",
                (m.chat_id, m.message_id, m.fecha, m.autor_id, m.autor_nombre, m.tipo, m.texto, estado,
                 archivo, m.tipo_archivo, m.duracion_s, m.respuesta_a, foto,
                 _json(m.atributos), m.ia_descripcion, _json(m.ia_atributos), m.es_descripcion,
                 m.latitud, m.longitud, m.precision_m, m.origen_ubicacion),
            ).fetchone()
        return {"id": fila[0] if fila else None, "duplicado": fila is None, "foto_message_id": foto}

    def foto_para(self, m: MensajeGrupo) -> int | None:
        """La foto a la que se asociaría el mensaje (None si aún no hay fotos)."""
        with _conexion() as conn:
            return self._foto_asociada(conn, m)

    def borrar(self, id_: int) -> str | None:
        """Borra un mensaje guardado (no era una descripción). Devuelve la clave
        de su archivo en el bucket, si tenía, para que quien llama la borre."""
        with _conexion() as conn:
            fila = conn.execute("DELETE FROM observaciones_grupo WHERE id = %s RETURNING archivo", (id_,)).fetchone()
        return fila[0] if fila else None

    @staticmethod
    def _foto_asociada(conn, m: MensajeGrupo) -> int | None:
        if m.tipo == "foto":
            return m.message_id
        if m.respuesta_a:
            fila = conn.execute(
                "SELECT tipo, foto_message_id FROM observaciones_grupo WHERE chat_id = %s AND message_id = %s",
                (m.chat_id, m.respuesta_a),
            ).fetchone()
            if fila:
                return m.respuesta_a if fila[0] == "foto" else fila[1]
        fila = conn.execute(
            "SELECT message_id FROM observaciones_grupo WHERE chat_id = %s AND tipo = 'foto' AND fecha <= %s "
            "ORDER BY fecha DESC, message_id DESC LIMIT 1",
            (m.chat_id, m.fecha),
        ).fetchone()
        return fila[0] if fila else None

    def guardar_atributos(self, id_: int, atributos: dict | None, es_descripcion: bool | None = None) -> None:
        with _conexion() as conn:
            conn.execute("UPDATE observaciones_grupo SET atributos = %s, "
                         "es_descripcion = coalesce(%s, es_descripcion) WHERE id = %s",
                         (_json(atributos), es_descripcion, id_))

    def guardar_ia(self, id_: int, descripcion_: str | None, atributos: dict | None) -> None:
        with _conexion() as conn:
            conn.execute("UPDATE observaciones_grupo SET ia_descripcion = %s, ia_atributos = %s WHERE id = %s",
                         (descripcion_, _json(atributos), id_))

    def datos_de_foto(self, chat_id: int, message_id: int, sitio: str, fecha: date, luz: str | None) -> bool:
        """El sitio y el día reales en que se tomó la foto (y si de día o de noche):
        con eso se compara después lo descrito con las mediciones de ese día."""
        with _conexion() as conn:
            return conn.execute(
                "UPDATE observaciones_grupo SET dato_sitio = %s, dato_fecha = %s, dato_luz = %s "
                "WHERE chat_id = %s AND message_id = %s AND tipo = 'foto'",
                (sitio, fecha, luz, chat_id, message_id),
            ).rowcount > 0

    def sin_atributos(self, limite: int = 200) -> list[dict[str, Any]]:
        """Para completar lo guardado antes: descripciones sin atributos y fotos sin observación de la IA."""
        with _conexion() as conn:
            filas = conn.execute(
                "SELECT id, tipo, texto, transcripcion, transcripcion_corregida, archivo, "
                "coalesce(tipo_archivo, 'image/jpeg') FROM observaciones_grupo "
                "WHERE (tipo IN ('texto', 'audio') AND atributos IS NULL AND es_descripcion IS NULL "
                "       AND (tipo = 'texto' OR estado_transcripcion = 'lista')) "
                "   OR (tipo = 'foto' AND ia_atributos IS NULL AND ia_descripcion IS NULL AND archivo IS NOT NULL) "
                "ORDER BY id LIMIT %s", (limite,)
            ).fetchall()
        return [{"id": f[0], "tipo": f[1], "texto": f[2], "transcripcion": f[3], "transcripcion_corregida": f[4],
                 "archivo": f[5], "tipo_archivo": f[6]} for f in filas]

    def guardar_transcripcion(self, id_: int, texto: str | None, estado: str) -> bool:
        with _conexion() as conn:
            return conn.execute(
                "UPDATE observaciones_grupo SET transcripcion = %s, estado_transcripcion = %s WHERE id = %s",
                (texto, estado, id_),
            ).rowcount > 0

    def mensaje(self, chat_id: int, message_id: int) -> dict[str, Any] | None:
        with _conexion() as conn:
            fila = conn.execute(
                "SELECT id, tipo, transcripcion, estado_transcripcion, transcripcion_corregida, corregida_por, "
                "archivo IS NOT NULL, fecha, atributos, ia_atributos, ia_descripcion, dato_sitio, dato_fecha, dato_luz "
                "FROM observaciones_grupo WHERE chat_id = %s AND message_id = %s",
                (chat_id, message_id),
            ).fetchone()
        if not fila:
            return None
        return {"id": fila[0], "tipo": fila[1], "transcripcion": fila[2], "estado_transcripcion": fila[3],
                "transcripcion_corregida": fila[4], "corregida_por": fila[5], "audio_disponible": fila[6],
                "fecha": fila[7].isoformat(timespec="minutes"), "atributos": fila[8], "ia_atributos": fila[9],
                "ia_descripcion": fila[10], "dato_sitio": fila[11],
                "dato_fecha": fila[12].isoformat() if fila[12] else None, "dato_luz": fila[13]}

    def corregir(self, chat_id: int, message_id: int, texto: str, por: str) -> bool:
        """Guarda la transcripción corregida por una persona; la de Whisper se conserva."""
        with _conexion() as conn:
            return conn.execute(
                "UPDATE observaciones_grupo SET transcripcion_corregida = %s, corregida_por = %s, corregida_el = now() "
                "WHERE chat_id = %s AND message_id = %s AND tipo = 'audio'",
                (texto.strip() or None, por, chat_id, message_id),
            ).rowcount > 0

    def audios_pendientes(self, limite: int = 50) -> list[dict[str, Any]]:
        with _conexion() as conn:
            filas = conn.execute(
                "SELECT id, archivo FROM observaciones_grupo WHERE tipo = 'audio' AND archivo IS NOT NULL "
                "AND estado_transcripcion = 'pendiente' ORDER BY id LIMIT %s", (limite,)
            ).fetchall()
        return [{"id": f[0], "archivo": f[1]} for f in filas]

    def archivo_de(self, id_: int) -> tuple[str, str] | None:
        with _conexion() as conn:
            fila = conn.execute("SELECT archivo, coalesce(tipo_archivo, 'application/octet-stream') "
                                "FROM observaciones_grupo WHERE id = %s", (id_,)).fetchone()
        return (fila[0], fila[1]) if fila and fila[0] else None

    def audios_vencidos(self, dias: int = DIAS_AUDIO) -> list[dict[str, Any]]:
        """Audios con más de `dias` días ya transcritos (o que fallaron): el
        audio se puede borrar, la transcripción se queda."""
        limite = datetime.now(timezone.utc) - timedelta(days=dias)
        with _conexion() as conn:
            filas = conn.execute(
                "SELECT id, archivo FROM observaciones_grupo WHERE tipo = 'audio' AND archivo IS NOT NULL "
                "AND fecha < %s AND estado_transcripcion IN ('lista', 'fallida', 'vacia')", (limite,)
            ).fetchall()
        return [{"id": f[0], "archivo": f[1]} for f in filas]

    def quitar_archivo(self, id_: int) -> None:
        with _conexion() as conn:
            conn.execute("UPDATE observaciones_grupo SET archivo = NULL WHERE id = %s", (id_,))

    # ── consulta ──────────────────────────────────────────────────────────
    def consultar(self, sitio: str = "", desde: date | None = None, hasta: date | None = None,
                  foto: int | None = None, chat_id: int | None = None) -> list[dict[str, Any]]:
        condiciones, valores = [], []
        if sitio:
            condiciones.append("g.sitio ILIKE %s")
            valores.append(f"%{sitio}%")
        if chat_id is not None:
            condiciones.append("o.chat_id = %s")
            valores.append(chat_id)
        if desde:
            condiciones.append("(o.fecha AT TIME ZONE 'America/Bogota')::date >= %s")
            valores.append(desde)
        if hasta:
            condiciones.append("(o.fecha AT TIME ZONE 'America/Bogota')::date <= %s")
            valores.append(hasta)
        if foto is not None:
            condiciones.append("o.foto_message_id = %s")
            valores.append(foto)
        donde = ("WHERE " + " AND ".join(condiciones)) if condiciones else ""
        with _conexion() as conn:
            filas = conn.execute(
                "SELECT o.id, g.sitio, o.chat_id, o.message_id, o.fecha AT TIME ZONE 'America/Bogota', "
                "o.autor_id, o.autor_nombre, o.tipo, o.texto, o.transcripcion, o.estado_transcripcion, "
                "o.foto_message_id, o.archivo IS NOT NULL, o.transcripcion_corregida, o.archivo, "
                "o.atributos, o.ia_descripcion, o.ia_atributos, o.dato_sitio, o.dato_fecha, o.dato_luz, "
                "o.es_descripcion, o.latitud, o.longitud, o.precision_m, o.origen_ubicacion "
                f"FROM observaciones_grupo o JOIN grupos_observacion g USING (chat_id) {donde} "
                "ORDER BY o.fecha, o.message_id",
                valores,
            ).fetchall()
        return [{
            "registro": f[0], "sitio": f[1], "chat_id": f[2], "mensaje": f[3],
            "fecha": f[4].isoformat(timespec="minutes"), "autor_id": f[5], "autor": f[6], "tipo": f[7],
            "texto": f[8], "transcripcion": f[9], "estado_transcripcion": f[10], "foto": f[11],
            "archivo_disponible": f[12], "transcripcion_corregida": f[13], "archivo": f[14],
            "atributos": f[15], "ia_descripcion": f[16], "ia_atributos": f[17], "dato_sitio": f[18],
            "dato_fecha": f[19].isoformat() if f[19] else None, "dato_luz": f[20],
            "es_descripcion": f[21], "latitud": f[22], "longitud": f[23], "precision_m": f[24],
            "origen_ubicacion": f[25],
        } for f in filas]


def descripcion(fila: dict[str, Any]) -> str:
    """Lo que la persona dijo: el texto escrito, la transcripción del audio (la
    corregida si alguien la revisó) o el pie de la foto."""
    partes = [fila.get("texto") or "", fila.get("transcripcion_corregida") or fila.get("transcripcion") or ""]
    return " ".join(p.strip() for p in partes if p and p.strip())


def resumir(filas: list[dict[str, Any]]) -> dict[str, Any]:
    """Por foto: cuántas descripciones y cuántas personas distintas, y las
    descripciones. Una descripción es un mensaje de texto o un audio con
    contenido. El pie de la foto no cuenta (lo escribe quien la envía) y se
    devuelve aparte; las preguntas al bot tampoco."""
    fotos: dict[Any, dict[str, Any]] = {}
    for f in filas:
        if f["tipo"] == "ubicacion":
            continue  # la ubicación va en el Excel; no es una descripción de una foto
        clave = (f["chat_id"], f["foto"])
        grupo = fotos.setdefault(clave, {"sitio": f["sitio"], "foto": f["foto"], "enviada": None,
                                         "descripciones": [], "personas": set(), "audios_pendientes": 0})
        if f["tipo"] == "foto" and f["mensaje"] == f["foto"]:
            grupo["enviada"] = f["fecha"]
            grupo["pie"] = f["texto"]
            grupo["archivo"] = f.get("archivo")
            grupo["ia"] = {"descripcion": f.get("ia_descripcion"), "atributos": f.get("ia_atributos")}
            if f.get("dato_fecha"):
                grupo["datos"] = {"sitio": f.get("dato_sitio"), "fecha": f["dato_fecha"], "luz": f.get("dato_luz")}
            continue
        if f["tipo"] == "audio" and f["estado_transcripcion"] == "pendiente":
            grupo["audios_pendientes"] += 1
        texto = descripcion(f)
        if texto and f["tipo"] in ("texto", "audio") and f.get("es_descripcion") is False:
            grupo["otros_mensajes"] = grupo.get("otros_mensajes", 0) + 1
        elif texto and f["tipo"] in ("texto", "audio"):
            entrada = {"fecha": f["fecha"], "autor": f["autor"], "tipo": f["tipo"], "texto": texto}
            if f["tipo"] == "audio":
                entrada["transcripcion_revisada"] = bool(f.get("transcripcion_corregida"))
            if f.get("atributos"):
                entrada["atributos"] = f["atributos"]
            grupo["descripciones"].append(entrada)
            grupo["personas"].add(f["autor_id"] or f["autor"])
    resumen = []
    for g in fotos.values():
        resumen.append({
            "sitio": g["sitio"],
            "foto": g["foto"] if g["foto"] is not None else "sin foto (mensajes antes de la primera imagen)",
            "foto_enviada": g["enviada"],
            "pie_de_foto": g.get("pie") or "",
            "archivo": g.get("archivo"),
            "descripciones": len(g["descripciones"]),
            "personas": len(g["personas"]),
            "audios_por_transcribir": g["audios_pendientes"],
            "otros_mensajes_no_descripcion": g.get("otros_mensajes", 0),
            "foto_del_dia": g.get("datos"),
            "lo_que_ve_la_ia": (g.get("ia") or {}).get("descripcion"),
            "atributos_que_ve_la_ia": (g.get("ia") or {}).get("atributos"),
            "textos": g["descripciones"],
        })
    return {
        "mensajes": len(filas),
        "fotos": sum(1 for g in fotos.values() if g["foto"] is not None),
        "personas_que_describieron": len({p for g in fotos.values() for p in g["personas"]}),
        "por_foto": resumen,
    }
