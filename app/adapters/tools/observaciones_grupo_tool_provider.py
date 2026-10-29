"""Consulta, desde el chat, lo que se dijo en los grupos de observación de
Telegram (por ejemplo el del sitio inventado «Paramox»): descripciones escritas
y audios transcritos, agrupados por la foto a la que se refieren.

Solo con la sesión iniciada en la plataforma: así los participantes del estudio
no pueden pedirle al asistente (en Telegram, donde no hay sesión, o en el chat
web sin entrar) lo que describieron los demás y copiarlo."""
import logging
import re
from datetime import date, timedelta
from typing import Any

from app.domain.models import ToolSpec
from app.domain.ports.tool_provider import ToolProvider
from app.domain.ports.user_directory import UserDirectory
from app.domain.services.contexto import autorizacion_actual
from app.domain.services import preguntas_foto
from app.domain.services.atributos_sitio import CAMPOS
from app.domain.services.observaciones_grupo import (CAMPOS_DETALLE_IA, SIN_DESCRIPCION, ObservacionesGrupo,
                                                     descripcion, resumir)

# Fotos de los grupos en el bucket (el chat las muestra y permite descargarlas).
FOTO_DE_GRUPO = re.compile(r"^telegram/[^/]+/fotos/[^/]+$")
PREFIJO_IMAGENES_ANTIGUO = "documentos/imagen/"  # donde quedaban antes de separarlas por sitio

logger = logging.getLogger("uvicorn.error")

SIN_SESION = {
    "sin_datos": True,
    "requiere_sesion": True,
    "nota": ("Las fotos y descripciones de los grupos de observación solo se consultan en la plataforma con la "
             "sesión iniciada, para que quienes participan no vean lo que describieron los demás. Dilo así, "
             "en una frase, sin dar detalles de las fotos ni de las descripciones."),
}

MAX_TEXTOS_POR_FOTO = 15
MAX_FOTOS_LISTA = 30
MAX_TITULO = 70
NOTA = ("Son descripciones cualitativas que las personas de un grupo de Telegram dieron sobre fotos: "
        "lo que perciben, no mediciones. Los audios aparecen transcritos automáticamente (Whisper); los que "
        "tienen transcripcion_revisada=true los corrigió una persona, los demás pueden tener errores. Cita los textos tal cual, sin inventar. "
        "'lo_que_ve_la_ia' es la descripción automática de la foto; preséntala aparte de lo que dicen las "
        "personas, sin compararlas. Son percepciones: no las conviertas en valores de flujo. "
        "'pregunta' es el código de la pregunta que respondía cada texto (ADICIONAL: lo que agregó al final); "
        "los «No sé» y «No aplica» solo se cuentan.")


def _fechas(valor: Any) -> tuple[date | None, date | None]:
    texto = str(valor or "").strip().lower()
    if not texto:
        return None, None
    hoy = date.today()
    if texto == "hoy":
        return hoy, hoy
    if texto == "ayer":
        return hoy - timedelta(days=1), hoy - timedelta(days=1)
    dia = re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})", texto)
    if dia:
        d = date.fromisoformat(texto)
        return d, d
    mes = re.fullmatch(r"(\d{4})-(\d{2})", texto)
    if mes:
        inicio = date(int(mes.group(1)), int(mes.group(2)), 1)
        fin = (inicio.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)
        return inicio, fin
    raise ValueError("Usa la fecha como AAAA-MM-DD, AAAA-MM, «hoy» o «ayer».")


