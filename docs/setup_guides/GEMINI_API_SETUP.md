#!/usr/bin/env python
"""
GEMINI API SETUP GUIDE
======================

This guide shows exactly where to put your Google Gemini API key.
"""

SETUP_GUIDE = """
╔══════════════════════════════════════════════════════════════════════════════╗
║                    GOOGLE GEMINI API KEY SETUP                              ║
╚══════════════════════════════════════════════════════════════════════════════╝

STEP 1: Get Your API Key
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  1. Go to: https://aistudio.google.com/app/apikeys
  2. Click "Create API Key" 
  3. Select or create a Google Cloud project
  4. Copy the key (looks like: AIza... or similar)


STEP 2: Set Environment Variable (PowerShell)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

OPTION A: Temporary (Current Session Only)
───────────────────────────────────────────
Copy this command and paste into PowerShell:

    $Env:GOOGLE_API_KEY = "AIza... YOUR_KEY_HERE"

Then run the LLM script:

    python scripts/run_llm_live_gemini.py


OPTION B: Permanent (System-Wide)
──────────────────────────────────
Copy this command and paste into PowerShell (as Admin):

    [Environment]::SetEnvironmentVariable("GOOGLE_API_KEY", "AIza... YOUR_KEY_HERE", "User")

Then RESTART your terminal/IDE.


STEP 3: Run the LLM Analysis Script
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

PowerShell:

    python scripts/run_llm_live_gemini.py

The script will:
  1. Check for GOOGLE_API_KEY ✓
  2. Parse sample concurrency code ✓
  3. Build IR and analyze with Gemini ✓
  4. Validate LLM output deterministically ✓
  5. Attach results to findings ✓


STEP 4: Expected Output
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

======================================================================
Live Gemini LLM Analysis
======================================================================

✓ Using GOOGLE_API_KEY (length: 39 chars)

[1/5] Parsing sample files...
  ✓ tests/sample.c
  ✓ tests/sample.py

[2/5] Normalizing to IR...
  ✓ Variables: 4
  ✓ Accesses: 4
  ✓ Threads: 3

[3/5] Running static analysis...
  ✓ Found 0 data races

[4/5] Building enriched TIG...
  ✓ TIG nodes: 13
  ✓ TIG edges: 4

[5/5] Analyzing with Gemini...

  Finding 1: demo_1
  Type: data_race

  LLM Analysis:
    is_real_race: True
    severity: high
    root_cause: Unprotected access in parallel region
    runtime_impact: Possible data corruption
    recommended_fix: Protect with #pragma omp critical
    confidence: 92

  Validation:
    Schema OK: True
    Facts OK: True

  ✓ llm_analysis attached to finding

======================================================================
Analysis complete!
======================================================================


TROUBLESHOOTING
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

❌ "GOOGLE_API_KEY not set"
  → Set the environment variable (see STEP 2 above)
  → Restart terminal after using "Permanent" option

❌ "google-generativeai package not installed"
  → Run: pip install google-generativeai
  → Already done in this session ✓

❌ "Invalid API key"
  → Copy key again from https://aistudio.google.com/app/apikeys
  → Make sure no spaces are included

❌ "Rate limit exceeded"
  → Gemini has free tier limits; wait a few minutes before retrying
  → Or check quota at https://console.cloud.google.com/apis/dashboard


PRICING & LIMITS
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

✓ Gemini API is FREE (as of 2025) for reasonable usage
✓ Free tier includes:
  - Up to 60 requests/minute
  - Up to 1,500 requests/day
  - No credit card required

Models available:
  - gemini-1.5-pro (more powerful, slightly higher cost)
  - gemini-1.5-flash (faster, lower cost) ← default in script
  - gemini-2.0-flash (latest)


WHERE YOUR KEY IS USED
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

File: backend/llm/llm_orchestrator.py
  └─ Line: gets from os.getenv('GOOGLE_API_KEY')
  └─ Configures Google Generative AI client
  └─ Used in LLMOrchestrator.analyze_finding()

Flow:
  ConcurrencyIssue
    ↓ (RAG retrieves context)
    ↓ (Prompt templates format message)
    ↓ (LLM Orchestrator calls Gemini API)
    ↓ (Validators check response)
    ↓ (llm_analysis attached to finding)
"""

print(SETUP_GUIDE)

if __name__ == '__main__':
    pass
