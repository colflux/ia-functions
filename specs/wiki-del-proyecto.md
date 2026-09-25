# La wiki del proyecto como fuente del asistente

## Introducción

El asistente usa como fuente la wiki del proyecto, https://colflux.github.io/context/:
todas sus páginas y los PDF que enlazan. La wiki se vuelve a leer sola cada día, y solo
se recalcula lo que cambió.

## Contexto

La wiki reúne lo que es COLFLUX como proyecto: presentación, objetivos, gestión,
roadmap y catálogo de funcionalidades, cómo se mide el carbono, el diplomado,
territorios, encuentros, noticias, preguntas frecuentes y la arquitectura de la
plataforma. Hasta ahora el asistente no la conocía. A preguntas como «¿qué es
COLFLUX?» o «¿qué es el flujo de carbono?» respondía con el párrafo del prompt o
decía que no tenía información.

Decisiones:

- **Toda la wiki**, incluida la documentación técnica y las tareas del equipo.
- **Actualización automática diaria.**

## Qué hace

### Lectura de la wiki

La wiki es un sitio MkDocs Material, y `search/search_index.json` trae el texto de
todas sus páginas por secciones. `domain/services/wiki.py` (`SincronizadorWiki`):

1. Descarga el índice y arma el texto de cada página: título, URL y secciones en texto
   plano, con listas y tablas legibles.
2. El índice no trae enlaces, así que descarga el HTML de cada página, busca los
   enlaces a PDF y los lee con el mismo extractor de la subida de archivos
   (`extraer_texto`).
3. Guarda cada página y cada PDF en la colección `documents` con su **URL como
   fuente**. `buscar_documentos` los encuentra junto a los archivos subidos, y el chat
   muestra la URL en «fuentes».

### Solo lo que cambió

- `RagService.actualizar_fuente` compara los fragmentos nuevos con los guardados. Si
  son iguales no recalcula nada; si cambiaron, calcula los vectores y reemplaza la
  fuente en una sola transacción (`VectorStore.reemplazar`). Nunca queda una página a
  medias.
- Las páginas que ya no están en la wiki se retiran del índice.
- Si la wiki no responde o el índice llega vacío, no se borra nada. Si un PDF o una
  página no se pudo leer, lo que ya estaba indexado se conserva.

### Cuándo se sincroniza

- Cada `WIKI_INTERVALO_HORAS` (24 por defecto). La primera vez que se usa
  `buscar_documentos` después de ese intervalo, la sincronización arranca en un hilo
  aparte y la pregunta no espera. Tras reiniciar el asistente se revisa en la primera
  búsqueda; como solo se recalcula lo que cambió, cuesta poco.
- `python -m app.scripts.sincronizar_wiki` sincroniza de inmediato y muestra qué se
  actualizó. Se usa para la primera carga o después de cambiar la wiki.
- `WIKI_URL` vacío desactiva la wiki.

### Asistente

- La descripción de `buscar_documentos` nombra los temas de la wiki, para que el
  enrutador de herramientas la elija ante preguntas sobre el proyecto.
- Regla en el prompt: lo que es COLFLUX como proyecto se busca en la wiki y se dice
  que sale de ahí.

### Archivos

| Archivo | Cambio |
|---------|--------|
| `app/domain/services/wiki.py` | Nuevo: lectura y sincronización |
| `app/adapters/web/descarga_publica.py` | Nuevo: descarga HTTP (30 s, máx. 25 MB) |
| `app/scripts/sincronizar_wiki.py` | Nuevo: sincronización manual |
| `app/domain/services/rag_service.py` | `actualizar_fuente` |
| `app/domain/ports/vector_store.py`, `adapters/vectorstore/pgvector_store.py` | `fragmentos`, `reemplazar` |
| `app/adapters/tools/rag_tool_provider.py` | Descripción y disparo de la sincronización |
| `app/bootstrap/container.py`, `app/config.py`, `.env.example` | `get_wiki`, `WIKI_URL`, `WIKI_INTERVALO_HORAS` |
| `app/domain/services/agent_orchestrator.py` | Regla del prompt |

## Plan por fases

1. **Lectura e indexación** de páginas y PDF, con reemplazo por fuente.
2. **Sincronización diaria** sin bloquear el chat y script manual.
3. **Uso en el asistente**: descripción de la herramienta y regla del prompt.
4. **Futuro**: si la wiki crece mucho, sincronizar desde el push del repositorio de
   la wiki (webhook) en vez de revisar cada día.

## Verificación

- Pruebas locales con el índice real de la wiki (24 páginas): texto por página
  correcto, segunda pasada sin cambios (0 actualizadas), página retirada de la wiki
  retirada del índice, página caída sin borrar los PDF, archivos subidos
  (`documentos/`) sin tocar.
- Laboratorio: `sincronizar_wiki` y preguntas «¿qué es COLFLUX?», «¿qué es el flujo de
  carbono?», «¿cuál es el roadmap?», «¿cómo se mide el carbono?», con la URL de la wiki
  en fuentes.

## Al desplegar

- Reiniciar `api` (sin dependencias nuevas; `mcp-backend` no cambia).
- Sin variables obligatorias: por defecto usa https://colflux.github.io/context/ cada 24 h.
- Primera carga: `docker exec ia-functions-api-1 python -m app.scripts.sincronizar_wiki`.
