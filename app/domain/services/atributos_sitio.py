"""Atributos del sitio que se pueden decir o ver en una foto, con los mismos
nombres para las descripciones de la gente y para lo que ve la IA, así se
pueden contar y comparar.

Las categorías siguen el diccionario de términos de COLFLUX (olor, color, suelo
al pisar, sensación ambiental, agua visible, plantas, altitud): cada atributo
apunta a una variable que la plataforma mide (flujo de CH4, humedad relativa,
temperatura, nivel del agua...). Un atributo que nadie menciona queda en null:
no se rellena ni se deduce.
"""
from collections import Counter
from typing import Any

# campo: (valores permitidos, es_lista, qué es — va en el prompt)
CAMPOS: dict[str, tuple[tuple[str, ...], bool, str]] = {
    "ecosistema": (("paramo", "subparamo", "superparamo", "humedal", "turbera", "sabana_inundable", "morichal",
                    "bosque"), False, "tipo de ecosistema o zona"),
    "cielo": (("soleado", "parcialmente_nublado", "nublado", "neblina", "lluvia"), False,
              "estado del cielo o del tiempo"),
    "temperatura": (("frio", "templado", "calor"), False, "sensación de temperatura"),
    "humedad_aire": (("humedo", "seco"), False, "aire húmedo o pesado (bochorno, aire mojado) o aire seco"),
    "viento": (("sin_viento", "brisa", "fuerte"), False, "viento"),
    "agua": (("sin_agua", "charcos", "encharcado", "inundado"), False,
             "agua en superficie: ninguna, charcos, suelo encharcado o zona inundada"),
    "color_agua": (("te_cafe", "negra", "turbia", "clara"), False, "color del agua"),
    "burbujas": (("si", "no"), False, "burbujas o agua que «hierve»"),
    "olor": (("pantano", "huevo_podrido", "tierra_mojada", "quemado", "sin_olor"), False, "olor"),
    "suelo": (("se_hunde", "esponjoso", "blando", "firme", "seco_agrietado"), False, "cómo es el suelo al pisarlo"),
    "color_suelo": (("negro", "cafe", "claro", "rojizo"), False, "color del suelo o del lodo"),
    "vegetacion": (("pajonal", "frailejones", "musgo", "pastos", "juncos", "arbustos", "arboles", "moriches",
                    "cultivo"), True, "tipos de vegetación presentes"),
    "estado_vegetacion": (("verde_intensa", "verde", "mixta", "amarilla_seca"), False,
                          "color o estado de la vegetación"),
    "hojarasca": (("mucha", "poca"), False, "hojas secas en el suelo"),
    "alteracion": (("quema", "ganado", "drenaje", "cultivo", "pisoteo"), True, "señales de intervención humana"),
}
CERTEZAS = ("percibe", "supone")


def esquema_para_prompt() -> str:
    lineas = []
    for campo, (valores, lista, que) in CAMPOS.items():
        forma = f"lista con algunos de: {', '.join(valores)}" if lista else f"uno de: {', '.join(valores)}"
        lineas.append(f'- "{campo}" ({que}): {forma}; null si no se dice.')
    return "\n".join(lineas)


def limpiar(crudo: Any) -> dict[str, Any] | None:
    """Solo campos y valores conocidos; lo demás se descarta. None si no queda nada."""
    if not isinstance(crudo, dict):
        return None
    limpio: dict[str, Any] = {}
    for campo, (valores, lista, _) in CAMPOS.items():
        valor = crudo.get(campo)
        if lista:
            elegidos = [v for v in (valor if isinstance(valor, list) else [valor]) if v in valores]
            if elegidos:
                limpio[campo] = sorted(set(elegidos), key=valores.index)
        elif valor in valores:
            limpio[campo] = valor
    if not limpio:
        return None
    if crudo.get("certeza") in CERTEZAS:
        limpio["certeza"] = crudo["certeza"]
    return limpio


def consenso(por_persona: dict[Any, dict[str, Any]], ia: dict[str, Any] | None) -> list[dict[str, Any]]:
    """Por atributo: el valor más dicho, cuántas personas lo mencionan de cuántas
    describieron, el acuerdo entre quienes lo mencionaron, y si coincide con lo
    que ve la IA en la foto. `por_persona` ya trae un dict por persona (sus
    descripciones de esa foto unidas)."""
    personas = len(por_persona)
    ia = ia or {}
    filas = []
    for campo, (_, lista, _) in CAMPOS.items():
        votos: Counter = Counter()
        mencionan = 0
        for atributos in por_persona.values():
            valor = atributos.get(campo)
            if valor is None:
                continue
            mencionan += 1
            for v in (valor if lista else [valor]):
                votos[v] += 1
        valor_ia = ia.get(campo)
        if not mencionan and valor_ia is None:
            continue
        veces = max(votos.values()) if votos else 0
        # En las listas (vegetación, alteración) van todos los más dichos; en lo demás, uno.
        empatados = [v for v, n in votos.items() if n == veces]
        principal = (empatados if lista else empatados[0]) if votos else None
        if valor_ia is None:
            contraste = "la IA no lo ve"
        elif not mencionan:
            contraste = "nadie lo dice"
        else:
            vistos = set(valor_ia if isinstance(valor_ia, list) else [valor_ia])
            dichos = set(principal if lista else [principal])
            contraste = "coincide" if dichos & vistos else "contradice"
        filas.append({
            "atributo": campo,
            "valor": principal,
            "personas_que_lo_mencionan": mencionan,
            "personas_que_describieron": personas,
            "acuerdo": round(veces / mencionan, 2) if mencionan else None,
            "otros_valores": {v: n for v, n in votos.items() if v not in empatados},
            "ia": valor_ia,
            "contraste_con_ia": contraste,
        })
    return filas


def unir(atributos: list[dict[str, Any]]) -> dict[str, Any]:
    """Une las descripciones de una misma persona: en las listas suma, en lo
    demás vale lo último que dijo."""
    unido: dict[str, Any] = {}
    for a in atributos:
        for campo, valor in (a or {}).items():
            if campo not in CAMPOS:
                continue
            if CAMPOS[campo][1]:
                unido[campo] = sorted(set(unido.get(campo, [])) | set(valor))
            else:
                unido[campo] = valor
    return unido
