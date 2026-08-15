import sys
sys.path.insert(0, '.')
import modules.chapter_writer.main as main

payload = {
    'book_metadata': {'title': 'Libro', 'book_id': 1},
    'chapter_outline': {
        'number': 1, 'title': 'Introducción', 'objective': 'Presentar el tema',
        'sections': [
            {'heading': 'Antecedentes', 'objective': 'Contexto histórico'},
        ],
    },
    'research': 'Datos verificados del tema.',
    'sources': [
        {'url': 'https://example.com/1', 'title': 'Fuente 1', 'source_type': 'web'},
        {'url': 'https://example.com/2', 'title': 'Fuente 2', 'source_type': 'web'},
    ],
    'previous_chapter_summaries': ['Resumen previo.'],
    'target_word_count': 1500,
    'style_guide': 'formal',
}

outline_headings = '\n'.join(f"## {s['heading']}" for s in payload['chapter_outline']['sections'])
short_md = outline_headings + '\n\n' + 'word ' * 100
expansion = 'newcontent ' * 1600

class FakeResult:
    text = expansion
    provider = 'ollama'
    model = 'qwen-agent:latest'
    input_tokens = 10
    output_tokens = 20
    cost = 0.0
    raw_response = {'model': 'qwen-agent:latest', 'response': expansion}

class FakeProvider:
    name = 'ollama'
    model = 'qwen-agent:latest'
    def generate(self, prompt, *args, **kwargs):
        return FakeResult()

main.get_provider = lambda: FakeProvider()
main.DEFAULT_ROUTER_MODEL = 'qwen-agent:latest'
main._write_artifacts = lambda *a, **k: 'data/artifacts/1/chapter_1/chapter.md'

out = main.execute(payload)
print('WORDS', out['word_count'])
print('GATE', out['quality_gate'])
print('ERRORS', out['quality_errors'])
print('DUP', out['metadata']['duplicate_detected'])
print('REJ', out['metadata']['continuation_rejected_as_duplicate'])
