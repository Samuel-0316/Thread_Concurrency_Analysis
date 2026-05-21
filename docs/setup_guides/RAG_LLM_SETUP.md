# RAG/LLM Pipeline Setup Guide

## Overview

The RAG/LLM pipeline enriches static analysis findings with:
- **RAG (Retrieval-Augmented Generation)**: Extracts source code context around each finding
- **LLM Analysis**: Uses OpenAI to reason about findings, assess severity, and provide recommendations

## Architecture

```
Static Findings (JSON)
    ↓
RAG Retriever (context extraction)
    ↓
LLM Orchestrator (OpenAI API call)
    ↓
Enriched Analysis (severity, recommendations, etc.)
    ↓
JSON Report
```

## Quick Start (Mock Mode - No API Key Required)

```bash
python tests/run_rag_llm_pipeline.py --max-findings 5
```

This runs with mock LLM responses for demonstration.

## Enable Real OpenAI API

### 1. Get an API Key

- Go to https://platform.openai.com/api-keys
- Create a new API key
- Copy the key (starts with `sk-`)

### 2. Configure Environment

Create/edit `.env` file in the project root:

```
OPENAI_API_KEY=sk-your-actual-key-here
```

Or set the environment variable:

```bash
# PowerShell
$env:OPENAI_API_KEY = "sk-your-key-here"

# Bash
export OPENAI_API_KEY="sk-your-key-here"
```

### 3. Install OpenAI Package

```bash
pip install openai
```

Or from requirements:

```bash
pip install -r requirements.txt
```

### 4. Run with Real API

```bash
python tests/run_rag_llm_pipeline.py --max-findings 10 --use-api
```

## Module Reference

### RAG Retriever (`backend/rag/rag_retriever.py`)

**Key Functions:**

- `RAGRetriever.get_file_context(file_path, line_number, context_lines)` - Get source code snippet around a finding
- `RAGRetriever.get_function_containing_line(file_path, line_number)` - Identify containing function
- `RAGRetriever.get_variable_usage_context(file_path, variable_name)` - Find all usages of a variable
- `retrieve_batch_context(findings, repo_path, max_findings)` - Batch retrieval for multiple findings

**Example:**

```python
from backend.rag.rag_retriever import RAGRetriever

retriever = RAGRetriever()
context = retriever.get_file_context("src/main.c", line_number=42, context_lines=5)
print(context['content'])  # Source code around line 42
```

### LLM Orchestrator (`backend/llm/llm_orchestrator.py`)

**Key Functions:**

- `LLMOrchestrator.analyze_finding(finding, context_summary)` - Analyze a single finding
- `LLMOrchestrator.analyze_batch(findings, context_summaries, max_results)` - Batch analysis
- `analyze_findings_with_llm(findings, context_summaries, model, max_findings)` - Convenience wrapper

**Example:**

```python
from backend.llm.llm_orchestrator import LLMOrchestrator

orchestrator = LLMOrchestrator(model="gpt-4")
result = orchestrator.analyze_finding(
    finding={'file': 'src/main.c', 'variable': 'x', 'line': 42},
    context_summary="source code context here"
)
print(result['analysis']['severity'])  # 'high', 'medium', 'low', etc.
```

## Output Format

The pipeline produces JSON with this structure:

```json
{
  "finding": {
    "file": "path/to/file.c",
    "variable": "var_name",
    "line": 42,
    "omp_kind": "parallel_for"
  },
  "analysis": {
    "severity": "high",
    "is_real_race": true,
    "explanation": "Variable accessed without synchronization...",
    "impact": "Multiple threads may corrupt data...",
    "reproduction": "Run with high thread count...",
    "recommendations": ["Add #pragma omp critical", "..."],
    "confidence_pct": 92
  },
  "model": "gpt-4"
}
```

## Cost Estimation

Analyzing DataRaceBench findings with GPT-4:

- **Per-finding cost**: ~$0.01-0.05 (varies by context size)
- **100 findings**: ~$1-5 total
- **Full 169 findings**: ~$2-8 total

For cost-sensitive analysis, use `gpt-3.5-turbo` instead:

```python
orchestrator = LLMOrchestrator(model="gpt-3.5-turbo")
```

Cost reduction: ~90% cheaper, slightly lower quality analysis.

## Customization

### Change LLM Model

```python
orchestrator = LLMOrchestrator(model="gpt-3.5-turbo")
# or
orchestrator = LLMOrchestrator(model="gpt-4-turbo")
```

### Adjust Analysis Prompt

Edit `LLMOrchestrator._build_analysis_prompt()` to customize what the LLM analyzes.

### Filter Findings Before Analysis

```python
# Only analyze high-confidence findings
high_conf_findings = [f for f in findings if f.get('confidence', 0) > 80]
results = analyze_findings_with_llm(high_conf_findings, ...)
```

## Troubleshooting

### "OPENAI_API_KEY not set"

- Ensure `.env` file exists in project root
- Verify key is correct (starts with `sk-`)
- Run `echo $env:OPENAI_API_KEY` to check if set

### "openai module not installed"

```bash
pip install openai
```

### Rate limit errors

- Wait a few seconds and retry
- Use smaller `max_findings` batches
- OpenAI free tier has limits; check your account

### Slow responses

- Use `gpt-3.5-turbo` (faster, cheaper)
- Reduce `context_lines` in RAG retriever
- Analyze fewer findings

## Integration with Other Components

The RAG/LLM pipeline can feed into:

1. **Multi-agent pipeline**: Agents could use LLM insights to prioritize fixes
2. **REST API**: Expose analysis as endpoint: `POST /api/analyze_findings`
3. **VS Code extension**: Display recommendations in editor
4. **Report generation**: Combine findings + analysis into HTML report

## Future Enhancements

- [ ] Caching LLM responses to reduce API calls
- [ ] Batch analysis for better cost efficiency
- [ ] Custom prompts for different OpenMP pragmas
- [ ] Integration with code refactoring tools
- [ ] Feedback loop to improve analysis accuracy