class ObservacionesGrupoToolProvider(ToolProvider):
    def __init__(self, servicio: ObservacionesGrupo, usuarios: UserDirectory | None = None) -> None:
        self._servicio = servicio
        self._usuarios = usuarios

    def _con_sesion(self) -> bool:
        """La sesión de quien pregunta, verificada con el backend (el token lo
        manda el chat web; Telegram no manda ninguno)."""
        autorizacion = autorizacion_actual.get()
        if not autorizacion or self._usuarios is None:
            return False
        try:
            return self._usuarios.usuario_por_token(autorizacion) is not None
        except Exception:
            logger.warning("GRUPO no se pudo verificar la sesión con el backend", exc_info=True)
            return False

    def list_tools(self) -> list[ToolSpec]:
        return [ToolSpec(
            name="consultar_observaciones_grupo",
            description=(
                "Fotos e imágenes enviadas a los grupos de observación de Telegram (por ejemplo el del "
                "sitio Paramox) y las descripciones y percepciones que las personas escribieron o dijeron "
                "en audio sobre ellas. Sin 'foto' devuelve una lista breve (número, fecha y hora, título y "
                "cuántas descripciones tiene cada foto); con 'foto' (o ultima=true) devuelve esa foto con sus "
                "descripciones, lo que la IA ve en ella y la imagen. Filtra por sitio y fecha. Es solo lo de "
                "Telegram: los archivos subidos desde el chat se consultan con consultar_archivos_subidos. Con "
                "exportar=true entrega el Excel: cada respuesta a las preguntas de cada foto con su fecha, "
                "autor, ubicación y los atributos del sitio que menciona (cielo, agua, suelo, vegetación, "
                "olor...), una fila por persona y foto, las fotos y el listado de preguntas. Solo funciona con "
                "la sesión iniciada en la plataforma."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "sitio": {"type": "string", "description": "Nombre del sitio del grupo, por ejemplo Paramox"},
                    "fecha": {"type": "string", "description": "AAAA-MM-DD, AAAA-MM, «hoy» o «ayer»"},
                    "foto": {"type": "integer", "description": "Número de la foto si se pregunta por una concreta"},
                    "ultima": {"type": "boolean", "description": "true si se pregunta por la última foto enviada"},
                    "exportar": {"type": "boolean"},
                },
            },
        )]

    def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        if name != "consultar_observaciones_grupo":
            return {"error": f"Herramienta desconocida: {name}"}
        if not self._con_sesion():
            return dict(SIN_SESION)
        try:
            desde, hasta = _fechas(arguments.get("fecha"))
        except ValueError as exc:
            return {"error": str(exc)}
        foto = arguments.get("foto")
        foto = int(foto) if foto not in (None, "") else None
        sitio = str(arguments.get("sitio") or "").strip()
        filas = self._servicio.consultar(sitio, desde, hasta, foto)
        if arguments.get("ultima") and foto is None and filas:
            fotos = [f for f in filas if f["tipo"] == "foto"]
            if fotos:
                foto, chat = fotos[-1]["mensaje"], fotos[-1]["chat_id"]
                filas = [f for f in filas if f["foto"] == foto and f["chat_id"] == chat]
        if foto is not None and len({f["chat_id"] for f in filas}) > 1:
            sitios_foto = sorted({f["sitio"] for f in filas})
            return {"aclarar": f"La foto {foto} existe en varios grupos ({', '.join(sitios_foto)}). "
                               "Pregunta de qué sitio es y vuelve a consultar con 'sitio'."}
        if not filas:
            grupos = [g["sitio"] for g in self._servicio.grupos()]
            return {"sin_datos": True,
                    "nota": "No hay mensajes de grupos de observación con esos criterios. "
                            + (f"Grupos registrados: {', '.join(grupos)}." if grupos else "No hay grupos registrados.")}
        datos = resumir(filas)
        salida = _detalle(datos) if foto is not None else _lista(datos)
        if arguments.get("exportar"):
            sitios = sorted({f["sitio"] for f in filas})
            salida["exportar"] = {"titulo": "Respuestas", "filas": _filas_mensajes(filas),
                                  # El archivo lleva el nombre del grupo (su sitio) de donde son las fotos.
                                  "nombre": _slug(sitios[0]) if len(sitios) == 1 else "varios-grupos",
                                  "hojas_extra": [
                                      {"titulo": "Por persona y foto", "filas": _filas_por_persona(filas)},
                                      {"titulo": "Caracterización", "filas": _filas_caracterizacion(filas)},
                                      {"titulo": "Fotos", "filas": _filas_fotos(filas)},
                                      {"titulo": "Descripción IA", "filas": _filas_descripcion_ia(filas),
                                       "ajustar_texto": True},
                                      {"titulo": "Preguntas", "filas": _filas_preguntas(filas)},
                                  ]}
        return salida


