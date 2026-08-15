import sys
sys.path.insert(0, '.')
import modules.chapter_writer.main as main
from typing import Any

def _payload() -> dict:
    return {
        "book_metadata": {"title": "Libro", "book_id": 1},
        "chapter_outline": {
            "number": 1,
            "title": "Introducción",
            "objective": "Presentar el tema",
            "sections": [
                {"heading": "Antecedentes", "objective": "Contexto histórico"},
            ],
        },
        "research": "Datos verificados del tema.",
        "sources": [
            {"url": "https://example.com/1", "title": "Fuente 1", "source_type": "web"},
            {"url": "https://example.com/2", "title": "Fuente 2", "source_type": "web"},
        ],
        "previous_chapter_summaries": ["Resumen previo."],
        "target_word_count": 1500,
        "style_guide": "formal",
    }

def _fake_result(text: str) -> Any:
    class FakeResult:
        def __init__(self):
            self.text = text
            self.provider = "ollama"
            self.model = "qwen-agent:latest"
            self.input_tokens = 10
            self.output_tokens = 20
            self.cost = 0.0
            self.raw_response = {"model": "qwen-agent:latest", "response": text}
    return FakeResult()

outline_headings = "\n".join(f"## {s['heading']}" for s in _payload()["chapter_outline"]["sections"])
short_md = outline_headings + "\n\n" + "word " * 100
expansion = "newcontent " * 1600
responses = [_fake_result(short_md), _fake_result(expansion)]
call_count = [0]

class FakeProvider:
    name = "ollama"
    model = "qwen-agent:latest"
    def generate(self, prompt: str, *args: Any, **kwargs: Any) -> Any:
        idx = call_count[0]
        call_count[0] += 1
        return responses[idx]

main.get_provider = lambda: FakeProvider()
main.DEFAULT_ROUTER_MODEL = "qwen-agent:latest"
main._write_artifacts = lambda *args, **kwargs: "data/artifacts/1/chapter_1/chapter.md"

out = main.execute(_payload())
print('WORDS', out['word_count'])
print('CALLS', call_count[0])
print('DUP', out['metadata']['duplicate_detected'])
print('REJ', out['metadata']['continuation_rejected_as_duplicate'])
