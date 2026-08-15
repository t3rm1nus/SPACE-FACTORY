# MANUAL DE USUARIO — SPACE LAIR
## Panel Editorial Web

**Versión:** 1.0  
**Objetivo:** guía práctica para usar el frontend de SPACE LAIR sin necesidad de conocer programación.

---

## 1. ¿Qué es SPACE LAIR?

SPACE LAIR es un panel de control para ejecutar y supervisar tareas de un sistema editorial.

Desde el panel puedes:

- Crear proyectos/libros.
- Consultar los libros existentes.
- Ver las fases de producción.
- Crear y encolar tareas.
- Seleccionar una capability o módulo.
- Enviar un payload JSON a una tarea.
- Aprobar, rechazar, cancelar o reintentar tareas.
- Ver logs y progreso.
- Consultar el estado de los módulos.
- Supervisar los resultados finales y checkpoints.

El frontend es **la interfaz visual**. El trabajo real lo realizan los módulos del backend.

---

# 2. Cómo arrancar el frontend

Abre una consola de Windows.

Primero entra en la carpeta del proyecto:

```bat
cd /d "C:\proyectos\SPACE LAIR"
```

Después arranca el servidor:

```bat
python -m frontend.frontend_api
```

Si todo está correcto, el servidor queda disponible en:

```text
http://127.0.0.1:8080
```

Abre esa dirección en Chrome o Edge.

### Si aparece este error

```text
ModuleNotFoundError: No module named 'frontend'
```

Significa que estás en la carpeta equivocada.

La secuencia correcta es:

```bat
cd /d "C:\proyectos\SPACE LAIR"
python -m frontend.frontend_api
```

---

# 3. Qué ves en la pantalla principal

## 3.1 Barra superior

Muestra indicadores generales:

- **TAREAS** — cantidad de tareas activas.
- **TAREAS/MIN** — actividad reciente.
- **MÓDULOS** — módulos/capabilities disponibles.
- **COSTE** — coste registrado.
- **TOKENS** — consumo de tokens.

Estos indicadores son informativos.

## 3.2 Columna izquierda — TAREAS

Aquí aparecen las tareas que están en ejecución o esperando.

Si no hay ninguna:

```text
Sin tareas...
```

## 3.3 Zona central

Es el área principal de trabajo:

- Estado general del sistema.
- Panel editorial.
- Selector de libros.
- Información del proyecto seleccionado.
- Progreso de las fases.

## 3.4 Columna derecha — LOGS

Aquí aparecen mensajes del sistema, por ejemplo:

```text
Libro creado
Tarea encolada
Fase iniciada
Fase completada
Error de validación
```

Los logs son especialmente útiles cuando una tarea no funciona como esperabas.

---

# 4. Crear un libro/proyecto

En **PANEL EDITORIAL** encontrarás el formulario de creación.

Aunque algunos textos de la interfaz estén actualmente en inglés, los campos significan:

| Campo | Significado |
|---|---|
| TITLE | Título del libro |
| SUBTITLE | Subtítulo |
| AUTHOR | Autor |
| GENRE | Género |
| LANGUAGE | Idioma |
| TARGET AUDIENCE | Público objetivo |
| CHAPTERS | Número de capítulos |
| TARGET WORDS | Número total de palabras deseadas |
| IMAGES / CHAPTER | Imágenes deseadas por capítulo |
| DESCRIPTION | Descripción del proyecto |

Los campos tienen valores predeterminados.

### Ejemplo

```text
Título:
Historia de Internet

Subtítulo:
De ARPANET a la era de la IA

Autor:
J. A. Charneco

Género:
Tecnología

Idioma:
Español

Público:
General

Capítulos:
6

Palabras objetivo:
3000

Imágenes/capítulo:
3
```

La descripción sirve para explicar qué quieres conseguir con el libro.

---

# 5. Botón CREAR PROYECTO

El botón **CREAR PROYECTO** crea el libro en el sistema.

Importante:

- Un clic crea un proyecto.
- El frontend incorpora protección contra doble clic.
- Si el título está vacío, el frontend impide el envío.
- El backend valida nuevamente los datos.

Después de crear el proyecto, debería aparecer en el selector de libros.

