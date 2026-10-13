# Red Confiable

Entorno Python 3.11 o superior. Ollama es el proveedor predeterminado,
con el modelo local `llama3.2:3b`. No requiere una clave de OpenAI.

## Uso

Desde la carpeta del proyecto:

```sh
source .venv/bin/activate
python llm.py "Hola, responde en español"
```

Ollama debe estar abierto. Si está detenido, abre la aplicación o ejecuta
`ollama serve` en otra terminal. Para descargar el modelo en otro equipo:
`ollama pull llama3.2:3b`.

## Kimi en el futuro

Copia `.env.example` a `.env` y cambia estas variables:

```dotenv
LLM_PROVIDER=kimi
MOONSHOT_API_KEY=REEMPLAZA_CON_TU_CLAVE_REAL
KIMI_BASE_URL=https://api.moonshot.ai/v1
KIMI_MODEL=kimi-k3
KIMI_THINKING=disabled
```

`KIMI_THINKING` activa (`enabled`) o desactiva (`disabled`, predeterminado) el
razonamiento de Kimi. Sus tokens cuentan dentro de `max_tokens` y del costo,
y pueden agotar el límite antes de la respuesta. Se puede ajustar por agente
con `<AGENTE>_KIMI_THINKING` (por ejemplo `DIRECTOR_KIMI_THINKING=enabled`).
Cualquier otro valor detiene la ejecución con un error claro.

Ejecuta el mismo comando `python llm.py`. El script carga `.env`
automáticamente; las variables exportadas en la terminal tienen prioridad.
`.env` está excluido de Git. Kimi requiere conexión, una clave de su plataforma
API y disponibilidad/saldo para el modelo elegido. Las consultas se envían al
proveedor seleccionado. No hay cambio automático a un servicio remoto.
Para volver al modelo local, configura `LLM_PROVIDER=ollama`.

## Integración

Para LangChain o nodos de LangGraph usa `from llm import langchain_llm`.
Para CrewAI usa `from llm import crewai_llm` y pasa `llm=crewai_llm()` al agente.
Ambas funciones usan la misma configuración. Las capacidades de herramientas
y salidas estructuradas dependen del modelo y deben probarse en cada flujo.

