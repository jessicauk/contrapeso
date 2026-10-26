# Agentes

## Crew de análisis (`contrapeso_m1.py`)

Tres agentes de CrewAI revisan una ficha y buscan razones por las que la
tesis podría fallar.

| Agente | Rol | Herramientas | Delega |
|---|---|---|---|
| **Director de Análisis** | Coordina la revisión y entrega el análisis estructurado. No investiga y nunca recomienda comprar ni vender. | Ninguna | Sí, al investigador |
| **Investigador de contraargumentos** | Busca hasta 5 contraargumentos verificables; cada uno cita el archivo exacto de donde sale. | `leer_fuente_permitida` (solo la lista blanca de `fuentes/`) | No |
| **Auditor de análisis** | Lee las fuentes citadas y aprueba o rechaza el análisis (hasta `MAX_RECHAZOS` veces). | `leer_fuente_permitida` | No |

El auditor no forma parte del crew: actúa como *guardrail* de la tarea, así
que el director no puede delegarle trabajo ni saltárselo. El crew corre en
orden (director → investigador). Después del auditor se aplican una
validación en código (`validar()`) y tu confirmación (s/n) antes de escribir
nada en la ficha.

Cada agente puede usar su propio modelo (ver
[Modelo por agente](configuracion.md#modelo-por-agente)). Comparten los límites `MAX_ITER`,
`MAX_SEGUNDOS`, `MAX_RPM`, `MAX_TOKENS` y el techo mensual de gasto.

## Extractor (`extractor.py`)

No usa CrewAI: hace una sola llamada al modelo por post, con temperatura 0.
Lee los posts de `tweets_crudos` y extrae predicciones verificables, que se
validan con Pydantic y reglas explícitas: el post debe mencionar el activo,
no se aceptan stops ni entradas como predicciones y el plazo no puede pasar
de 365 días. Después genera los `fuentes/x-*.md` que usa el investigador.

`ingesta.py`, `resultados.py` y `bitacora.py` no usan ningún modelo.
