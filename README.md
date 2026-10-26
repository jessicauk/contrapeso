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

## Documentación

- [Agentes](docs/agentes.md): el crew de análisis (director, investigador
  y auditor) y el extractor de predicciones.
- [Configuración](docs/configuracion.md): proveedor de modelo, modelo por
  agente, costos y topes, e integración con frameworks.
- [Archivos generados](docs/archivos.md): qué escribe cada script y qué
  queda fuera de Git.

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

## Pruebas

```sh
PYTHONPATH=. python -m unittest discover -s tests
```

Las pruebas no llaman a X, Binance ni a ningún modelo.