Documentación: [Ollama](https://docs.ollama.com/api/openai-compatibility)
y [Kimi API](https://platform.kimi.ai/docs/overview).

## Modelos por agente en Contrapeso

`contrapeso_m1.py` organiza tres agentes con los roles de la Milpa:
el director (Maíz) delega, el investigador (Frijol) busca contraargumentos
y el auditor (Calabaza) revisa el análisis. Si el auditor lo rechaza, el
director vuelve a delegar con sus motivos, hasta `MAX_RECHAZOS` veces.

Cada agente recibe su propia instancia de `crewai_llm()` de `llm.py`. Sin
ajustes adicionales, los tres heredan `LLM_PROVIDER` y el modelo
correspondiente (`OLLAMA_MODEL` o `KIMI_MODEL`).

Para separarlos, configura `DIRECTOR_LLM_PROVIDER`,
`INVESTIGADOR_LLM_PROVIDER` y `AUDITOR_LLM_PROVIDER` (`ollama` o `kimi`), y
opcionalmente `DIRECTOR_MODELO`, `INVESTIGADOR_MODELO` y `AUDITOR_MODELO`.
La antigua variable `MODELO` ya no se utiliza. Las conexiones y claves
siguen siendo las del proveedor.

Para cada agente remoto debes definir `<AGENTE>_PRECIO_ENTRADA_USD_M` y
`<AGENTE>_PRECIO_SALIDA_USD_M` con las tarifas de su modelo por millón de
tokens. Ollama usa cero por defecto para el costo de API local. El registro
en `gastos.jsonl` desglosa tokens y costo estimado por agente, incluso si
la ejecución falla después de consumir tokens reportados por el proveedor.
La estimación no distingue descuentos de caché ni otros cargos.
El freno mensual se comprueba antes de comenzar; no garantiza un límite
estricto durante la ejecución.

Cada corrida deja dos archivos en `logs/`: el log de CrewAI
(`<ficha>-<fecha>.log`) y el de decisiones (`<ficha>-<fecha>-decisiones.jsonl`),
con los modelos y límites usados, cada veredicto del auditor, errores,
costo y tu respuesta a la pregunta de guardar.

El módulo también requiere `ficha.py` con la constante `CARPETA`, las fichas
JSON correspondientes y archivos autorizados en `fuentes/`. Los modelos
elegidos deben admitir herramientas y la salida estructurada utilizada.

## Pruebas

```sh
PYTHONPATH=. python -m unittest tests/test_multimodel.py
```

## Reinstalación del entorno

```sh
python3.11 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Ingesta de X

`ingesta.py` trae los posts de las cuentas de `cuentas.yaml` a `ingesta.db`
(SQLite, excluida de Git). Cada cuenta necesita una `razon`; si alguna no la
tiene, el script se niega a correr.

```sh
python ingesta.py --cuenta TraderSteve_   # primera prueba con una sola cuenta
python ingesta.py --gasto                 # gasto del mes contra CONTRAPESO_TOPE_USD
python ingesta.py                         # todas las cuentas
python ingesta.py --verificar             # recalcula la cadena y revisa las anclas
python ingesta.py --anclar                # sella el último hash fuera de tu control
```

Requiere `X_BEARER`, `X_PRECIO_POST_USD` y `X_PRECIO_USUARIO_USD` en `.env`.

- `tweets_crudos` y `eventos` tienen triggers que bloquean `UPDATE` y `DELETE`.
- Cada corrida, llamada a X, alta, retiro o cambio de razón de una cuenta queda
  en `eventos`, encadenado con SHA-256. `--verificar` detecta alteraciones,
  incluidos cambios en los posts guardados.
- El tope se revisa antes de cada llamada con el peor caso
  (`max_results × precio`). El costo registrado es una estimación basada en
  los posts devueltos.
- Una cuenta nueva solo trae sus `MAX_POR_CUENTA` (20) posts más recientes.
  En las corridas siguientes se usa `since_id`. Si una cuenta publica más de 20
  posts entre corridas, los más antiguos de ese intervalo no se traen.
- Las cuentas nuevas se buscan en lote con `users/by` (hasta 100 por llamada).
  Las que X no encuentra se registran como error, sin detener a las demás.
- Si una cuenta falla, se registra el error y se sigue con las demás. Las
  fallas de red y los errores 5xx se reintentan con espera creciente (2 s, 4 s;
  `MAX_INTENTOS` = 3). Ante un 429, se espera una sola vez hasta
  `x-rate-limit-reset` (máximo 15 minutos). Otros 4xx no se reintentan.

### Anclaje externo

La cadena de hashes detecta si se edita un evento, pero no si se borran los
últimos eventos ni si alguien reescribe la bitácora completa recalculando los
hashes. `--anclar` guarda el número y el hash del último evento en
`anclas/ancla_<n>.txt`. Si está instalado `opentimestamps-client`
(`pip install opentimestamps-client`), además lo sella en la blockchain de
Bitcoin: esa prueba de fecha no la puede modificar nadie, ni tú.

`--verificar` revisa que cada ancla coincida con la bitácora. Si no coincide,
avisa que la bitácora fue truncada o reescrita. También avisa si falta el
archivo de un ancla registrada. Además muestra el estado de cada sello: solo
local, pendiente o confirmado en Bitcoin. La confirmación tarda unas horas, y
no poder consultarla no se cuenta como alteración.

Al sellar, solo se envía el hash a los servidores públicos de
OpenTimestamps; ningún post ni dato tuyo. Las anclas solo contienen hashes,
así que puedes subirlas a git como un testigo más.

Corrida diaria a las 8:00 con cron (`crontab -e`):

```cron
0 8 * * * cd /ruta/a/CONTRAPESO && .venv/bin/python ingesta.py >> logs/ingesta.log 2>&1
```

Pruebas: `PYTHONPATH=. python -m unittest tests/test_ingesta.py`

## Extractor

`extractor.py` lee `tweets_crudos` y hace dos cosas:

1. **Predicciones.** Por cada post pendiente, el modelo propone las
   predicciones verificables, usando los tipos de `ficha.py` (`precio_mayor`,
   `precio_menor`, `supera_a`). El código valida cada una: ticker, tipo,
   precio > 0, plazo de 1 a 365 días y una `cita` que aparezca literal en el
   post y que el token (y la referencia de `supera_a`) se mencione en el post,
   por ticker o por nombre (`ALIAS`: bitcoin→BTC, ethereum→ETH, …). La cita se
   compara sin distinguir entidades HTML (`&amp;`), comillas tipográficas,
   espacios ni mayúsculas. Si el modelo agrega campos que no se pidieron, la
   salida se rechaza. Si algo falla, se reintenta una vez con los motivos. Si
   vuelve a fallar, el post queda `rechazado` y no se paga otra vez.
   Si el precio predicho va pegado en la cita a "stop loss", "SL", "entry",
   "entrada" o "invalidación", se rechaza como `NIVEL_OPERATIVO`: es un nivel
   de la operación, no un pronóstico. El objetivo ("target 75k") sí pasa.
   Cada rechazo se guarda con un código (`CITA_NO_LITERAL`,
   `ACTIVO_NO_MENCIONADO`, `FORMATO`, …) en `extracciones.detalle`.
   Los posts que intentan dar instrucciones a una IA quedan `aislados`, sin
   predicciones. Se detectan por reglas en el código (`INYECCION`), antes de
   llamar al modelo. Si es el modelo quien reporta `contiene_instrucciones`,
   el post no se aísla: queda marcado para revisión (`--marcados`), porque un
   modelo pequeño da falsos positivos y aislaría predicciones reales. Si el post no da un plazo, el
   código asigna `HORIZONTE_DIAS` (30) y lo marca como `plazo_explicito = 0`.
2. **Fuentes.** Escribe `fuentes/x-<cuenta>-<fecha>.md` para el investigador
   de `contrapeso_m1.py`, solo con las cuentas activas.

```sh
python extractor.py              # extrae (hasta MAX_POSTS = 50 posts) y escribe fuentes
python extractor.py --fuentes    # solo fuentes, sin LLM ni costo
python extractor.py --listar     # predicciones extraídas
python extractor.py --marcados   # posts que el modelo marcó como instrucciones a una IA
python extractor.py --verificar  # cadena de hashes + integridad de las predicciones
```

`extracciones` y `predicciones` viven en `ingesta.db` con triggers que
bloquean `UPDATE` y `DELETE`. Cada extracción queda en la bitácora encadenada
con un digesto de sus predicciones. Si cambias el prompt o las reglas, sube
`VERSION`: todo se vuelve a extraer y lo anterior se conserva.

El modelo se elige con `EXTRACTOR_LLM_PROVIDER` y `EXTRACTOR_MODELO`. Con
Kimi, `EXTRACTOR_PRECIO_ENTRADA_USD_M` y `EXTRACTOR_PRECIO_SALIDA_USD_M` son
obligatorios. El gasto se anota en `gastos.jsonl` y comparte el techo mensual
de `contrapeso_m1.py`. Se revisa antes de cada llamada con el peor caso.

Para la corrida diaria, después de la ingesta:

```cron
0 8 * * * cd /ruta/a/CONTRAPESO && .venv/bin/python ingesta.py >> logs/ingesta.log 2>&1; .venv/bin/python extractor.py >> logs/extractor.log 2>&1
```

Pruebas: `PYTHONPATH=. python -m unittest tests/test_extractor.py`

## Validador de resultados

`resultados.py` decide en código, sin LLM, si cada predicción vencida se
cumplió. Usa velas diarias en UTC de `TOKENUSDT` de los datos públicos de
Binance (`data-api.binance.vision`), sin clave ni costo. Solo resuelve cuando
el día de `fecha_resolucion` ya cerró en UTC.

```sh
python resultados.py              # resuelve las predicciones vencidas
python resultados.py --historial  # aciertos por cuenta
python resultados.py --fichas     # resuelve tus fichas vencidas (pide s/n)
python resultados.py --verificar  # cadena + predicciones + resultados
```

- `precio_mayor` / `precio_menor`: se guardan dos lecturas.
  - **Al cierre:** el cierre del día de resolución quedó por encima o por
    debajo del objetivo.
  - **Tocó:** el máximo o el mínimo alcanzó el objetivo entre el día
    siguiente al post y el de resolución. El día del post no cuenta, porque
    el precio pudo moverse antes del post.
- `supera_a`: el rendimiento del token contra el de la referencia, del cierre
  del día del post al cierre del día de resolución.
- **Triviales:** las predicciones que ya se cumplían al publicarse se marcan
  y no cuentan en el historial.
- Si Binance no lista el token o faltan velas, la predicción queda pendiente y
  se reintenta. El intento queda en la bitácora.
- `resultados` vive en `ingesta.db` con triggers que bloquean `UPDATE` y
  `DELETE`. Cada resultado queda en la bitácora encadenada con su digesto.
- `--fichas` resuelve tus fichas al cierre y solo escribe
  `resultado` y `resultado_detalle` si respondes `s`.

Cron diario completo:

```cron
0 8 * * * cd /ruta/a/CONTRAPESO && .venv/bin/python ingesta.py >> logs/ingesta.log 2>&1; .venv/bin/python extractor.py >> logs/extractor.log 2>&1; .venv/bin/python resultados.py >> logs/resultados.log 2>&1; .venv/bin/python ingesta.py --anclar >> logs/anclas.log 2>&1
```

Pruebas: `PYTHONPATH=. python -m unittest tests/test_resultados.py`
