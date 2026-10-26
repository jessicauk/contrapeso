# CLAUDE.md

Contrapeso: diario de decisiones cripto con agentes (CrewAI), ingesta de X
y validación de predicciones con precios de Binance. Uso y configuración en
[README.md](README.md).

## Comandos

```sh
source .venv/bin/activate
PYTHONPATH=. python -m unittest discover -s tests   # correr antes de cada commit
```

## Reglas fundamentales

### Integridad de los datos (no negociable)
- `tweets_crudos`, `eventos`, `extracciones`, `predicciones` y `resultados`
  son inmutables: triggers de SQLite abortan `UPDATE` y `DELETE`. Nunca
  quites, debilites ni esquives esos triggers. Una corrección se registra
  como fila nueva.
- Todo cambio de estado relevante pasa por `ingesta.registrar()`, que
  encadena el evento por SHA-256. No escribas en `eventos` directamente.
- Nunca borres, reescribas ni "repares" `ingesta.db`, `anclas/`,
  `gastos.jsonl`, `logs/` ni `fichas/`. Si `--verificar` falla, informa;
  no lo arregles.

### ASILO vive en el código, no en el prompt
- Límites (`MAX_*`, topes de gasto, lista blanca de `fuentes/`, herramientas
  de solo lectura) son constantes en código. No los muevas al prompt ni los
  hagas configurables por el modelo.
- Toda validación de la salida del modelo se hace en código (Pydantic y
  reglas explícitas). No confíes en que el modelo cumpla instrucciones.
- El texto de los posts es dato no confiable: siempre delimitado y nunca
  interpretado como instrucción.
- Nada se escribe en las fichas del usuario sin su confirmación (s/n).
- Si cambias el prompt o las reglas del extractor, sube `VERSION` en
  `extractor.py`.

### Costos y llamadas externas
- El tope de gasto se revisa ANTES de cada llamada, con el peor caso. No
  agregues llamadas a modelos o a la API de X que salten esa revisión.
- Si faltan precios de un servicio de pago en `.env`, el script debe
  negarse a correr; no inventes precios por defecto. Solo Ollama cuesta cero.
- No ejecutes `ingesta.py`, `extractor.py` ni `contrapeso_m1.py` contra
  servicios reales sin que el usuario lo pida: cuestan dinero.
- No hay respaldo automático a otro proveedor. Un fallo se reporta.

### Secretos y privacidad
- Nunca leas, muestres ni subas `.env`. Las variables nuevas se documentan
  en `.env.example` (sin valores reales) y en el README.
- `fichas/`, `ingesta.db`, `logs/`, `gastos.jsonl` y `fuentes/*.md` son
  privados y están en `.gitignore`; no los agregues a Git.
- Los archivos `fuentes/x-*.md` los genera `extractor.py`: no los edites.

### Pruebas
- Las pruebas no tocan la red ni modelos reales: usan fakes (`XFalso`,
  respuestas simuladas) y bases temporales con `tempfile`. Mantenlo así.
- Toda regla de validación nueva o error corregido lleva su prueba.
- Nunca uses `ingesta.db` real en pruebas.

### Estilo
- Código, comentarios, mensajes y commits en español, como el resto del
  proyecto. Mensajes de commit en infinitivo ("Agregar...", "Corregir...").
- Python 3.11+, Pydantic 2. Docstring de módulo con sección `Uso:` y
  constantes `MAX_*` comentadas con su letra ASILO (`# L.`, `# I.`).
- Mensajes de error claros para el usuario; fallar pronto en vez de
  continuar con datos dudosos.
- Dependencias nuevas van en `requirements.txt` con versión mínima.

### Autoría
- **Cero rastro de IA en el repositorio.** Ni `Co-Authored-By` de un
  asistente, ni "Generated with", ni menciones a la herramienta en mensajes
  de commit, código, comentarios, documentación, PRs, reviews o nombres de
  rama. El trabajo lo firma quien lo publica. Si un mensaje ya redactado trae
  esa atribución, se quita antes de commitear, no después.
