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
```

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
python ingesta.py --verificar             # recalcula la cadena de hashes
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
- Si una cuenta falla, se registra el error y se sigue con las demás. Ante un
  429, se espera hasta `x-rate-limit-reset` (máximo 15 minutos) y se reintenta
  una vez.

Corrida diaria a las 8:00 con cron (`crontab -e`):

```cron
0 8 * * * cd /ruta/a/CONTRAPESO && .venv/bin/python ingesta.py >> logs/ingesta.log 2>&1
```

Pruebas: `PYTHONPATH=. python -m unittest tests/test_ingesta.py`
