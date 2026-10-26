# Contrapeso

Diario de decisiones calibrado para cripto. Registras una decisión con su
tesis y una predicción verificable, tres agentes buscan contraargumentos y
el código comprueba después, con precios reales, si se cumplió. También
ingiere posts de cuentas de X, extrae sus predicciones y lleva el historial
de aciertos de cada cuenta.

Los límites y garantías viven en el código, no en los prompts (marco ASILO:
Aislamiento, Supervisión, Iteración, Límites, Observabilidad).

## Flujo

```
bitacora.py nueva ──► fichas/<id>.json ──► contrapeso_m1.py <id> ──► análisis (guardar s/n)
                                                  ▲
cuentas.yaml ─► ingesta.py ─► ingesta.db ─► extractor.py ─► fuentes/x-*.md
                                   │
                                   └──► resultados.py (precios de Binance) ──► aciertos
```

| Script | Qué hace | Costo |
|---|---|---|
| `bitacora.py` | Crea y muestra tus fichas de decisión | — |
| `contrapeso_m1.py` | Director, investigador y auditor analizan una ficha | LLM |
| `ingesta.py` | Trae posts de X a una base inmutable | API de X |
| `extractor.py` | Extrae predicciones verificables de los posts | LLM |
| `resultados.py` | Resuelve predicciones vencidas con precios de Binance | Gratis |
| `llm.py` | Configuración compartida del modelo (Ollama o Kimi) | — |

## Instalación

Requiere Python 3.11 o superior.

