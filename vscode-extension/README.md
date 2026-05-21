# Concurrency Analyzer (VS Code Extension)

Lightweight extension to visualize concurrency findings and a small KG using Cytoscape.js inside a Webview panel.

Usage
- Run the command `Concurrency Analyzer: Open Panel` from the command palette.
- Click `Analyze Current File` to run the backend analysis and visualize the result.

Notes
- The extension invokes the Python script `scripts/run_agent_validation.py` in the repository root. Ensure your Python virtualenv is active when running the command.
- The webview expects the backend to print a JSON result to stdout. The orchestrator already produces sanitized JSON in `scripts/run_agent_validation.py`.
