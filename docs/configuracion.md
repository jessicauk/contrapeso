# Configuración

Todas las variables están documentadas en [.env.example](../.env.example). El
script carga `.env` automáticamente; las variables exportadas en la
terminal tienen prioridad. `.env` está excluido de Git.

## Proveedor de modelo

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

## Modelo por agente

Los agentes `DIRECTOR`, `INVESTIGADOR`, `AUDITOR` y `EXTRACTOR` heredan
`LLM_PROVIDER` por defecto. Para separarlos:

```dotenv
DIRECTOR_LLM_PROVIDER=kimi
DIRECTOR_MODELO=kimi-k3
INVESTIGADOR_LLM_PROVIDER=ollama
INVESTIGADOR_MODELO=llama3.2:3b
```

La antigua variable `MODELO` ya no se usa.

## Costos y topes

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

## Integración con frameworks

```python
from llm import langchain_llm   # LangChain / LangGraph
from llm import crewai_llm      # CrewAI: Agent(..., llm=crewai_llm())
```

Ambas usan la misma configuración. Las herramientas y la salida
estructurada dependen del modelo; pruébalas en cada flujo.
Documentación: [Ollama](https://docs.ollama.com/api/openai-compatibility),
[Kimi API](https://platform.kimi.ai/docs/overview).
