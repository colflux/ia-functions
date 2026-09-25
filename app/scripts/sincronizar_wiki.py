"""Sincroniza ya la wiki del proyecto con el índice del asistente.

El asistente lo hace solo cada WIKI_INTERVALO_HORAS; este script sirve para la
primera carga y para ver el resultado después de cambiar la wiki:

    docker exec ia-functions-api-1 python -m app.scripts.sincronizar_wiki
"""
import sys

from app.bootstrap.container import get_wiki


def main() -> int:
    wiki = get_wiki()
    if wiki is None:
        print("WIKI_URL está vacío: la wiki no se usa.")
        return 1
    r = wiki.sincronizar()
    print(f"Páginas: {r.paginas} · PDF: {r.pdfs}")
    print(f"Actualizadas ({len(r.actualizadas)}):", *r.actualizadas, sep="\n  ")
    print(f"Retiradas ({len(r.retiradas)}):", *r.retiradas, sep="\n  ")
    if r.errores:
        print(f"Errores ({len(r.errores)}):", *r.errores, sep="\n  ")
    return 0


if __name__ == "__main__":
    sys.exit(main())
