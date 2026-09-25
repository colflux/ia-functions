"""El bucle de tool-calling: le da al modelo una lista cerrada de
herramientas (locales y remotas, vía el ToolRegistry), ejecuta las que
decida usar, y deja que redacte la respuesta final con esos resultados.
El modelo nunca ejecuta nada directamente.

Reemplaza (sin sus bugs) al `responder()` que existía en el módulo de IA
del backend: la variable de "pendiente de confirmar" siempre está definida,
la clave de resultado es consistente entre el orquestador y quien la
consume, y el schema de argumentos de cada tool sí llega completo al
modelo."""

import json
import logging
import unicodedata
import re

from app.domain.models import Message, PendingConfirmation, ToolCall
from app.domain.ports.conversation_repository import ConversationRepository
from app.domain.ports.llm_provider import LLMProvider
from app.domain.services.bienvenida import respuesta_fija
from app.domain.services.contexto import autorizacion_actual
from app.domain.services.tool_registry import ToolRegistry

logger = logging.getLogger("uvicorn.error")

MAX_TURNS = 7
HISTORY_TURNS = 6
HISTORY_MINUTES = 30
HISTORY_MAX_CHARS = 6000

# Si la pregunta se parece mucho a un término del diccionario de campo, su
# definición se entrega al modelo desde el principio (ver _consultar_diccionario).
SIEMPRE_VISIBLES = {"listar_sitios"}
HERRAMIENTA_DICCIONARIO = "buscar_diccionario"
PARECIDO_DICCIONARIO_PREVIO = 0.86
MAX_TERMINOS_PREVIOS = 2

CONFIRMATIONS = {"confirmo", "confirmar", "si confirmo", "sí confirmo", "sí, confirmo"}

SYSTEM_PROMPT = (
    "Eres el asistente de COLFLUX, un proyecto de investigación sobre las "
    "dinámicas del carbono en los ecosistemas colombianos NO forestales: páramos, "
    "humedales, sabanas inundables, morichales y sus suelos orgánicos. "
    "Responde en español, breve y concreto, en texto plano, sin LaTeX ni asteriscos. "
    "Usa las herramientas cuando la pregunta necesite datos; no respondas de memoria. "
    "Si la persona describe algo con palabras de campo (olores, colores, texturas, "
    "frases como que el suelo respira fuerte o que el humedal está hirviendo), busca "
    "primero en el diccionario qué significa y después consulta las mediciones. "
    "Nunca inventes cifras ni unidades: si una herramienta no declara la unidad, di "
    "que no está declarada. "
    "No calcules promedios, sumas ni totales por tu cuenta sobre filas parciales: di "
    "cuántos registros hay en total y aclara que lo mostrado es una muestra. "
    "Cuando cites un dato, menciona su fuente. "
    "No inventes secciones, menús, botones, niveles de acceso ni funciones de la "
    "plataforma: si algo no sale de tus herramientas o documentos, di que no tienes "
    "esa información. "
    "Los archivos que las personas suben desde el chat se consultan con "
    "consultar_archivos_subidos, también cuando piden descargarlos. Si las mediciones "
    "de la plataforma no tienen el dato, revisa esos archivos antes de decir que no "
    "hay; si el dato sale de ahí, dilo y aclara que no ha pasado por la validación "
    "del ETL. "
    "Sé puntual: responde primero y directo lo que se preguntó, en pocas frases "
    "(unas 120 palabras como máximo) salvo que pidan más detalle. No uses tablas ni "
    "marcas de cita, y no agregues conteos ni valores de ejemplo que no se pidieron. "
    "No afirmes relaciones que los datos no muestran: si algo no se puede comprobar "
    "con los datos, dilo en una frase. Las interpretaciones de expresiones de campo "
    "salen del diccionario; si una no está, dilo y no agregues una interpretación "
    "propia. Si el diccionario trae un término parecido pero no el mismo, di que el "
    "término exacto no está y presenta el parecido con su propio nombre, sin mezclarlos. "
    "Menciona los archivos subidos solo si contienen el dato que se pidió. "
    "No ofrezcas datos, sitios ni consultas que las herramientas indican que no existen; "
    "si un sitio está registrado pero sin mediciones, dilo así. "
    "Si la persona dicta una medición que hizo («hoy medí…»), usa registrar_medicion y "
    "nunca digas que quedó guardada: se guarda solo cuando escribe «confirmo». Las "
    "mediciones dictadas en el chat se consultan con consultar_mediciones_chat. "
    "Después de responder con mediciones o datos de campo, ofrece en una frase "
    "descargarlos en Excel y pregunta si quiere agregar otros datos (otro gas, otras "
    "fechas u otros sitios). Si acepta, llama de nuevo a las herramientas de esos datos "
    "con exportar=true, una vez por cada conjunto pedido: todo queda en un solo Excel. "
    "No ofrezcas Excel para listas de sitios ni cuando no hubo datos. "
    "Nunca conviertas valores entre unidades (nmol, umol, g): da cada valor en la "
    "unidad en que viene y compara solo dentro de una misma unidad. "
    "Si mencionan un nombre que puede ser un sitio, vereda o municipio, búscalo con "
    "listar_sitios; SWAMP e IDEAM son proyectos, no sitios. Nombres de personas, "
    "testimonios y entrevistas se buscan en los documentos. Nunca digas que no tienes "
    "información sin haber consultado antes una herramienta, salvo que la pregunta no "
    "tenga que ver con COLFLUX. "
    "Si la pregunta no tiene que ver con COLFLUX, sus ecosistemas o sus datos, dilo "
    "en una frase y ofrece ayuda con la plataforma."
)



