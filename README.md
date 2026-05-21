# AI Thread Safety Analysis — MVP

MVP decisions:
- Languages: Python, C
- Parser: Tree-sitter (with pragmatic fallbacks)
- Graph: NetworkX (in-memory)
- LLM: OpenAI (modular)
- Start: Parser service -> IR -> TIG -> Static analysis -> RAG -> LLM -> Agents -> VS Code

This workspace contains an initial parser service and CLI for repository scanning.
