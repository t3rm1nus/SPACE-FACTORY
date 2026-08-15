p='modules/chapter_writer/main.py'
lines=open(p,encoding='utf-8').read().splitlines(keepends=True)
inserts=[
    '        f"REGLAS ADICIONALES DE PROGRESI\u00d3N:\\n"\n',
    '        f"- Tu contribuci\u00f3n debe ampliar la secci\u00f3n con informaci\u00f3n NUEVA, no repetir lo ya escrito.\\n"\n',
    '        f"- Incluso una frase corta reutilizada del cap\u00edtulo existente provocar\u00e1 el rechazo de la respuesta.\\n"\n',
    '        f"- Si no puedes generar contenido nuevo para esta secci\u00f3n, entrega texto vac\u00edo en lugar de repetir.\\n"\n',
    '        f"\\n"\n',
]
for ins in inserts:
    lines.insert(622, ins)
open(p,'w',encoding='utf-8').write(''.join(lines))
print('INSERTED')