---

# 6. Seleccionar un libro

En **BOOK SELECTOR** encontrarás un desplegable.

Selecciona el libro que quieres consultar y pulsa:

**CARGAR**

El panel mostrará información relacionada con ese libro:

- Estado.
- Fases.
- Progreso.
- Tareas.
- Checkpoints.
- Outputs.

---

# 7. ¿Qué es una tarea?

Una tarea es una operación concreta que el sistema debe ejecutar.

Ejemplos:

- Crear un plan de libro.
- Investigar en la web.
- Escribir un capítulo.
- Editar un capítulo.
- Comprobar datos.
- Contar palabras.
- Crear un DOCX.
- Crear un PDF.
- Generar imágenes.
- Traducir texto.

Las tareas se ejecutan mediante las **capabilities** del sistema.

---

# 8. Crear una NUEVA TAREA

La acción **Nueva tarea** abre un modal.

Aparece un selector llamado:

```text
CAPABILITY
```

Una capability es la operación que quieres ejecutar.

Entre las disponibles pueden aparecer:

```text
build_book_docx
build_book_pdf
count_words
create_book_plan
create_chapter_image_plan
edit_chapter
external_tool
extract_text
fact_check_chapter
fetch_url
final_quality_control
generate_chapter_images
generate_image
research_web
reverse_text
summarize_text
translate_en_es
translate_es_en
write_chapter_en
write_chapter_es
```

No todas sirven para lo mismo.

---

# 9. El campo PAYLOAD JSON

Debajo de la capability aparece:

```text
PAYLOAD JSON
```

El payload son los datos que necesita esa tarea.

**No todas las tareas utilizan el mismo formato.**

Por ejemplo, para `create_book_plan` no debe utilizarse:

```json
{
  "text": "ejemplo"
}
```

Debe utilizarse al menos:

```json
{
  "idea": "Historia de Internet"
}
```

También puede recibir opciones adicionales:

```json
{
  "idea": "Historia de Internet",
  "target_chapters": 6,
  "language": "es",
  "target_audience": "General",
  "style": "Divulgativo, riguroso y claro"
}
```

---

# 10. ¿Por qué aparece "Payload inválido"?

Si aparece:

```text
Error encolando: Payload invalido
```

significa que la estructura enviada no coincide con lo que espera esa capability.

Por ejemplo:

```json
{
  "text": "ejemplo"
}
```

es incorrecto para `create_book_plan`.

La API espera:

```json
{
  "idea": "ejemplo"
}
```

La API está haciendo lo correcto al rechazar datos incorrectos.

### Regla práctica

**Cada capability tiene su propio payload.**

Si no sabes qué payload necesita una capability, no conviene inventarlo.

---

# 11. Encolar una tarea

Una vez seleccionada la capability y escrito un JSON válido:

1. Selecciona la capability.
2. Introduce el payload.
3. Comprueba que el JSON sea válido.
4. Pulsa **ENCOLAR**.

Si todo está correcto, la tarea entrará en la cola.

Aparecerá en la zona de tareas.

---

# 12. Estados habituales de una tarea

Conceptualmente:

```text
CREADA
  ↓
ENCOLADA
  ↓
EN EJECUCIÓN
  ↓
COMPLETADA
```

También puede terminar como:

```text
ERROR
CANCELADA
RECHAZADA
```

Dependiendo del flujo, puede existir también:

```text
APROBADA
REINTENTADA
```

---

# 13. Aprobar, rechazar, cancelar y reintentar

## APROBAR

Indica que una tarea o resultado ha sido aceptado.

## RECHAZAR

Indica que no quieres aceptar el resultado.

## CANCELAR

Detiene una tarea que todavía puede cancelarse.

## REINTENTAR

Solicita volver a ejecutar una tarea que ha fallado o necesita repetirse.

---

# 14. Logs: cómo utilizarlos

Los logs son una de las herramientas más importantes para diagnosticar problemas.

Si algo falla:

1. Mira primero el mensaje del log.
2. Identifica la tarea.
3. Identifica la capability.
4. Comprueba el payload.
5. Comprueba si el error es de validación o de ejecución.

### Ejemplo

```text
Payload invalido
```