def _titulo(foto: dict[str, Any]) -> str:
    """El pie de la foto o, si no tiene, la primera frase de lo que ve la IA."""
    texto = (foto.get("pie_de_foto") or foto.get("lo_que_ve_la_ia") or "").strip()
    texto = re.split(r"(?<=[.!?])\s", texto, maxsplit=1)[0] if texto else f"Foto {foto['foto']}"
    return texto if len(texto) <= MAX_TITULO else texto[:MAX_TITULO].rsplit(" ", 1)[0] + "…"


def _lista(datos: dict[str, Any]) -> dict[str, Any]:
    """Una línea por foto, sin textos ni imágenes: con muchas fotos la respuesta
    sería enorme. Para ver una, se pregunta por su número."""
    fotos = [f for f in datos["por_foto"] if isinstance(f["foto"], int)]
    lista = [{"sitio": f["sitio"], "foto": f["foto"], "fecha": f["foto_enviada"], "titulo": _titulo(f),
              "descripciones": f["descripciones"], "personas": f["personas"]} for f in fotos]
    salida = {"sitios": sorted({f["sitio"] for f in fotos}), "total_fotos": len(lista),
              "fotos": lista[-MAX_FOTOS_LISTA:],
              "nota": ("Responde con una lista breve: una línea por foto con su número, fecha y hora y título "
                       "(y el sitio, si hay fotos de varios sitios), "
                       "y cuántas descripciones tiene. No pegues las descripciones. Ofrece ver una foto concreta "
                       "(por su número o «la última») o descargar todo en Excel.")}
    if len(lista) > MAX_FOTOS_LISTA:
        salida["nota_muestra"] = f"Se muestran las {MAX_FOTOS_LISTA} fotos más recientes de {len(lista)}."
    return salida


def _detalle(datos: dict[str, Any]) -> dict[str, Any]:
    """Una foto: sus descripciones, lo que ve la IA y la imagen (debajo de la respuesta)."""
    sources = []
    for foto_ in datos["por_foto"]:
        archivo = foto_.pop("archivo", None)
        if archivo and (FOTO_DE_GRUPO.match(archivo) or archivo.startswith(PREFIJO_IMAGENES_ANTIGUO)):
            sources.append({"source": archivo, "score": 1.0,
                            "content": f"Foto {foto_['foto']} del grupo {foto_['sitio']}: {_titulo(foto_)} "
                                       f"({foto_['descripciones']} descripciones)"})
            foto_["imagen"] = "se muestra y se puede descargar debajo de la respuesta"
        if len(foto_["textos"]) > MAX_TEXTOS_POR_FOTO:
            foto_["nota_muestra"] = f"Se muestran {MAX_TEXTOS_POR_FOTO} de {len(foto_['textos'])} descripciones."
            foto_["textos"] = foto_["textos"][:MAX_TEXTOS_POR_FOTO]
        for t in foto_["textos"]:
            t.pop("atributos", None)  # los atributos van en el Excel, no cargan al modelo
    datos["nota"] = NOTA
    if sources:
        datos["sources"] = sources
    return datos


def _slug(texto: str) -> str:
    """«Laguna Verde» → «laguna-verde» (igual que las carpetas de cada sitio en el bucket)."""
    import unicodedata
    plano = unicodedata.normalize("NFKD", texto or "").encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", "-", plano).strip("-")[:40] or "grupo"


