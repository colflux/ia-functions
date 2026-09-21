# Modelo de embeddings multilingüe

## Contexto

La última línea de `adl.md` deja este punto abierto:

> Embeddings: se mantiene tu decisión (sentence-transformers local), no la suya
> (Gemini) — pendiente de confirmar si la cambias.

Este documento aporta la medición que faltaba para cerrarlo. La propuesta no
cambia el enfoque: los embeddings siguen siendo locales y siguen siendo
sentence-transformers. Lo que cambia es el modelo, por uno que entienda
español.

## El problema

`all-MiniLM-L6-v2` está entrenado únicamente en inglés. Sobre texto en español
no compara significados: se apoya en la coincidencia de palabras, que es
justamente lo que la búsqueda semántica debería superar. Si la pregunta usa
sinónimos, no encuentra el fragmento.

Además trunca la entrada a 256 unidades sin emitir ningún aviso, así que los
fragmentos largos se indexan a medias y nadie se entera.

## La medición

Seis preguntas parafraseadas —sin repetir las palabras del texto original—
contra el diccionario de campo de 48 términos:

```
all-MiniLM-L6-v2       el fragmento correcto cae al 2.º y 3.º puesto
multilingual-e5-base   5 de 6 quedan en primer puesto
                       6 de 6 dentro de los primeros 8 resultados
```

## Qué cambia

```
modelo      all-MiniLM-L6-v2  →  multilingual-e5-base
dimensión   384               →  768 (configurable con EMBEDDING_DIM)
prefijos    ninguno           →  query: al consultar, passage: al indexar
```

Los modelos E5 exigen esos prefijos: distinguen si el texto es una pregunta o
un pasaje del corpus, y sin ellos la calidad cae. El adaptador los añade solo
cuando el nombre del modelo contiene "e5", así que volver a otro modelo no
obliga a tocar código.

## Por qué hace falta migrar la columna

Dos modelos distintos producen vectores que no son comparables entre sí, aunque
coincidan en dimensión. Cambiar solo la variable de entorno dejaría la base de
conocimiento llena de vectores del modelo anterior, y las búsquedas devolverían
resultados sin sentido, sin ningún error visible.

Por eso el arranque compara la dimensión declarada en la tabla con
`EMBEDDING_DIM`. Si no coinciden, vacía la columna conservando el texto, y los
vectores se recalculan en lotes de 32.

## Consecuencias que conviene conocer

- **El primer arranque tras el cambio hace trabajo extra**: recalcular los
  vectores de todo el contenido. Con la base actual es cuestión de segundos.
- **La columna queda sin `NOT NULL`** después de migrar, porque las filas
  existentes pasan un momento sin vector. Es necesario para el diseño.
- **El modelo multilingüe pesa más de 1 GB**, frente a los 90 MB del actual, y
  hoy se descarga en cada despliegue porque no hay volumen de caché. Conviene
  añadir ese volumen antes de fusionar esto.
- **No existe índice vectorial** sobre la columna `embedding`, ni antes ni
  después de este cambio, así que las búsquedas recorren la tabla completa.
  Con el volumen actual no se nota; queda anotado aparte.

## Verificación

Probado en un entorno aislado, con su propia base, en los dos sentidos: de 384
a 768 y de vuelta. En ambos casos el contenido se conserva y los vectores se
recalculan solos al arrancar.

## Fuera de alcance

La búsqueda híbrida. Los embeddings no distinguen términos exactos como códigos
de sitio o fórmulas químicas; combinarlos con la búsqueda de texto de
PostgreSQL cubriría ese punto ciego, pero es un cambio aparte.