normalmente significa:

**El JSON no tiene la estructura que espera la capability.**

Un error producido durante la ejecución puede indicar:

- problema del módulo;
- proveedor LLM;
- archivo inexistente;
- conexión;
- datos incorrectos;
- excepción interna.

---

# 15. Flujo editorial completo

Un flujo típico es:

```text
CREAR LIBRO
     ↓
PLANIFICAR LIBRO
     ↓
INVESTIGAR
     ↓
CREAR ESQUEMA
     ↓
ESCRIBIR CAPÍTULO
     ↓
FACT CHECK
     ↓
EDITAR
     ↓
QUALITY GATE
     ↓
DOCUMENT BUILDER
     ↓
DOCX
```

No necesariamente tienes que ejecutar cada etapa manualmente.

El runner E2E puede ejecutar el flujo completo.

---

# 16. Qué significa QUALITY GATE

El Quality Gate es una comprobación final.

En la auditoría actual se comprobó, entre otras cosas:

- fuentes suficientes;
- número mínimo de palabras;
- editor aprobado;
- fact check aprobado;
- ausencia de placeholders;
- coherencia de los datos.

Si aparece:

```text
QUALITY GATE: PASS
```

la salida ha superado las comprobaciones configuradas.

---

# 17. DOCX final

Cuando el flujo termina correctamente, el documento se genera en:

```text
output\docx\book_es.docx
```

El flujo comprobado es:

```text
Chapter Writer
      ↓
Fact Check
      ↓
Editor
      ↓
Quality Gate
      ↓
Document Builder
      ↓
DOCX
```

---

# 18. Diferencia entre FRONTEND y BACKEND

## FRONTEND

Es lo que ves:

- botones;
- formularios;
- selectores;
- ventanas;
- logs;
- tareas;
- indicadores.

Archivos principales:

```text
frontend\
frontend\index.html
frontend\app.js
frontend\style.css
frontend\frontend_api.py
```

## BACKEND

Es donde ocurre el trabajo real:

- validación;
- módulos;
- planificación;
- investigación;
- escritura;
- edición;
- fact checking;
- generación de documentos.

Carpetas importantes:

```text
core\
modules\
data\
output\
```

---

# 19. Lo que NO necesitas saber para utilizar el frontend

No necesitas saber:

- Python.
- Flask.
- JavaScript.
- Pydantic.
- JSON Schema.
- LLM.
- APIs.

Sí necesitas conocer:

1. Qué quieres hacer.
2. Qué capability corresponde.
3. Qué datos necesita esa capability.

La interfaz debería evolucionar para ocultar progresivamente la complejidad del JSON.

---

# 20. Recomendación de uso para principiantes

### PASO 1

Arranca:

```bat
cd /d "C:\proyectos\SPACE LAIR"
python -m frontend.frontend_api
```

### PASO 2

Abre:

```text
http://127.0.0.1:8080
```

### PASO 3

Crea un proyecto desde **PANEL EDITORIAL**.

### PASO 4

Selecciona el libro en **BOOK SELECTOR**.

### PASO 5

Comprueba su estado.

### PASO 6

Antes de crear una tarea manual, comprueba qué capability necesitas.

### PASO 7

No inventes el payload.

### PASO 8

Encola la tarea.

### PASO 9

Mira la zona de tareas y los logs.

### PASO 10

Si falla, guarda el mensaje exacto del error antes de modificar nada.

---

# 21. Problemas conocidos de la interfaz

## Textos en inglés

Hay elementos de la interfaz que todavía aparecen en inglés, por ejemplo:

```text
NEW BOOK
BOOK SELECTOR
CAPABILITY
PAYLOAD JSON
BUILD BOOK DOCX
```

Esto es una cuestión de interfaz/idioma, no de funcionamiento.

### Traducciones recomendadas

```text
NEW BOOK → NUEVO LIBRO
BOOK SELECTOR → SELECTOR DE LIBRO
CAPABILITY → CAPACIDAD
PAYLOAD JSON → DATOS DE LA TAREA
```

Pero los identificadores técnicos deben permanecer intactos:

```text
create_book_plan
build_book_docx
write_chapter_es
```

---

