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

`contrapeso_m1.py` usa `crewai_llm()` de `llm.py`. Cada agente recibe una
instancia independiente. Sin ajustes adicionales, ambos heredan `LLM_PROVIDER`
y el modelo correspondiente (`OLLAMA_MODEL` o `KIMI_MODEL`).

Para separarlos, configura `DIRECTOR_LLM_PROVIDER` y
`INVESTIGADOR_LLM_PROVIDER` (`ollama` o `kimi`), y opcionalmente
`DIRECTOR_MODELO` e `INVESTIGADOR_MODELO`. La antigua variable `MODELO`
ya no se utiliza. Las conexiones y claves siguen siendo las del proveedor.

Para cada agente remoto debes definir `<AGENTE>_PRECIO_ENTRADA_USD_M` y
`<AGENTE>_PRECIO_SALIDA_USD_M` con las tarifas de su modelo por millón de
tokens. Ollama usa cero por defecto para el costo de API local. El registro
en `gastos.jsonl` desglosa tokens y costo estimado por agente, incluso si
la ejecución falla después de consumir tokens reportados por el proveedor.
La estimación no distingue descuentos de caché ni otros cargos.
El freno mensual se comprueba antes de comenzar; no garantiza un límite
estricto durante la ejecución.

El módulo también requiere `ficha.py` con la constante `CARPETA`, las fichas
JSON correspondientes y archivos autorizados en `fuentes/`. Los modelos
elegidos deben admitir herramientas y la salida estructurada utilizada.

## Reinstalación del entorno

```sh
python3.11 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```