def _celda(valor: Any) -> str:
    if valor is None:
        return ""
    return ", ".join(valor) if isinstance(valor, list) else str(valor)


ESTADOS = {"respondida": "respondida", "no_sabe": "no sé", "no_aplica": "no aplica",
           "no_responde": "prefiere no responder"}
ESPECIALES = {"no_sabe": "No sé", "no_aplica": "No aplica", "no_responde": "Prefiero no responder"}


def _es_respuesta(f: dict[str, Any]) -> bool:
    """Lo que una persona dijo sobre una foto (o un «No sé» / «No aplica»).
    Los saludos y mensajes que no describen la foto no van al Excel."""
    return (f["tipo"] in ("texto", "audio") and f.get("es_descripcion") is not False
            and f.get("pregunta_alcance") != preguntas_foto.PERSONA)


def _es_caracterizacion(f: dict[str, Any]) -> bool:
    return f["tipo"] in ("texto", "audio") and f.get("pregunta_alcance") == preguntas_foto.PERSONA


def _listado() -> list[tuple[str, str]]:
    """(código, categoría) de todo el listado, también las inactivas, en su orden."""
    filas = []
    for p in preguntas_foto.todas():
        try:
            orden = float(p.get("orden") or 0)
        except ValueError:
            orden = 0
        if p.get("codigo"):
            filas.append((orden, p["codigo"].upper(), p.get("categoria", "")))
    return [(codigo, categoria) for _, codigo, categoria in sorted(filas)]


def _categorias() -> dict[str, str]:
    return dict(_listado()) | {preguntas_foto.CIERRE: "Pregunta abierta"}


