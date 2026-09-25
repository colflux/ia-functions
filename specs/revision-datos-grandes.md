# Subida y revisión de archivos de datos grandes

## Introducción

Los Excel y CSV de datos con miles de filas ya se pueden subir desde el chat. El
asistente guarda el original, revisa cada hoja y le muestra a quien sube el archivo
lo que conviene corregir antes de cargarlo a la plataforma por el ETL. Esa revisión
queda indexada, así que después se le puede preguntar por el archivo.

## Contexto

Al subir `IDEAM-IA.xlsx` (8 hojas, 12.558 filas: unidades de muestreo, CO2, CH4,
clima, MOM, COS y biomasa del IDEAM), el chat respondía «El archivo tiene demasiado
texto». La subida convierte el archivo en texto para indexarlo, con un tope de 300.000
caracteres, y este tiene unos 2 millones.

Subir el tope no servía. Con miles de filas partidas en fragmentos, el asistente solo
vería pedazos y no podría calcular nada por sitio. Los datos exactos deben entrar a la
base de la plataforma por el ETL; lo que sí sirve en el chat es **revisarlos**.

Decisión: revisión en el chat y, después, carga por el ETL con los datos corregidos.

## Qué hace

### Revisión automática (`domain/services/perfil_datos.py`)

Se aplica a `.xlsx`, `.xlsm` y `.csv` (el separador del CSV se detecta solo). Para
cada hoja:

- **Perfil**: filas y, por columna, cuántas tienen dato y cuántas están vacías, rango
  de fechas, rango y mediana de números, los valores si son pocos, o ejemplos. La
  cabecera es la primera fila con al menos dos celdas escritas; las columnas sin
  título ni datos se ignoran.
- **Hallazgos graves** (impiden usar filas), que se muestran primero:
    - hoja con gas pero sin columna de valor del flujo, o sin columna de unidad;
    - filas sin valor, desglosadas por analizador si existe esa columna;
    - filas sin unidad;
    - columnas con el mismo nombre;
    - porcentajes fuera de 0–100;
    - coordenadas fuera de Colombia;
    - fechas futuras.
- **Hallazgos para revisar**:
    - hoja con gas pero sin columna de condición de luz;
    - unidades mezcladas en una misma hoja;
    - valores del flujo a más de 3 rangos intercuartílicos del resto;
    - «Noche» entre las 7:00 y las 17:59, y «Día» entre las 20:00 y las 4:59;
    - fechas anteriores a la instalación de su unidad de muestreo (la fecha de
      instalación puede venir en la misma hoja o en otra, cruzando por «nombre unidad
      de muestreo»);
    - filas sin fecha;
    - filas repetidas;
    - columnas vacías;
    - nombres de sitio o de unidad experimental escritos de varias formas (mayúsculas,
      tildes, espacios).
- Un mismo hallazgo en varias hojas se muestra en una sola línea. No se corrige nada:
  solo se informa, con el número de filas afectadas.

### En la subida (`carga_documentos.py`)

- Si un Excel o CSV supera los 300.000 caracteres, **no se indexa entero**. El modelo
  revisa el perfil, en lugar del comienzo del texto, para decidir si está relacionado
  con COLFLUX y si coincide con lo descrito. Se guarda el original en el bucket y se
  indexa la revisión (unos 23.000 caracteres para el archivo del IDEAM). Las hojas
  pequeñas, de hasta 20.000 caracteres (por ejemplo su «Diccionario de datos»), se
  indexan completas.
- Se guarda como «datos para revisión (pendientes de pasar por el ETL)». No pasa por
  la validación de campos obligatorios: esa validación supone una sola categoría y
  aquí hay varias hojas. La revisión cumple ese papel.
- La respuesta a quien sube el archivo trae las hojas con su número de filas, los 12
  primeros hallazgos (los graves primero) y el aviso de que aún no son mediciones de la
  plataforma.
- Los archivos de datos pequeños siguen igual (texto completo y validación de campos
  obligatorios).

### Lugar «varios sitios» (`lugar.py`)

Una tabla con filas de varios sitios (Chingaza y Nevados, en el ejemplo) no tiene un
solo municipio y departamento. En el paso del lugar se acepta «varios sitios»: la
ubicación sale de las coordenadas de cada fila. Solo vale para Excel y CSV; para
imágenes y documentos se sigue pidiendo un lugar.

## Plan por fases

1. **Revisión en el chat** (este cambio).
2. **Carga por el ETL**, con el archivo corregido: queda en el backend. Para el archivo
   del IDEAM, antes hay que corregir lo siguiente:
    - CH4 no tiene columna de valor;
    - 111 filas del LICOR 8100 sin valor ni unidad;
    - condición de luz inconsistente con la hora;
    - «Calostros» escrito de tres formas;
    - dos valores de % de carbono por encima de 100.
3. **Futuro**: que el ETL use estas mismas comprobaciones como validación previa.

## Verificación

- Local, con `IDEAM-IA.xlsx`:
    - revisión en 2,8 s, 22 hallazgos y 22.868 caracteres indexados;
    - hallazgos comprobados contra el archivo: CH4 sin valor en 946 filas, LICOR 8100
      sin valor en 111, % de carbono hasta 351,5, 862 filas de CH4 marcadas «Noche»
      entre las 9:00 y las 16:00;
    - subida completa con almacenamiento y modelo simulados.
- Local con los archivos de prueba de flujos: el completo, sin hallazgos; el que no
  tiene unidad ni luz, con esos dos hallazgos. Un CSV con «;», coma decimal y nombres
  con espacios.
- «Varios sitios»: se acepta para un Excel y se rechaza para una imagen.
- Laboratorio: subir `IDEAM-IA.xlsx` desde el chat con «varios sitios» y preguntar por
  la revisión de una hoja.
