"""
Diario de decisiones calibrado — fase 1: la bitácora.

Uso:
    python bitacora.py nueva     # registrar una decisión (te hace preguntas)
    python bitacora.py listar    # ver todas tus fichas
    python bitacora.py ver ID    # ver una ficha completa
"""
import sys
import json
from ficha import (ACCIONES, ESTADOS, TIPOS_PREDICCION, FichaInvalida,
                   nueva_ficha, guardar, cargar_todas, CARPETA)


def preguntar(texto, opciones=None):
    while True:
        extra = f" ({' / '.join(opciones)})" if opciones else ""
        r = input(f"{texto}{extra}: ").strip()
        if not opciones or r in opciones:
            return r
        print("  Elige una de las opciones.")


def lista(texto):
    print(f"{texto} (una por línea, Enter vacío para terminar):")
    items = []
    while (r := input("  - ").strip()):
        items.append(r)
    return items


def numero(texto):
    while True:
        try:
            return float(input(f"{texto}: ").strip().replace(",", "."))
        except ValueError:
            print("  Escribe un número, por ejemplo 0.65")


def cmd_nueva():
    print("\nNueva decisión. Escribe como si le explicaras a alguien más.\n")
    token = preguntar("Token (ticker)")
    accion = preguntar("¿Qué decidiste?", ACCIONES)
    tesis = preguntar("¿Por qué? (tu tesis)")
    catalizadores = lista("¿Qué tendría que pasar para que tu tesis se cumpla?")
    invalidacion = lista("¿Qué te haría cambiar de opinión?")
    estado = preguntar("¿Cómo te sentías al decidir?", ESTADOS)

    print("\nAhora una predicción que el sistema pueda comprobar solo:")
    for t, plantilla in TIPOS_PREDICCION.items():
        print(f"  {t}: {plantilla.format(token=token.upper(), referencia='X')}")
    tipo = preguntar("Tipo", tuple(TIPOS_PREDICCION))
    referencia = preguntar("Activo de referencia (ej. BTC)") if tipo == "supera_a" \
        else numero("Precio objetivo en USD")
    probabilidad = numero("¿Qué tan segura estás? (0.01 a 0.99)")
    fecha_res = preguntar("¿Para qué fecha? (AAAA-MM-DD)")

    try:
        ficha = nueva_ficha(token, accion, tesis, catalizadores, invalidacion, estado,
                            tipo, referencia, probabilidad, fecha_res)
    except FichaInvalida as e:
        sys.exit(f"\nLa ficha no se guardó. Corrige esto:{e}")
    ruta = guardar(ficha)
    print(f"\nGuardada: {ruta.name}")
    print(f"Predicción: {ficha['prediccion']['enunciado']} para el "
          f"{fecha_res}, con {probabilidad:.0%} de confianza.")


def cmd_listar():
    fichas = cargar_todas()
    if not fichas:
        print("Aún no hay fichas. Empieza con: python bitacora.py nueva")
        return
    print(f"\n{'ID':<24}{'Acción':<12}{'Ánimo':<9}{'Conf.':>6}  Predicción")
    print("-" * 80)
    for f in fichas:
        p = f["prediccion"]
        estado = "" if f["resultado"] is None else ("  ✓" if f["resultado"] else "  ✗")
        print(f"{f['id']:<24}{f['accion']:<12}{f['estado_animo']:<9}{p['probabilidad']:>6.0%}  "
              f"{p['enunciado']} ({p['fecha_resolucion']}){estado}")


def cmd_ver(id_):
    ruta = CARPETA / f"{id_}.json"
    if not ruta.exists():
        sys.exit(f"No existe la ficha {id_}. Usa 'listar' para ver los IDs.")
    print(json.dumps(json.loads(ruta.read_text(encoding="utf-8")), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    args = sys.argv[1:]
    if args[:1] == ["nueva"]:
        cmd_nueva()
    elif args[:1] == ["listar"]:
        cmd_listar()
    elif args[:1] == ["ver"] and len(args) == 2:
        cmd_ver(args[1])
    else:
        print(__doc__)