# 22. Scroll de la página

El frontend está configurado para utilizar scroll vertical normal de la página.

Si el contenido supera la altura de la ventana:

**debe aparecer scroll vertical.**

No debería ser necesario hacer scroll independiente en cada panel.

---

# 23. Qué hacer si el frontend no arranca

Primero:

```bat
cd /d "C:\proyectos\SPACE LAIR"
```

Después:

```bat
python -m frontend.frontend_api
```

Si aparece un error:

**no cambies código todavía.**

Copia el traceback completo.

---

# 24. Qué hacer si una tarea da "Payload inválido"

No modifiques el backend inmediatamente.

Haz esto:

1. Copia el nombre de la capability.
2. Copia el JSON que estás enviando.
3. Copia el mensaje completo del error.
4. Guarda esos tres datos.

Ejemplo:

```text
Capability:
create_book_plan

Payload:
{"text":"ejemplo"}

Error:
Payload invalido
```

Con esos datos se puede determinar qué campo espera el schema.

---

# 25. Qué hacer si aparecen textos raros o en inglés

No significa necesariamente que haya un error.

Hay que distinguir:

### Texto de interfaz

Se puede traducir:

```text
NEW BOOK
BOOK SELECTOR
TASKS
LOGS
```

### Identificador técnico

No conviene traducirlo:

```text
create_book_plan
write_chapter_es
fact_check_chapter
build_book_docx
```

Los identificadores son utilizados por el backend.

---

# 26. Arquitectura mental sencilla

Puedes pensar en SPACE LAIR como una fábrica:

```text
                 SPACE LAIR
                     │
          ┌──────────┴──────────┐
          │                     │
       FRONTEND              BACKEND
          │                     │
       botones              módulos
       formularios           agentes
       tareas                LLM
       logs                  archivos
          │                     │
          └──────────┬──────────┘
                     │
                  RESULTADO
                     │
                   DOCX
```

El frontend manda órdenes.

El backend ejecuta el trabajo.

Los logs informan de lo ocurrido.

---

# 27. Estado actual del sistema

Según la auditoría realizada:

- Frontend: funcional.
- Backend Flask: funcional.
- API principal: funcional.
- Creación de libros: corregida.
- Doble creación: corregida.
- Validación de títulos: corregida.
- Gestión de tareas: implementada.
- SSE: funcional en el flujo actual.
- E2E editorial: completado correctamente.
- Quality Gate: PASS.
- Document Builder: PASS.
- DOCX: generado correctamente.
- Checkpoint final: correcto.
- Tests: 334 passed.

Mejoras menores identificadas para una futura fase:

1. Eliminar código duplicado/inaccesible en `frontend_api.py`.
2. Añadir una protección explícita para evitar múltiples conexiones SSE si en el futuro se implementa reconexión.

---

# 28. Regla de oro

**Si no sabes qué hace una capability, no la ejecutes a ciegas.**

Primero identifica:

```text
¿QUÉ QUIERO HACER?
        ↓
¿QUÉ CAPABILITY LO HACE?
        ↓
¿QUÉ PAYLOAD NECESITA?
        ↓
ENCOLAR
        ↓
OBSERVAR TAREA + LOGS
        ↓
COMPROBAR RESULTADO
```

Esta es la forma segura de utilizar el panel.

---

# 29. Chuleta rápida

### Arrancar

```bat
cd /d "C:\proyectos\SPACE LAIR"
python -m frontend.frontend_api
```

### Abrir

```text
http://127.0.0.1:8080
```

### Crear libro

**Panel Editorial → rellenar datos → CREAR PROYECTO**

### Seleccionar libro

**Book Selector → seleccionar → CARGAR**

### Crear tarea

**Nueva tarea → seleccionar capability → introducir payload → ENCOLAR**

### Si falla

**Logs → copiar error completo → revisar capability + payload**

### Resultado editorial

```text
QUALITY GATE → DOCUMENT BUILDER → DOCX
```

### DOCX

```text
output\docx\book_es.docx
```

---

## Fin del manual

Este documento está pensado como guía de uso, no como documentación de programación. Para modificar el comportamiento interno del sistema hay que consultar la documentación técnica y el código del proyecto.
