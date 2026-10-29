"""Grupos de observación: lo que se habla en un grupo de Telegram sobre un sitio.

Un grupo se registra con el comando /observacion <sitio> (por ejemplo
«Paramox»). Desde ese momento el bot guarda en silencio todo lo que se dice en
él: textos, fotos (en el bucket) y audios (en el bucket por GRUPOS_DIAS_AUDIO
días, 5 por defecto, y su transcripción en la base para siempre).

La transcripción de Whisper se puede revisar y corregir desde el grupo; la
corrección se guarda aparte y no reemplaza la original, para poder medir qué
tanto se equivoca Whisper.

Descripciones en privado: para que nadie copie lo que dijeron los demás, el
grupo queda solo para que el equipo envíe las fotos. Bajo cada foto el bot pone
un botón que abre el chat privado con él; ahí cada persona describe la foto
(texto o nota de voz) mientras dure su «descripción en curso» (hasta que toque
«Terminar» o pasen 12 horas sin escribir). Esos mensajes se guardan con el
chat_id del grupo y la foto que describen, canal = 'privado' y chat_origen = el
chat privado; su message_id es el del chat privado, por eso la unicidad es
(chat_id, message_id) para lo del grupo y (chat_origen, message_id) para lo
privado.

Preguntas por foto: en privado el bot pregunta una a una las preguntas del
listado editable (preguntas_foto.py) y al final si la persona observa algo más.
Cada respuesta guarda el código y el texto de la pregunta (pregunta_codigo,
pregunta_texto; ADICIONAL para lo que agrega al final) y estado_respuesta:
'respondida', 'no_sabe' o 'no_aplica'. La pregunta que toca es la primera que la
persona no ha respondido sobre esa foto; un audio que se está transcribiendo
(audio_en_espera) todavía no cuenta, porque si no responde a la pregunta se borra
y se vuelve a preguntar.

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
# Una descripción en privado sigue abierta mientras la persona escriba; tras
# este tiempo sin mensajes, lo que escriba vuelve a ser una pregunta al asistente.
MINUTOS_DESCRIPCION = 12 * 60
# Respuestas que no describen nada pero sí responden la pregunta.
SIN_DESCRIPCION = ("no_sabe", "no_aplica", "no_responde")
# Descripción detallada de cada foto por la IA (hoja «Descripción IA» del Excel):
# los aspectos siguen las preguntas que responden las personas.
CAMPOS_DETALLE_IA = ("general", "paisaje_y_ecosistema", "agua", "nivel_del_agua", "suelo", "vegetacion", "fauna",
                     "cielo_y_luz", "intervencion_humana", "otros_detalles", "no_se_puede_ver")

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
    # Ubicaciones que comparte cada persona (en el grupo, en el chat privado
    # con el bot o en tiempo real). Sus descripciones toman la más reciente.
    """
    CREATE TABLE IF NOT EXISTS ubicaciones_personas (
        id SERIAL PRIMARY KEY,
        autor_id BIGINT NOT NULL,
        autor_nombre TEXT NOT NULL DEFAULT '',
        chat_id BIGINT,
        latitud DOUBLE PRECISION NOT NULL,
        longitud DOUBLE PRECISION NOT NULL,
        precision_m DOUBLE PRECISION,
        origen TEXT NOT NULL,
        fecha TIMESTAMPTZ NOT NULL,
        creado TIMESTAMPTZ NOT NULL DEFAULT now()
    )
    """,
    "CREATE INDEX IF NOT EXISTS ubicaciones_personas_autor_idx ON ubicaciones_personas (autor_id, fecha)",
    "CREATE INDEX IF NOT EXISTS observaciones_grupo_foto_idx ON observaciones_grupo (chat_id, foto_message_id)",
    # Descripciones en privado (ver arriba): de dónde llegó el mensaje.
    "ALTER TABLE observaciones_grupo ADD COLUMN IF NOT EXISTS canal TEXT NOT NULL DEFAULT 'grupo'",
    "ALTER TABLE observaciones_grupo ADD COLUMN IF NOT EXISTS chat_origen BIGINT",
    "ALTER TABLE observaciones_grupo DROP CONSTRAINT IF EXISTS observaciones_grupo_chat_id_message_id_key",
    "CREATE UNIQUE INDEX IF NOT EXISTS observaciones_grupo_mensaje_grupo_uidx "
    "ON observaciones_grupo (chat_id, message_id) WHERE chat_origen IS NULL",
    "CREATE UNIQUE INDEX IF NOT EXISTS observaciones_grupo_mensaje_privado_uidx "
    "ON observaciones_grupo (chat_origen, message_id) WHERE chat_origen IS NOT NULL",
    # La foto que cada persona está describiendo en su chat privado.
    """
    CREATE TABLE IF NOT EXISTS descripciones_en_curso (
        autor_id BIGINT PRIMARY KEY,
        autor_nombre TEXT NOT NULL DEFAULT '',
        chat_privado BIGINT NOT NULL,
        chat_id BIGINT NOT NULL REFERENCES grupos_observacion(chat_id),
        foto_message_id BIGINT NOT NULL,
        desde TIMESTAMPTZ NOT NULL DEFAULT now(),
        ultima TIMESTAMPTZ NOT NULL DEFAULT now()
    )
    """,
    "CREATE INDEX IF NOT EXISTS observaciones_grupo_fecha_idx ON observaciones_grupo (fecha)",
    # Preguntas por foto (ver arriba).
    "ALTER TABLE observaciones_grupo ADD COLUMN IF NOT EXISTS pregunta_codigo TEXT",
    "ALTER TABLE observaciones_grupo ADD COLUMN IF NOT EXISTS pregunta_texto TEXT",
    "ALTER TABLE observaciones_grupo ADD COLUMN IF NOT EXISTS estado_respuesta TEXT",
    # 'persona' para la caracterización (categoría 0, una sola vez por persona): no
    # es una descripción de la foto y no va al chat ni a las hojas de respuestas.
    "ALTER TABLE observaciones_grupo ADD COLUMN IF NOT EXISTS pregunta_alcance TEXT",
    "ALTER TABLE descripciones_en_curso ADD COLUMN IF NOT EXISTS audio_en_espera INTEGER",
    # Descripción detallada de la foto por la IA, con el modelo que la hizo y cuándo.
    "ALTER TABLE observaciones_grupo ADD COLUMN IF NOT EXISTS ia_detalle JSONB",
    "ALTER TABLE observaciones_grupo ADD COLUMN IF NOT EXISTS ia_detalle_modelo TEXT",
    "ALTER TABLE observaciones_grupo ADD COLUMN IF NOT EXISTS ia_detalle_el TIMESTAMPTZ",
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
    # Descripciones en privado: el chat privado de donde llegó y la foto que describe.
    canal: str = "grupo"
    chat_origen: int | None = None
    foto_message_id: int | None = None
    # Preguntas por foto: a qué pregunta responde y cómo (respondida, no_sabe, no_aplica).
    pregunta_codigo: str | None = None
    pregunta_texto: str | None = None
    estado_respuesta: str | None = None
    pregunta_alcance: str | None = None


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
            if m.chat_origen is not None:
                # En privado la foto la dice la descripción en curso, no la conversación.
                m.canal = "privado"
                foto = m.foto_message_id
                if not foto or not conn.execute(
                        "SELECT 1 FROM observaciones_grupo WHERE chat_id = %s AND message_id = %s "
                        "AND tipo = 'foto' AND chat_origen IS NULL", (m.chat_id, foto)).fetchone():
                    return {"id": None, "duplicado": False, "foto_message_id": None, "sin_foto": True}
            else:
                foto = self._foto_asociada(conn, m)
            if m.tipo == "ubicacion" and m.latitud is not None and m.autor_id:
                self._registrar_ubicacion(conn, m.autor_id, m.autor_nombre, m.chat_id, m.latitud, m.longitud,
                                          m.precision_m, m.origen_ubicacion or "compartida en el grupo", m.fecha)
            if m.latitud is None and m.tipo in ("texto", "audio") and m.autor_id:
                ultima = conn.execute(
                    "SELECT latitud, longitud, precision_m, origen FROM ubicaciones_personas WHERE autor_id = %s "
                    "AND fecha BETWEEN %s AND %s ORDER BY fecha DESC LIMIT 1",
                    (m.autor_id, m.fecha - timedelta(hours=HORAS_UBICACION), m.fecha),
                ).fetchone()
                if ultima:
                    m.latitud, m.longitud, m.precision_m = ultima[:3]
                    m.origen_ubicacion = ultima[3]
            # Un audio queda pendiente aunque no haya bucket: el bot lo transcribe
            # desde memoria. Solo los que tienen archivo se pueden reintentar.
            estado = "pendiente" if m.tipo == "audio" else None
            conflicto = ("(chat_origen, message_id) WHERE chat_origen IS NOT NULL" if m.chat_origen is not None
                         else "(chat_id, message_id) WHERE chat_origen IS NULL")
            fila = conn.execute(
                "INSERT INTO observaciones_grupo (chat_id, message_id, fecha, autor_id, autor_nombre, tipo, texto, "
                "estado_transcripcion, archivo, tipo_archivo, duracion_s, respuesta_a, foto_message_id, "
                "atributos, ia_descripcion, ia_atributos, es_descripcion, latitud, longitud, precision_m, "
                "origen_ubicacion, canal, chat_origen, pregunta_codigo, pregunta_texto, estado_respuesta, "
                "pregunta_alcance) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, "
                "%s, %s, %s, %s) "
                f"ON CONFLICT {conflicto} DO NOTHING RETURNING id",
                (m.chat_id, m.message_id, m.fecha, m.autor_id, m.autor_nombre, m.tipo, m.texto, estado,
                 archivo, m.tipo_archivo, m.duracion_s, m.respuesta_a, foto,
                 _json(m.atributos), m.ia_descripcion, _json(m.ia_atributos), m.es_descripcion,
                 m.latitud, m.longitud, m.precision_m, m.origen_ubicacion, m.canal, m.chat_origen,
                 m.pregunta_codigo, m.pregunta_texto, m.estado_respuesta, m.pregunta_alcance),
            ).fetchone()
            if m.chat_origen is not None and m.autor_id:
                conn.execute("UPDATE descripciones_en_curso SET ultima = now() WHERE autor_id = %s", (m.autor_id,))
        return {"id": fila[0] if fila else None, "duplicado": fila is None, "foto_message_id": foto}

    @staticmethod
    def _registrar_ubicacion(conn, autor_id: int, autor_nombre: str, chat_id: int | None, latitud: float,
                             longitud: float, precision_m: float | None, origen: str, fecha: datetime) -> int:
        conn.execute(
            "INSERT INTO ubicaciones_personas (autor_id, autor_nombre, chat_id, latitud, longitud, precision_m, "
            "origen, fecha) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
            (autor_id, autor_nombre, chat_id, latitud, longitud, precision_m, origen, fecha),
        )
        # Las descripciones de las últimas horas que no tenían ubicación la toman.
        return conn.execute(
            "UPDATE observaciones_grupo SET latitud = %s, longitud = %s, precision_m = %s, origen_ubicacion = %s "
            "WHERE autor_id = %s AND tipo IN ('texto', 'audio') AND latitud IS NULL AND fecha BETWEEN %s AND %s",
            (latitud, longitud, precision_m, f"{origen} (después)", autor_id,
             fecha - timedelta(hours=HORAS_UBICACION), fecha),
        ).rowcount

    def ubicacion_persona(self, autor_id: int, autor_nombre: str, chat_id: int | None, latitud: float,
                          longitud: float, precision_m: float | None, origen: str, fecha: datetime) -> int:
        """Guarda una ubicación compartida por una persona. Devuelve cuántas de
        sus descripciones recientes sin ubicación la tomaron."""
        with _conexion() as conn:
            return self._registrar_ubicacion(conn, autor_id, autor_nombre, chat_id, latitud, longitud,
                                             precision_m, origen, fecha)

    def foto_para(self, m: MensajeGrupo) -> int | None:
        """La foto a la que se asociaría el mensaje (None si aún no hay fotos)."""
        if m.chat_origen is not None:
            return m.foto_message_id
        with _conexion() as conn:
            return self._foto_asociada(conn, m)

    def borrar(self, id_: int) -> str | None:
        """Borra un mensaje guardado (no era una descripción). Devuelve la clave
        de su archivo en el bucket, si tenía, para que quien llama la borre."""
        with _conexion() as conn:
            fila = conn.execute("DELETE FROM observaciones_grupo WHERE id = %s RETURNING archivo", (id_,)).fetchone()
        return fila[0] if fila else None

    def borrar_privado(self, id_: int) -> str | None:
        """Borra un mensaje enviado en privado (un audio que resultó no ser una
        descripción). Devuelve la clave de su archivo ("" si no tenía) o None si
        no era un mensaje privado."""
        with _conexion() as conn:
            fila = conn.execute("DELETE FROM observaciones_grupo WHERE id = %s AND canal = 'privado' "
                                "RETURNING coalesce(archivo, '')", (id_,)).fetchone()
        return fila[0] if fila else None

    @staticmethod
    def _foto_asociada(conn, m: MensajeGrupo) -> int | None:
        if m.tipo == "foto":
            return m.message_id
        if m.respuesta_a:
            fila = conn.execute(
                "SELECT tipo, foto_message_id FROM observaciones_grupo WHERE chat_id = %s AND message_id = %s "
                "AND chat_origen IS NULL",
                (m.chat_id, m.respuesta_a),
            ).fetchone()
            if fila:
                return m.respuesta_a if fila[0] == "foto" else fila[1]
        fila = conn.execute(
            "SELECT message_id FROM observaciones_grupo WHERE chat_id = %s AND tipo = 'foto' AND fecha <= %s "
            "AND chat_origen IS NULL "
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

    def guardar_detalle(self, id_: int, detalle: dict, modelo: str) -> None:
        with _conexion() as conn:
            conn.execute("UPDATE observaciones_grupo SET ia_detalle = %s, ia_detalle_modelo = %s, ia_detalle_el = now() "
                         "WHERE id = %s AND tipo = 'foto'", (_json(detalle), modelo, id_))

    def fotos_sin_detalle(self, limite: int = 20) -> list[dict[str, Any]]:
        """Fotos guardadas que todavía no tienen la descripción detallada de la IA."""
        with _conexion() as conn:
            filas = conn.execute(
                "SELECT id FROM observaciones_grupo WHERE tipo = 'foto' AND chat_origen IS NULL "
                "AND archivo IS NOT NULL AND ia_detalle IS NULL ORDER BY id LIMIT %s", (limite,)
            ).fetchall()
        return [{"id": f[0]} for f in filas]

    def datos_de_foto(self, chat_id: int, message_id: int, sitio: str, fecha: date, luz: str | None) -> bool:
        """El sitio y el día reales en que se tomó la foto (y si de día o de noche):
        con eso se compara después lo descrito con las mediciones de ese día."""
        with _conexion() as conn:
            return conn.execute(
                "UPDATE observaciones_grupo SET dato_sitio = %s, dato_fecha = %s, dato_luz = %s "
                "WHERE chat_id = %s AND message_id = %s AND tipo = 'foto' AND chat_origen IS NULL",
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
        """Un mensaje del grupo."""
        return self._mensaje("chat_id = %s AND message_id = %s AND chat_origen IS NULL", (chat_id, message_id))

    def mensaje_privado(self, chat_origen: int, message_id: int) -> dict[str, Any] | None:
        """Un mensaje que una persona envió en su chat privado con el bot."""
        return self._mensaje("chat_origen = %s AND message_id = %s", (chat_origen, message_id))

    def _mensaje(self, donde: str, valores: tuple) -> dict[str, Any] | None:
        with _conexion() as conn:
            fila = conn.execute(
                "SELECT id, tipo, transcripcion, estado_transcripcion, transcripcion_corregida, corregida_por, "
                "archivo IS NOT NULL, fecha, atributos, ia_atributos, ia_descripcion, dato_sitio, dato_fecha, dato_luz, "
                "canal, autor_id, pregunta_codigo, pregunta_texto, estado_respuesta, pregunta_alcance, chat_id "
                f"FROM observaciones_grupo WHERE {donde}",
                valores,
            ).fetchone()
        if not fila:
            return None
        return {"id": fila[0], "tipo": fila[1], "transcripcion": fila[2], "estado_transcripcion": fila[3],
                "transcripcion_corregida": fila[4], "corregida_por": fila[5], "audio_disponible": fila[6],
                "fecha": fila[7].isoformat(timespec="minutes"), "atributos": fila[8], "ia_atributos": fila[9],
                "ia_descripcion": fila[10], "dato_sitio": fila[11],
                "dato_fecha": fila[12].isoformat() if fila[12] else None, "dato_luz": fila[13],
                "canal": fila[14], "autor_id": fila[15], "pregunta_codigo": fila[16], "pregunta_texto": fila[17],
                "estado_respuesta": fila[18], "pregunta_alcance": fila[19], "chat_id": fila[20]}

    def mensaje_por_id(self, id_: int) -> dict[str, Any] | None:
        """Un mensaje por su número de registro (para saber a qué pregunta responde un audio)."""
        return self._mensaje("id = %s", (id_,))

    def corregir(self, chat_id: int, message_id: int, texto: str, por: str) -> bool:
        """Guarda la transcripción corregida por una persona; la de Whisper se conserva."""
        return self._corregir("chat_id = %s AND message_id = %s AND chat_origen IS NULL", (chat_id, message_id),
                              texto, por)

    def corregir_registro(self, id_: int, texto: str, por: str) -> bool:
        """Corrección que hace el equipo con el número de registro (columna «registro» del Excel)."""
        return self._corregir("id = %s", (id_,), texto, por)

    def corregir_privado(self, chat_origen: int, message_id: int, texto: str, por: str) -> bool:
        return self._corregir("chat_origen = %s AND message_id = %s", (chat_origen, message_id), texto, por)

    def _corregir(self, donde: str, valores: tuple, texto: str, por: str) -> bool:
        with _conexion() as conn:
            return conn.execute(
                "UPDATE observaciones_grupo SET transcripcion_corregida = %s, corregida_por = %s, corregida_el = now() "
                f"WHERE {donde} AND tipo = 'audio'",
                (texto.strip() or None, por, *valores),
            ).rowcount > 0

    # ── descripciones en privado ──────────────────────────────────────────
    def foto_por_registro(self, id_: int) -> dict[str, Any] | None:
        """La foto del grupo con ese número de registro (el que lleva el botón
        «Describir esta foto»), con su sitio y si el grupo sigue activo."""
        with _conexion() as conn:
            fila = conn.execute(
                "SELECT o.chat_id, o.message_id, g.sitio, g.activo FROM observaciones_grupo o "
                "JOIN grupos_observacion g USING (chat_id) "
                "WHERE o.id = %s AND o.tipo = 'foto' AND o.chat_origen IS NULL", (id_,)
            ).fetchone()
        return {"chat_id": fila[0], "message_id": fila[1], "sitio": fila[2], "activo": fila[3]} if fila else None

    def iniciar_descripcion(self, autor_id: int, autor_nombre: str, chat_privado: int, chat_id: int,
                            foto: int) -> None:
        """Desde ahora lo que la persona escriba en privado describe esa foto."""
        with _conexion() as conn:
            conn.execute(
                "INSERT INTO descripciones_en_curso (autor_id, autor_nombre, chat_privado, chat_id, foto_message_id) "
                "VALUES (%s, %s, %s, %s, %s) ON CONFLICT (autor_id) DO UPDATE SET autor_nombre = EXCLUDED.autor_nombre, "
                "chat_privado = EXCLUDED.chat_privado, chat_id = EXCLUDED.chat_id, "
                "foto_message_id = EXCLUDED.foto_message_id, desde = now(), ultima = now(), audio_en_espera = NULL",
                (autor_id, autor_nombre, chat_privado, chat_id, foto),
            )

    def descripcion_en_curso(self, autor_id: int, minutos: int = MINUTOS_DESCRIPCION) -> dict[str, Any] | None:
        """La foto que la persona está describiendo, si escribió en los últimos `minutos`."""
        with _conexion() as conn:
            fila = conn.execute(
                "SELECT d.chat_id, d.foto_message_id, g.sitio, d.desde, d.audio_en_espera FROM descripciones_en_curso d "
                "JOIN grupos_observacion g USING (chat_id) "
                "WHERE d.autor_id = %s AND g.activo AND d.ultima > now() - make_interval(mins => %s)",
                (autor_id, minutos),
            ).fetchone()
        return ({"chat_id": fila[0], "foto": fila[1], "sitio": fila[2], "desde": fila[3].isoformat(),
                 "audio_en_espera": fila[4]} if fila else None)

    def respondidas(self, autor_id: int, chat_id: int, foto: int, excluir: int | None = None) -> set[str]:
        """Códigos de las preguntas que la persona ya respondió sobre esa foto
        (con «No sé» y «No aplica» incluidos). `excluir`: el audio que se está
        transcribiendo, que aún no cuenta."""
        with _conexion() as conn:
            filas = conn.execute(
                "SELECT DISTINCT pregunta_codigo FROM observaciones_grupo WHERE chat_id = %s AND foto_message_id = %s "
                "AND autor_id = %s AND canal = 'privado' AND pregunta_codigo IS NOT NULL AND id <> %s",
                (chat_id, foto, autor_id, excluir or 0),
            ).fetchall()
        return {f[0] for f in filas}

    def respondidas_persona(self, autor_id: int, excluir: int | None = None) -> set[str]:
        """Códigos que la persona ya respondió en cualquier foto (para las
        preguntas de la persona, que se hacen una sola vez)."""
        with _conexion() as conn:
            filas = conn.execute(
                "SELECT DISTINCT pregunta_codigo FROM observaciones_grupo WHERE autor_id = %s AND canal = 'privado' "
                "AND pregunta_alcance = 'persona' AND id <> %s", (autor_id, excluir or 0),
            ).fetchall()
        return {f[0] for f in filas}

    def esperar_audio(self, autor_id: int, id_: int) -> None:
        """La pregunta no avanza hasta saber qué dice este audio."""
        with _conexion() as conn:
            conn.execute("UPDATE descripciones_en_curso SET audio_en_espera = %s WHERE autor_id = %s", (id_, autor_id))

    def audio_listo(self, id_: int) -> None:
        with _conexion() as conn:
            conn.execute("UPDATE descripciones_en_curso SET audio_en_espera = NULL WHERE audio_en_espera = %s", (id_,))

    def marcar_respuesta(self, id_: int, estado: str) -> None:
        """Un audio en el que la persona dijo «no sé» o «no aplica»."""
        with _conexion() as conn:
            conn.execute("UPDATE observaciones_grupo SET estado_respuesta = %s, atributos = NULL WHERE id = %s",
                         (estado, id_))

    def terminar_descripcion(self, autor_id: int) -> dict[str, Any] | None:
        """Cierra la descripción en curso. Devuelve cuántos mensajes guardó la
        persona sobre esa foto desde que la abrió y de qué foto era (None si no
        había ninguna)."""
        with _conexion() as conn:
            fila = conn.execute("DELETE FROM descripciones_en_curso WHERE autor_id = %s "
                                "RETURNING chat_id, foto_message_id, desde", (autor_id,)).fetchone()
            if not fila:
                return None
            mensajes = conn.execute(
                "SELECT count(*) FROM observaciones_grupo WHERE chat_id = %s AND foto_message_id = %s "
                "AND autor_id = %s AND canal = 'privado' AND creado >= %s", (fila[0], fila[1], autor_id, fila[2])
            ).fetchone()[0]
        return {"mensajes": mensajes, "chat_id": fila[0], "foto": fila[1]}

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
                "o.es_descripcion, o.latitud, o.longitud, o.precision_m, o.origen_ubicacion, o.canal, "
                "o.pregunta_codigo, o.pregunta_texto, o.estado_respuesta, "
                "o.ia_detalle, o.ia_detalle_modelo, o.ia_detalle_el AT TIME ZONE 'America/Bogota', o.pregunta_alcance "
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
            "origen_ubicacion": f[25], "canal": f[26],
            "pregunta_codigo": f[27], "pregunta_texto": f[28], "estado_respuesta": f[29],
            "ia_detalle": f[30], "ia_detalle_modelo": f[31],
            "ia_detalle_el": f[32].isoformat(timespec="minutes") if f[32] else None,
            "pregunta_alcance": f[33],
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
    devuelve aparte; las preguntas al bot tampoco, ni los «No sé» y «No aplica»
    (se cuentan aparte)."""
    fotos: dict[Any, dict[str, Any]] = {}
    for f in filas:
        if f["tipo"] == "ubicacion" or f.get("pregunta_alcance") == "persona":
            # La ubicación va en el Excel; la caracterización de la persona (datos
            # personales) solo en su hoja del Excel: ninguna es una descripción de la foto.
            continue
        clave = (f["chat_id"], f["foto"])
        grupo = fotos.setdefault(clave, {"sitio": f["sitio"], "foto": f["foto"], "enviada": None,
                                         "descripciones": [], "personas": set(), "audios_pendientes": 0,
                                         "no_sabe": 0, "no_aplica": 0, "no_responde": 0})
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
        if f.get("estado_respuesta") in SIN_DESCRIPCION:
            grupo[f["estado_respuesta"]] += 1
            continue
        if texto and f["tipo"] in ("texto", "audio") and f.get("es_descripcion") is False:
            grupo["otros_mensajes"] = grupo.get("otros_mensajes", 0) + 1
        elif texto and f["tipo"] in ("texto", "audio"):
            entrada = {"fecha": f["fecha"], "autor": f["autor"], "tipo": f["tipo"], "texto": texto}
            if f.get("pregunta_codigo"):
                entrada["pregunta"] = f["pregunta_codigo"]
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
            "respuestas_no_se": g["no_sabe"],
            "respuestas_no_aplica": g["no_aplica"],
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
