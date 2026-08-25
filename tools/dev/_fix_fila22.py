import re
p = 'PROJECT_MASTER_STATUS.md'
c = open(p, encoding='utf-8').read()
old = "(2) tras el fix de §17 #21, en este camino `title_en`/`description_en` quedan None por diseño\n → libros bilingües con muchos capítulos seguirán mostrando título de capítulo/portada en español pese al fix, aunque las cabeceras de seción sí queden traducidas (outline_en vía mapeo canónico)."
new = "(2) tras el fix de §17 #21 + enmienda de hoy, en este camino `title_en`/`description_en` quedan None por diseño → libros bilingües con muchos capítulos seguirán mostrando título de capítulo/portada en español pese al fix; PERO `outline_en` ya NO se pierde (enmienda 2026-08-25 aplica `_deterministic_outline_en` como red de seguridad también en el camino de éxito con traducción fallida), así las cabeceras de sección quedan traducidas (Introduction/Development/Conclusion) aunque `title_en`/`description_en` no."
if old in c:
    c = c.replace(old, new, 1)
    open(p, 'w', encoding='utf-8').write(c)
    print("REEMPLAZADO OK")
else:
    # Buscar con regex para diagnosticar
    m = re.search(r"\(2\) tras el fix de §17 #21.*?mapeo can.nico\)\.", c, re.DOTALL)
    if m:
        print("MATCH REGEX (diagnóstico):")
        print(repr(m.group()[:200]))
    else:
        print("NO ENCONTRADO NI CON REGEX")