def _acotar_historial(mensajes: list[Message], tope: int) -> list[Message]:
    """Deja los mensajes más recientes que quepan en tope caracteres. Acotar por
    tamaño y no solo por número de turnos es lo que evita que una sola respuesta
    larga (un listado de sitios, por ejemplo) arrastre el límite de tokens del
    proveedor en las preguntas siguientes."""
    acumulado = 0
    recortado: list[Message] = []
    for m in reversed(mensajes):
        largo = len(m.text or "")
        if recortado and acumulado + largo > tope:
            break
        acumulado += largo
        recortado.append(m)
    return list(reversed(recortado))


def _sin_tablas_ni_citas(texto: str) -> str:
    """Red de seguridad por si el modelo no sigue las reglas de forma: quita las
    marcas de cita (【source: …】) y convierte cada fila de una tabla en una línea
    de texto; el chat muestra texto plano y una tabla se ve como un muro de barras."""
    texto = re.sub(r"【[^】]*】", "", texto)
    lineas = []
    for linea in texto.splitlines():
        celdas = linea.strip()
        if celdas.startswith("|") and celdas.endswith("|"):
            if re.fullmatch(r"\|[\s:|-]+\|", celdas):
                continue  # separador |---|---|
            lineas.append(" — ".join(c.strip() for c in celdas.strip("|").split("|") if c.strip()))
        else:
            lineas.append(linea)
    return re.sub(r"[ \t]+([.,;:])", r"\1", "\n".join(lineas)).strip()


def _texto_plano(texto: str) -> str:
    """Quita el énfasis de Markdown que el modelo añade pese a pedírsele texto
    plano en el prompt. El widget del chat no lo interpreta, así que los
    asteriscos se verían tal cual en pantalla. Las instrucciones de formato son
    de las que peor obedecen los modelos: sale más fiable limpiarlo aquí."""
    limpio = re.sub(r"\*\*(.+?)\*\*", r"\1", texto or "")
    limpio = re.sub(r"(?<!\*)\*(?!\s)([^*\n]+?)\*", r"\1", limpio)
    limpio = re.sub(r"^\s{0,3}#{1,6}\s+", "", limpio, flags=re.MULTILINE)
    return limpio

def _normalizar_termino(texto: str) -> str:
    """Minúsculas, sin tildes, comillas ni signos: «“El humedal está hirviendo”» → «el humedal esta hirviendo»."""
    base = unicodedata.normalize("NFKD", texto or "").encode("ascii", "ignore").decode().lower()
    return " ".join(re.sub(r"[^a-z0-9 ]", " ", base).split())