```sh
python3.11 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

Ollama es el proveedor predeterminado, con el modelo local `llama3.2:3b`;
no hace falta ninguna clave. Ollama debe estar abierto (la aplicación o
`ollama serve` en otra terminal). En un equipo nuevo:
`ollama pull llama3.2:3b`.

Prueba la conexión:

```sh
python llm.py "Hola, responde en español"
```

## Uso

### 1. Registrar una decisión

```sh
python bitacora.py nueva     # preguntas guiadas
python bitacora.py listar
python bitacora.py ver ID
```

Cada ficha incluye token, acción (`comprar`, `vender`, `mantener`,
`no_comprar`), estado de ánimo, tesis, señales de invalidación y una
predicción de tipo `precio_mayor`, `precio_menor` o `supera_a`, con
probabilidad (0.01–0.99) y fecha de resolución. Se guarda en `fichas/`.

### 2. Analizar la ficha con los agentes

```sh
python contrapeso_m1.py 2026-09-29-NEAR
```

Tres roles de la Milpa: el director (Maíz) delega, el investigador
(Frijol) busca contraargumentos solo en `fuentes/` y el auditor (Calabaza)
revisa el análisis. Si el auditor lo rechaza, el director vuelve a delegar
con sus motivos, hasta `MAX_RECHAZOS` veces. Nada se guarda sin tu
confirmación (s/n).

Los modelos elegidos deben admitir herramientas y la salida estructurada
que se usa.

### 3. Fuentes permitidas

El investigador solo puede leer archivos `.md` de `fuentes/` (lista
blanca). Puedes agregar archivos a mano; anota al inicio de dónde salió el
texto y la fecha, y no uses el prefijo `x-`. Los archivos `x-*.md` los
genera `extractor.py` y se sobrescriben. Más detalles en
[fuentes/LEEME.md](fuentes/LEEME.md).

### 4. Ingerir posts de X

Las cuentas se definen en [cuentas.yaml](cuentas.yaml), cada una con una
`razon` que explique por qué es una fuente confiable.

```sh
python ingesta.py                  # posts nuevos de todas las cuentas
python ingesta.py --cuenta NOMBRE  # solo una cuenta
python ingesta.py --gasto          # gasto del mes contra el tope (no llama a X)
python ingesta.py --verificar      # revisa la cadena de hashes y las anclas
python ingesta.py --anclar         # sella el último hash con OpenTimestamps
```

`ingesta.db` no admite cambios: unos triggers de SQLite abortan cualquier
`UPDATE` o `DELETE` sobre los posts y la bitácora. Cada evento queda
encadenado por SHA-256. Como la cadena por sí sola no detecta que se
reescriba completa, `--anclar` guarda el último hash en `anclas/` y, con
`opentimestamps-client`, lo sella en Bitcoin. Una cuenta nueva solo trae sus
posts más recientes, nunca el historial completo.

### 5. Extraer predicciones

```sh
python extractor.py              # procesa los posts pendientes y escribe fuentes/x-*.md
python extractor.py --fuentes    # solo regenera fuentes/x-*.md (sin LLM)
python extractor.py --listar     # predicciones extraídas
python extractor.py --marcados   # posts que el modelo marcó como instrucciones a una IA
python extractor.py --verificar  # integridad de la cadena y de las predicciones
```

Cada predicción se valida en código: la cita debe ser literal, el activo
debe aparecer en el post, y los stops y entradas no cuentan como
predicciones (`NIVEL_OPERATIVO`). Las que fallan se rechazan con un código
de motivo. Los posts que intentan dar órdenes a una IA se aíslan antes de
llegar al modelo.

Si cambias el prompt o las reglas, sube `VERSION` en `extractor.py` para
volver a extraer todo.

### 6. Resolver resultados

```sh
python resultados.py              # resuelve las predicciones vencidas de ingesta.db
python resultados.py --historial  # aciertos por cuenta
python resultados.py --fichas     # resuelve tus fichas vencidas (pide confirmación)
python resultados.py --verificar  # integridad de predicciones y resultados
```

Usa velas diarias UTC de `TOKENUSDT` desde la API pública de Binance (sin
clave). Solo resuelve cuando el día de resolución ya cerró en UTC. Se
registra si se cumplió al cierre, si el precio tocó el objetivo y si la
predicción era trivial (ya se cumplía el día del post); las triviales no
cuentan como acierto. Si falta un precio, la predicción queda pendiente y
se reintenta en la siguiente corrida.

## Configuración

Todas las variables están documentadas en [.env.example](.env.example). El
script carga `.env` automáticamente; las variables exportadas en la
terminal tienen prioridad. `.env` está excluido de Git.

### Proveedor de modelo

```dotenv
LLM_PROVIDER=kimi
MOONSHOT_API_KEY=REEMPLAZA_CON_TU_CLAVE_REAL
KIMI_BASE_URL=https://api.moonshot.ai/v1
KIMI_MODEL=kimi-k3
KIMI_THINKING=disabled
```

Kimi requiere conexión, una clave de su plataforma y saldo para el modelo
elegido. Si falla, no cambia automáticamente a otro servicio. Para volver
al modelo local: `LLM_PROVIDER=ollama`.

`KIMI_THINKING` activa (`enabled`) o desactiva (`disabled`) el
razonamiento. Esos tokens cuentan dentro de `max_tokens` y del costo, y
pueden agotar el límite antes de la respuesta. Se puede fijar por agente
con `<AGENTE>_KIMI_THINKING`. Cualquier otro valor detiene la ejecución.

### Modelo por agente

Los agentes `DIRECTOR`, `INVESTIGADOR`, `AUDITOR` y `EXTRACTOR` heredan
`LLM_PROVIDER` por defecto. Para separarlos:

```dotenv
DIRECTOR_LLM_PROVIDER=kimi
DIRECTOR_MODELO=kimi-k3
INVESTIGADOR_LLM_PROVIDER=ollama
INVESTIGADOR_MODELO=llama3.2:3b
```

La antigua variable `MODELO` ya no se usa.

### Costos y topes

- Cada agente que use Kimi necesita `<AGENTE>_PRECIO_ENTRADA_USD_M` y
  `<AGENTE>_PRECIO_SALIDA_USD_M` (USD por millón de tokens). Sin ellos, el
  script se niega a correr. Ollama cuesta cero.
- `contrapeso_m1.py` y `extractor.py` comparten un techo mensual de
  5 USD, registrado en `gastos.jsonl` por agente. El techo se comprueba
  antes de cada corrida o llamada con una estimación del peor caso. No
  distingue descuentos de caché ni otros cargos.
- La ingesta de X requiere `X_BEARER`, `X_PRECIO_POST_USD` y
  `X_PRECIO_USUARIO_USD`, con su propio tope `CONTRAPESO_TOPE_USD`
  (5 por defecto).

### Integración con frameworks

```python
from llm import langchain_llm   # LangChain / LangGraph
from llm import crewai_llm      # CrewAI: Agent(..., llm=crewai_llm())
```

Ambas usan la misma configuración. Las herramientas y la salida
estructurada dependen del modelo; pruébalas en cada flujo.
Documentación: [Ollama](https://docs.ollama.com/api/openai-compatibility),
[Kimi API](https://platform.kimi.ai/docs/overview).

## Archivos generados

Excepto `anclas/`, ninguno de estos archivos se sube a Git:

| Ruta | Contenido |
|---|---|
| `fichas/` | Tus fichas de decisión (privadas) |
| `ingesta.db` | Posts, predicciones, resultados y bitácora encadenada |
| `fuentes/*.md` | Fuentes del investigador (excepto `LEEME.md`) |
| `gastos.jsonl` | Tokens y costo estimado por agente |
| `logs/` | `<ficha>-<fecha>.log` (CrewAI) y `<ficha>-<fecha>-decisiones.jsonl` |
| `anclas/` | Hashes sellados con `--anclar` (no está en `.gitignore`) |

## Pruebas

```sh
PYTHONPATH=. python -m unittest discover -s tests
```

Las pruebas no llaman a X, Binance ni a ningún modelo.