def _filas_mensajes(filas: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Hoja «Respuestas»: un renglón por respuesta (o descripción libre, en lo
    anterior a las preguntas), con su pregunta, cómo respondió, lo que dijo y
    los atributos del sitio que menciona. Las fotos van en la hoja «Fotos»."""
    categorias = _categorias()
    salida = []
    for f in filas:
        if not (_es_respuesta(f) or f["tipo"] == "ubicacion"):
            continue
        atributos = f.get("atributos") or {}
        codigo = f.get("pregunta_codigo") or ""
        fila = {"registro": f["registro"], "sitio": f["sitio"], "foto": f["foto"], "fecha": f["fecha"],
                "autor": f["autor"], "canal": f.get("canal") or "",
                "pregunta_codigo": codigo, "categoria": categorias.get(codigo, ""),
                "pregunta": f.get("pregunta_texto") or "",
                "estado_respuesta": ESTADOS.get(f.get("estado_respuesta") or "", ""),
                "respuesta": descripcion(f), "tipo": f["tipo"],
                "transcripcion_whisper": f["transcripcion"] or "",
                "transcripcion_corregida": f["transcripcion_corregida"] or "",
                "certeza": atributos.get("certeza", ""),
                "latitud": f.get("latitud") if f.get("latitud") is not None else "",
                "longitud": f.get("longitud") if f.get("longitud") is not None else "",
                "origen_ubicacion": f.get("origen_ubicacion") or ""}
        fila.update({campo: _celda(atributos.get(campo)) for campo in CAMPOS})
        salida.append(fila)
    return salida


def _orden_codigos(filas: list[dict[str, Any]]) -> list[str]:
    """Los códigos de pregunta presentes, en el orden del listado; los que ya no
    están en el listado van después y ADICIONAL al final."""
    presentes = {f.get("pregunta_codigo") for f in filas if f.get("pregunta_codigo")}
    orden = [codigo for codigo, _ in _listado() if codigo in presentes]
    orden += sorted(presentes - set(orden) - {preguntas_foto.CIERRE})
    return orden + ([preguntas_foto.CIERRE] if preguntas_foto.CIERRE in presentes else [])


def _filas_por_persona(filas: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Hoja «Por persona y foto»: una fila por persona y foto, con una columna
    por pregunta (su código; el texto está en la hoja «Preguntas»)."""
    respuestas = [f for f in filas if _es_respuesta(f)]
    codigos = _orden_codigos(respuestas)
    libre = any(not f.get("pregunta_codigo") for f in respuestas)
    enviadas = {(f["chat_id"], f["mensaje"]): f["fecha"] for f in filas if f["tipo"] == "foto"}
    personas: dict[tuple, dict[str, Any]] = {}
    for f in respuestas:
        clave = (f["chat_id"], f["foto"], f["autor_id"] or f["autor"])
        fila = personas.get(clave)
        if fila is None:
            fila = {"sitio": f["sitio"], "foto": f["foto"], "foto_enviada": enviadas.get((f["chat_id"], f["foto"]), ""),
                    "autor": f["autor"], "canal": f.get("canal") or ""}
            fila.update({c: "" for c in codigos})
            if libre:
                fila["descripcion_libre"] = ""
            fila.update({"preguntas_respondidas": 0, "no_se": 0, "no_aplica": 0, "no_responde": 0, "latitud": "", "longitud": ""})
            personas[clave] = fila
        estado = f.get("estado_respuesta")
        texto = ESPECIALES.get(estado) or descripcion(f)
        columna = f.get("pregunta_codigo") or "descripcion_libre"
        if texto:
            fila[columna] = f"{fila[columna]} | {texto}" if fila[columna] else texto
        if estado in SIN_DESCRIPCION:
            fila[{"no_sabe": "no_se"}.get(estado, estado)] += 1
        if f.get("latitud") is not None:
            fila["latitud"], fila["longitud"] = f["latitud"], f["longitud"]
    for fila in personas.values():
        fila["preguntas_respondidas"] = sum(1 for c in codigos if c != preguntas_foto.CIERRE and fila[c])
    return list(personas.values())


def _filas_caracterizacion(filas: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Hoja «Caracterización»: una fila por persona con sus respuestas de la
    categoría 0 (se hacen una sola vez). Datos personales: el Excel solo se
    descarga con la sesión iniciada en la plataforma."""
    respuestas = [f for f in filas if _es_caracterizacion(f)]
    if not respuestas:
        return []
    codigos = _orden_codigos(respuestas)
    personas: dict[Any, dict[str, Any]] = {}
    for f in respuestas:
        fila = personas.get(f["autor_id"] or f["autor"])
        if fila is None:
            fila = {"autor": f["autor"], "respondida_el": f["fecha"], "sitio": f["sitio"], "foto": f["foto"]}
            fila.update({c: "" for c in codigos})
            personas[f["autor_id"] or f["autor"]] = fila
        texto = ESPECIALES.get(f.get("estado_respuesta")) or descripcion(f)
        codigo = f["pregunta_codigo"]
        if texto:
            fila[codigo] = f"{fila[codigo]} | {texto}" if fila[codigo] else texto
    return list(personas.values())


def _filas_fotos(filas: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Hoja «Fotos»: cada foto con lo que ve la IA, el día de los datos y
    cuántas personas respondieron."""
    personas: dict[tuple, set] = {}
    respuestas: dict[tuple, int] = {}
    for f in filas:
        if _es_respuesta(f):
            clave = (f["chat_id"], f["foto"])
            personas.setdefault(clave, set()).add(f["autor_id"] or f["autor"])
            respuestas[clave] = respuestas.get(clave, 0) + 1
    salida = []
    for f in filas:
        if f["tipo"] != "foto":
            continue
        clave = (f["chat_id"], f["mensaje"])
        atributos = f.get("ia_atributos") or {}
        fila = {"registro": f["registro"], "sitio": f["sitio"], "foto": f["mensaje"], "fecha": f["fecha"],
                "enviada_por": f["autor"], "pie_de_foto": f.get("texto") or "",
                "lo_que_ve_la_ia": f.get("ia_descripcion") or "",
                "datos_sitio": f.get("dato_sitio") or "", "datos_fecha": f.get("dato_fecha") or "",
                "datos_luz": f.get("dato_luz") or "",
                "latitud": f.get("latitud") if f.get("latitud") is not None else "",
                "longitud": f.get("longitud") if f.get("longitud") is not None else "",
                "origen_ubicacion": f.get("origen_ubicacion") or "",
                "personas_que_respondieron": len(personas.get(clave, ())),
                "respuestas": respuestas.get(clave, 0)}
        fila.update({f"ia_{campo}": _celda(atributos.get(campo)) for campo in CAMPOS})
        salida.append(fila)
    return salida


def _filas_descripcion_ia(filas: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Hoja «Descripción IA»: lo que la IA describe de cada foto con el mayor
    detalle posible, por aspecto (los de las preguntas a las personas, más cielo
    y luz, intervención humana, otros detalles y lo que la foto no deja saber)."""
    salida = []
    for f in filas:
        if f["tipo"] != "foto":
            continue
        detalle = f.get("ia_detalle") or {}
        fila = {"registro": f["registro"], "sitio": f["sitio"], "foto": f["mensaje"], "fecha": f["fecha"]}
        fila.update({campo: detalle.get(campo, "") for campo in CAMPOS_DETALLE_IA})
        if not detalle:
            fila["general"] = "Pendiente: la IA todavía no ha descrito esta foto en detalle."
        fila.update({"descripcion_corta": f.get("ia_descripcion") or "", "modelo": f.get("ia_detalle_modelo") or "",
                     "descrita_el": f.get("ia_detalle_el") or ""})
        salida.append(fila)
    return salida


def _filas_preguntas(filas: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Hoja «Preguntas»: el listado (también las inactivas), la pregunta
    abierta del final y las que se respondieron pero ya no están en el listado."""
    cuantas: dict[str, int] = {}
    textos: dict[str, str] = {}
    for f in filas:
        if (_es_respuesta(f) or _es_caracterizacion(f)) and f.get("pregunta_codigo"):
            cuantas[f["pregunta_codigo"]] = cuantas.get(f["pregunta_codigo"], 0) + 1
            textos.setdefault(f["pregunta_codigo"], f.get("pregunta_texto") or "")
    salida, vistos = [], set()
    for p in preguntas_foto.todas():
        codigo = (p.get("codigo") or "").upper()
        if not codigo:
            continue
        vistos.add(codigo)
        alcance = "una vez por persona" if (p.get("alcance") or "").strip().lower() == "persona" else "cada foto"
        salida.append({"codigo": codigo, "orden": p.get("orden", ""), "alcance": alcance,
                       "categoria": p.get("categoria", ""),
                       "pregunta": p.get("pregunta", ""), "origen_en_el_instrumento": p.get("origen_en_el_instrumento", ""),
                       "activa": p.get("activa", ""), "respuestas_en_este_excel": cuantas.get(codigo, 0)})
    salida.append({"codigo": preguntas_foto.CIERRE, "orden": "al final", "alcance": "cada foto",
                   "categoria": "Pregunta abierta",
                   "pregunta": preguntas_foto.TEXTO_CIERRE, "origen_en_el_instrumento": "", "activa": "si",
                   "respuestas_en_este_excel": cuantas.get(preguntas_foto.CIERRE, 0)})
    vistos.add(preguntas_foto.CIERRE)
    for codigo in sorted(set(cuantas) - vistos):
        salida.append({"codigo": codigo, "orden": "", "alcance": "", "categoria": "", "pregunta": textos.get(codigo, ""),
                       "origen_en_el_instrumento": "", "activa": "ya no está en el listado",
                       "respuestas_en_este_excel": cuantas[codigo]})
    for fila in salida:
        fila["instrumento"] = preguntas_foto.INSTRUMENTO
    return salida