class AgentOrchestrator:
    def __init__(
        self,
        llm: LLMProvider,
        tools: ToolRegistry,
        conversations: ConversationRepository,
        router=None,
        reglas=None,
        descargas=None,
    ) -> None:
        self._llm = llm
        self._tools = tools
        self._conversations = conversations
        self._router = router
        self._reglas = reglas
        self._descargas = descargas

    def respond(self, question: str, external_user_id: str, autorizacion: str | None = None) -> dict:
        # La sesión la leen las herramientas que escriben (registrar mediciones);
        # el modelo no la ve.
        marca = autorizacion_actual.set(autorizacion)
        try:
            return self._responder(question, external_user_id)
        finally:
            autorizacion_actual.reset(marca)

    def _responder(self, question: str, external_user_id: str) -> dict:
        text = (question or "").strip()

        if text.lower().rstrip(".!") in CONFIRMATIONS:
            return self._resolve_confirmation(external_user_id)

        fija = respuesta_fija(text)
        if fija:
            self._conversations.save_turn(external_user_id, text, fija, [], None)
            return {"answer": fija, "sources": [], "tools_used": [], "pending_confirmation": None}

        history = self._conversations.recent_messages(external_user_id, HISTORY_TURNS, HISTORY_MINUTES)
        messages = [*_acotar_historial(history, HISTORY_MAX_CHARS), Message(role="user", text=text)]

        tools_used: list[str] = []
        sources: list[dict] = []
        pending: PendingConfirmation | None = None
        reply = None
        hojas_excel: list[dict] = []

        todas = self._tools.list_tools()
        visibles = self._router.elegir(text, todas) if self._router else todas
        # listar_sitios siempre va: un nombre propio suelto («¿dónde queda Calostros?»)
        # no se parece a ninguna descripción y el modelo contestaba que no sabía.
        visibles = [*visibles, *[t for t in todas if t.name in SIEMPRE_VISIBLES and t not in visibles]]
        if self._consultar_diccionario(text, {t.name for t in todas}, messages, tools_used, sources):
            # La herramienta ya aparece en la conversación: se declara también.
            visibles = [*visibles, *[t for t in todas if t.name == HERRAMIENTA_DICCIONARIO and t not in visibles]]

        for vuelta in range(MAX_TURNS):
            # La primera vuelta va con la seleccion del enrutador, que es donde esta
            # el ahorro. Si hace falta una segunda, la pregunta ya demostro que
            # necesita varios pasos: ahi importa mas no dejar al modelo sin la
            # herramienta que le falta que ahorrar tokens. Quitarlas del todo se
            # probo y se descarto: con el historial lleno de llamadas el modelo
            # imitaba el protocolo en texto plano e inventaba resultados.
            declaradas = visibles if vuelta == 0 else todas
            reply = self._llm.converse(messages, declaradas, SYSTEM_PROMPT)
            if not reply.tool_calls:
                break

            messages.append(Message(role="assistant", text=reply.text, tool_calls=reply.tool_calls))
            encadenadas: list[dict] = []
            for call in reply.tool_calls:
                tools_used.append(call.name)
                result = self._tools.call_tool(call.name, call.arguments)
                logger.info("HERRAMIENTA %s | args=%s | resultado=%s", call.name, call.arguments, str(result)[:400])
                encadenada = result.pop("consultar_tambien", None)
                if encadenada:
                    encadenadas.append(encadenada)

                result_sources = result.pop("sources", None)
                if result_sources:
                    sources.extend(result_sources)
                # Si la pregunta ya es sobre una expresión del diccionario, esa es la
                # relación que importa: no se añade otra por regla.
                if HERRAMIENTA_DICCIONARIO not in tools_used:
                    self._anotar_diccionario(call.name, call.arguments, result, sources)

                proposal = result.pop("pending_confirmation", None)
                herramienta_confirmar = result.pop("confirmar_con", None)
                if proposal and herramienta_confirmar:
                    pending = PendingConfirmation(id="", tool_name=herramienta_confirmar, arguments=proposal)

                # Filas completas para el Excel: no pasan por el modelo (serían miles),
                # se guardan aparte y el modelo solo sabe que quedaron incluidas.
                exportar = result.pop("exportar", None)
                if exportar:
                    hojas_excel.append(exportar)
                    result["excel"] = (f"Incluido en el Excel: hoja «{exportar.get('titulo')}» con "
                                       f"{len(exportar.get('filas') or [])} filas. El botón de descarga aparece "
                                       "debajo de tu respuesta; no pegues las filas en el texto.")

                messages.append(
                    Message(
                        role="tool",
                        tool_call_id=call.id,
                        tool_name=call.name,
                        tool_result=result,
                    )
                )
            self._encadenar(encadenadas, {t.name for t in todas}, messages, tools_used, sources)

        respaldo = (
            "Encontré información relacionada, pero no alcancé a armar la respuesta. "
            "Pregúntamelo de nuevo de forma más concreta, o por partes."
            if sources else
            "No pude completar la consulta con las herramientas disponibles. "
            "Intenta reformular la pregunta o indicar el sitio por su nombre o número."
        )
        answer = (reply.text if reply else "") or respaldo
        answer = _sin_tablas_ni_citas(_texto_plano(answer))
        unique_sources = self._dedupe_sources(sources)
        descargas = []
        if hojas_excel:
            try:
                excel = self._descargas.guardar(hojas_excel) if self._descargas else None
            except Exception:
                logger.exception("EXCEL no se pudo generar")
                excel = None
            if excel:
                descargas.append(excel)
            else:
                answer += "\n\nNo se pudo preparar el Excel en este momento; intenta de nuevo más tarde."

        if pending:
            self._conversations.save_turn(external_user_id, text, answer, tools_used, pending)
        else:
            self._conversations.save_turn(external_user_id, text, answer, tools_used, None)

        return {
            "answer": answer,
            "sources": unique_sources,
            "tools_used": list(dict.fromkeys(tools_used)),
            "pending_confirmation": pending.arguments if pending else None,
            "descargas": descargas,
        }

    def _resolve_confirmation(self, external_user_id: str) -> dict:
        pending = self._conversations.latest_pending(external_user_id)
        if not pending:
            answer = "No hay ninguna propuesta pendiente de confirmar."
            self._conversations.save_turn(external_user_id, "confirmo", answer, [], None)
            return {"answer": answer, "sources": [], "tools_used": [], "pending_confirmation": None}

        if not self._tools.tiene(pending.tool_name):
            # Una propuesta guardada con una herramienta que ya no existe no se puede
            # completar; sin esto, el registro devolvía su error interno y la
            # persona lo leía como si fuera la respuesta.
            answer = "Esa propuesta ya no se puede completar. No se guardó nada."
            self._conversations.resolve_pending(pending.id)
            self._conversations.save_turn(external_user_id, "confirmo", answer, [], None)
            return {"answer": answer, "sources": [], "tools_used": [], "pending_confirmation": None}

        result = self._tools.call_tool(pending.tool_name, pending.arguments)
        self._conversations.resolve_pending(pending.id)

        answer = result.get("mensaje") or result.get("error", "Listo.")
        self._conversations.save_turn(external_user_id, "confirmo", answer, [pending.tool_name], None)
        return {"answer": answer, "sources": [], "tools_used": [pending.tool_name], "pending_confirmation": None}

    def _consultar_diccionario(self, texto: str, disponibles: set[str], messages: list[Message],
                               tools_used: list[str], sources: list[dict]) -> bool:
        """Si la pregunta se parece mucho a un término del diccionario de campo, su
        definición se le entrega al modelo desde el principio, como si la hubiera
        pedido. Dejándolo decidir, ante «¿qué es la turba desnuda?» respondía de
        memoria con una definición propia, aunque el término está en el diccionario.
        El umbral es alto para no cargar definiciones en preguntas de datos."""
        if HERRAMIENTA_DICCIONARIO not in disponibles:
            return False
        try:
            resultado = self._tools.call_tool(HERRAMIENTA_DICCIONARIO, {"texto": texto})
        except Exception:  # sin diccionario la pregunta se atiende igual
            logger.exception("DICCIONARIO PREVIO falló")
            return False
        pregunta = _normalizar_termino(texto)
        # Solo en preguntas de definición: en «mediciones del páramo de Guerrero»
        # nombrar «páramo» no pide su definición.
        es_definicion = bool(re.search(r"\b(que (es|son|significa|quiere decir)|define|definicion|significado)\b", pregunta))

        def nombrado(fila: dict) -> bool:
            # «¿qué es un páramo?» nombra el término «Páramo» tal cual, aunque el
            # parecido por significado no llegue al umbral: también cuenta.
            titulo = re.sub(r"^diccionario-\d+\s*", "", str(fila.get("source", "")))
            return es_definicion and any(len(n) >= 5 and n in pregunta for n in map(_normalizar_termino, re.split(r"/", titulo)))

        cercanos = lambda filas: [f for f in filas or []
                                  if f.get("score", 0) >= PARECIDO_DICCIONARIO_PREVIO or nombrado(f)][:MAX_TERMINOS_PREVIOS]
        resultados = cercanos(resultado.get("resultados"))
        if not resultados:
            return False
        logger.info("DICCIONARIO PREVIO %s", [(r.get("source"), round(r.get("score", 0), 3)) for r in resultados])
        llamada = ToolCall(name=HERRAMIENTA_DICCIONARIO, arguments={"texto": texto})
        messages.append(Message(role="assistant", text="", tool_calls=[llamada]))
        messages.append(Message(role="tool", tool_call_id=llamada.id, tool_name=HERRAMIENTA_DICCIONARIO,
                                tool_result={"resultados": resultados,
                                             "nota": "Términos del diccionario de campo muy parecidos a la "
                                                     "pregunta. Si aplican, úsalos y cítalos."}))
        tools_used.append(HERRAMIENTA_DICCIONARIO)
        sources.extend(cercanos(resultado.get("sources")))
        return True

    def _anotar_diccionario(self, nombre: str, argumentos: dict, resultado: dict, sources: list[dict]) -> None:
        """Si el dato que devolvió la herramienta cumple una regla del diccionario
        de campo (p. ej. CH4 ≥ 0.0735 µmol/m²/s → «olor a huevo podrido»), se le
        añade al resultado para que el modelo lo mencione en una frase. La regla
        la evalúa el código, no el modelo: así no inventa relaciones."""
        if self._reglas is None or not isinstance(resultado, dict):
            return
        try:
            if nombre == "consultar_ultima_medicion" and (argumentos.get("categoria") or "flujos") == "flujos":
                gas = str(argumentos.get("variable") or "")
                ultima = resultado.get("ultima") or {}
                candidatos = [(ultima.get("valor"), ultima.get("unidad"), "la última medición")]
            elif nombre == "consultar_mediciones":
                gas = str(argumentos.get("gas") or "CO2")
                candidatos = [(m.get("valor"), unidad, f"el valor más alto en {unidad}")
                              for unidad, m in (resultado.get("mayor_por_unidad") or {}).items()]
            else:
                return
            for valor, unidad, que in candidatos:
                regla = self._reglas.evaluar(gas, unidad, valor)
                if regla:
                    break
            else:
                return
        except Exception:
            logger.exception("REGLAS no se pudo evaluar el resultado de %s", nombre)
            return
        logger.info("REGLAS %s %s %s cumple «%s»", gas, valor, unidad, regla.termino)
        resultado["diccionario_de_campo"] = {
            "termino": regla.termino,
            "dato": f"{que}: {valor} {unidad}",
            "regla": f"{regla.nivel} si el flujo de {regla.gas} es al menos el umbral del diccionario",
            "nota": ("Termina la respuesta con UNA frase breve: según el diccionario de campo, con "
                     f"{que} ({valor} {unidad}) es posible observar «{regla.termino}» en ese lugar. "
                     "No lo presentes como un hecho observado ni lo extiendas."),
        }
        sources.append({"source": regla.fuente, "content": regla.contenido, "score": 1.0})

    def _encadenar(self, encadenadas: list[dict], disponibles: set[str], messages: list[Message],
                   tools_used: list[str], sources: list[dict]) -> None:
        """Consultas que una herramienta pide hacer además de la suya (clave
        "consultar_tambien"). Se ejecutan de una vez, sin esperar a que el modelo
        lo decida: así un "no hay mediciones en la plataforma" llega junto con lo
        que haya en los archivos subidos, y el modelo no puede responder que no
        hay datos sin haber mirado (probado: con solo la indicación, le decía a
        la persona que usara la herramienta en vez de usarla)."""
        hechas: set[tuple[str, str]] = set()
        for pedido in encadenadas:
            nombre = pedido.get("herramienta")
            argumentos = pedido.get("argumentos") or {}
            clave = (nombre, json.dumps(argumentos, sort_keys=True))
            if nombre not in disponibles or clave in hechas:
                continue
            hechas.add(clave)
            llamada = ToolCall(name=nombre, arguments=argumentos)
            resultado = self._tools.call_tool(nombre, argumentos)
            logger.info("ENCADENADA %s | args=%s | resultado=%s", nombre, argumentos, str(resultado)[:400])
            tools_used.append(nombre)
            sources.extend(resultado.pop("sources", None) or [])
            messages.append(Message(role="assistant", text="", tool_calls=[llamada]))
            messages.append(Message(role="tool", tool_call_id=llamada.id, tool_name=nombre, tool_result=resultado))

    @staticmethod
    def _dedupe_sources(sources: list[dict]) -> list[dict]:
        unique: dict[tuple, dict] = {}
        for s in sources:
            key = (s["source"], s["content"][:80])
            if key not in unique or s["score"] > unique[key]["score"]:
                unique[key] = s
        return sorted(unique.values(), key=lambda s: s["score"], reverse=True)
