# AGENTS.md — Protocolo de desarrollo autónomo (Space Lair)

> **Cómo ejecutar el orquestador**: `python tools/orchestrator.py <comando>`.
> Estado persistente: [`PROJECT_STATUS.md`](PROJECT_STATUS.md) y `data/dev_ops/state.json`.

## 1. Principios maestros

- **No asumir**: verifica el estado con `verify`/`supervised` antes de planificar.
- **Fuente única de estado**: `PROJECT_STATUS.md` ( legible) ↔ `data/dev_ops/state.json` (parseable). Nunca confíes en la memoria de la conversación.
- **No forzar PASS**: no modifiques tests para ocultar fallos.
- **Protejer lo funcional**: `modules/chapter_writer/main.py` y todo `modules/` están en `PROTECTED_FILES`.
- **Modo supervisado por defecto**: PLAN → TEST → E2E → DIAGNÓSTICO → PROPUESTA → ESPERAR APROBACIÓN.

## 2. Comandos del orquestador

| comando | qué hace |
|---|---|
| `verify` | Valida parseos + captura (dry). No ejecuta nada lento. |
| `supervised` | Ciclo supervisado completo. |
| `autonomous [--max-iterations N] [--allow-autonomous]` | Diseñado. `--allow-autonomous` es necesario para aplicar cambios; sin él es dry-run. |
| `status` | Imprime `state.json`. |
| `init` | Reconstruye `PROJECT_STATUS.md` a partir del estado real. |

## 3. Estado (claves en `state.json`)

- `CURRENT_PHASE`, `CURRENT_OBJECTIVE`, `STATUS`, `TEST_STATUS`, `TEST_COUNT`,
- `E2E_STATUS`, `FAILED_STAGE`, `ROOT_CAUSE`, `LAST_CHANGE`, `LAST_VERIFIED`,
- `FILES_MODIFIED`, `KNOWN_GOOD`, `KNOWN_BAD`, `CONSTRAINTS`,
- `NEXT_ACTION`, `SUCCESS_CRITERIA`, `PROPOSAL`, `MODE`, `ITERATIONS`.

## 4. Reglas de protección (`tools/dev/security.py`)

- `is_protected(path)` → True si el archivo/directorio está protegido.
- `assert_change_permitted(path)` → True solo si la ruta está bajo `ALLOWED_AUTO_EDIT_DIRS` (`tools/`).
- `OUT_OF_SCOPE_MODULES` enumera módulos que no se auto-editan.

## 5. Reglas de validación (VALIDATION_RULES)

1. No modificar tests únicamente para obtener PASS.
2. No reducir requisitos de aceptación ni ocultar errores.
3. No desactivar Quality Gate ni eliminar validaciones.
4. No declarar PASS sin ejecutar la prueba correspondiente.
5. Todo cambio se registra con WHY/WHAT/FILES/VERIFICATION/RESULT.

## 6. Diagnóstico de continuaciones (E2E → `e2e_001_report.json`)

- `status`: `completed` → PASS; cualquier otro → FAIL.
- `failed_stage`: etapa concreta del fallo (p.ej. `chapter`).
- `error`: mensaje de calidad (p.ej. `quality gate FAIL: [...]`).
- `chapter_quality_errors`: lista exacta de errores (palabras, duplicados, headings).
- `chapter_placeholder_detected`: si se detecta placeholder en el capítulo.
- `chapter_word_count`: conteo de palabras del capítulo.
- `last_checkpoint`: última versión de draft (`data/checkpoints/<id>/book/draft/vXXXX.json`).
- `docx_path`, `docx_status`: entregable final.

## 7. Interpretación de pytest

Última línea de resumen (`passed|failed|error`). Estado PASS si `returncode==0` y `failed==0` y `errors==0`. Ver `tools/dev/parsers.py::parse_pytest_result`.

## 8. Iteraciones

Cada iteración se guarda en `data/dev_ops/iterations/iter_<ts>.json` y se referencia en `state.json` (últimas 100).

## 9. Próximos pasos (fase 7.9D.7, fuera de alcance de la infraestructura)

Resolver el fallo de `chapter` en `modules/chapter_writer/main.py` para alcanzar **≥1500 palabras sin placeholders ni duplicados de continuación**. Requiere aprobación y modifica únicamente `main.py`.
