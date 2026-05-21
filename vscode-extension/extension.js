const vscode = require('vscode');
const path = require('path');
const cp = require('child_process');
const fs = require('fs');

function loadEnvFile(repoRoot) {
  const envPath = path.join(repoRoot, '.env');
  if (!fs.existsSync(envPath)) {
    return;
  }
  try {
    const content = fs.readFileSync(envPath, 'utf8');
    for (const line of content.split(/\r?\n/)) {
      const trimmed = line.trim();
      if (!trimmed || trimmed.startsWith('#')) {
        continue;
      }
      const eqIdx = trimmed.indexOf('=');
      if (eqIdx === -1) {
        continue;
      }
      const key = trimmed.slice(0, eqIdx).trim();
      let value = trimmed.slice(eqIdx + 1).trim();
      if ((value.startsWith('"') && value.endsWith('"')) || (value.startsWith("'") && value.endsWith("'"))) {
        value = value.slice(1, -1);
      }
      if (key && process.env[key] === undefined) {
        process.env[key] = value;
      }
    }
  } catch (e) {
    // Best-effort .env loading; ignore parsing errors.
  }
}

/**
 * Find the best Python executable.
 * Priority: .venv in workspace > system python3 > system python
 */
function findPython(repoRoot) {
  const venvPaths = [
    path.join(repoRoot, '.venv', 'Scripts', 'python.exe'),  // Windows venv
    path.join(repoRoot, '.venv', 'bin', 'python'),           // Unix  venv
    path.join(repoRoot, 'venv', 'Scripts', 'python.exe'),
    path.join(repoRoot, 'venv', 'bin', 'python'),
  ];
  for (const p of venvPaths) {
    if (fs.existsSync(p)) { return p; }
  }
  return process.platform === 'win32' ? 'python' : 'python3';
}

function mergeFixMaps(fixes, outputChannel) {
  const merged = {
    patched_lines: {},
    insert_before: {},
    insert_after: {},
  };

  fixes.forEach((fix) => {
    const patched = fix.patched_lines || {};
    const insertBefore = fix.insert_before || {};
    const insertAfter = fix.insert_after || {};

    Object.keys(patched).forEach((line) => {
      if (merged.patched_lines[line] && merged.patched_lines[line] !== patched[line]) {
        outputChannel.appendLine(`[merge] Skipping conflicting patch at line ${line}`);
        return;
      }
      merged.patched_lines[line] = patched[line];
    });

    Object.keys(insertBefore).forEach((line) => {
      merged.insert_before[line] = (merged.insert_before[line] || '') + insertBefore[line];
    });

    Object.keys(insertAfter).forEach((line) => {
      merged.insert_after[line] = (merged.insert_after[line] || '') + insertAfter[line];
    });
  });

  return merged;
}

function applyFixesToFile(filePath, fixes, outputChannel) {
  const original = fs.readFileSync(filePath, 'utf-8');
  const lines = original.split('\n');
  const merged = mergeFixMaps(fixes, outputChannel);

  const result = [];
  for (let i = 0; i < lines.length; i += 1) {
    const lineNum = i + 1;
    if (merged.insert_before[lineNum]) {
      result.push(merged.insert_before[lineNum]);
    }

    if (merged.patched_lines[lineNum] !== undefined) {
      result.push(merged.patched_lines[lineNum]);
    } else {
      result.push(lines[i]);
    }

    if (merged.insert_after[lineNum]) {
      result.push(merged.insert_after[lineNum]);
    }
  }

  const patchedContent = result.join('\n');
  fs.writeFileSync(filePath, patchedContent, 'utf-8');
}

function applyFullFileFix(filePath, content) {
  fs.writeFileSync(filePath, content, 'utf-8');
}

function activate(context) {
  const outputChannel = vscode.window.createOutputChannel('Concurrency Analyzer');

  let disposable = vscode.commands.registerCommand('concurrencyAnalyzer.open', function () {
    const panel = vscode.window.createWebviewPanel(
      'concurrencyAnalyzer',
      'Concurrency Analyzer',
      vscode.ViewColumn.One,
      {
        enableScripts: true,
        retainContextWhenHidden: true,
        localResourceRoots: [vscode.Uri.file(path.join(context.extensionPath, 'media'))]
      }
    );

    const scriptUri = panel.webview.asWebviewUri(
      vscode.Uri.file(path.join(context.extensionPath, 'media', 'main.js'))
    );
    const styleUri = panel.webview.asWebviewUri(
      vscode.Uri.file(path.join(context.extensionPath, 'media', 'styles.css'))
    );

    panel.webview.html = getWebviewContent(scriptUri, styleUri);

    // Send initial context (active editor file path) to webview
    try {
      const activePath = vscode.window.activeTextEditor
        && vscode.window.activeTextEditor.document
        && vscode.window.activeTextEditor.document.uri.fsPath;
      panel.webview.postMessage({ command: 'init', activePath: activePath });
    } catch (e) { /* ignore */ }

    // Track active editor changes so the webview always knows the current file
    const editorWatcher = vscode.window.onDidChangeActiveTextEditor((editor) => {
      if (editor && editor.document) {
        panel.webview.postMessage({
          command: 'init',
          activePath: editor.document.uri.fsPath
        });
      }
    });
    context.subscriptions.push(editorWatcher);

    // Handle messages from the webview
    panel.webview.onDidReceiveMessage(async (message) => {
      if (message.command === 'analyzeFile') {
        const filePath = message.path;
        try {
          const repoRoot = path.resolve(context.extensionPath, '..');
          loadEnvFile(repoRoot);
          const py = findPython(repoRoot);

          // ── Use the new end-to-end pipeline script ──
          const script = path.join(repoRoot, 'scripts', 'analyze_file.py');
          const hasLlmKey = Boolean(
            process.env.GEMINI_API_KEY
            || process.env.GOOGLE_API_KEY
            || process.env.OPENROUTER_API_KEY
            || process.env.OLLAMA_MODEL
          );
          const args = [script, '--json'];
          if (hasLlmKey && !message.quick) {
            args.push('--llm');
          }
          if (message.quick) {
            args.push('--quick');
          }
          args.push(filePath || '');
          const env = Object.assign({}, process.env);

          outputChannel.appendLine(`--- Analysis started ---`);
          outputChannel.appendLine(`  python : ${py}`);
          outputChannel.appendLine(`  file   : ${filePath}`);
          outputChannel.appendLine(`  llm    : ${hasLlmKey && !message.quick ? 'enabled' : 'disabled'}`);
          outputChannel.appendLine(`  quick  : ${message.quick ? 'yes' : 'no'}`);

          // Tell webview we are loading
          panel.webview.postMessage({ command: 'analysisStarted' });

          const timeoutMs = hasLlmKey ? 300_000 : 120_000;

          cp.execFile(py, args, {
            env,
            cwd: repoRoot,
            maxBuffer: 50 * 1024 * 1024,
            timeout: timeoutMs
          }, (err, stdout, stderr) => {
            if (stderr) { outputChannel.appendLine('[stderr]\n' + stderr); }
            if (err) {
              outputChannel.appendLine('[error] ' + String(err));
              panel.webview.postMessage({
                command: 'analysisError',
                error: String(err),
                stderr: stderr || ''
              });
              return;
            }
            try {
              const data = JSON.parse(stdout);
              outputChannel.appendLine(`[success] elements=${(data.elements||[]).length}`);
              panel.webview.postMessage({ command: 'analysisResult', data });
            } catch (e) {
              outputChannel.appendLine('[parse-error] stdout (first 500 chars):\n' + stdout.slice(0, 500));
              panel.webview.postMessage({
                command: 'analysisError',
                error: 'Failed to parse JSON from backend: ' + e.message,
                stderr: stderr || ''
              });
            }
          });
        } catch (e) {
          panel.webview.postMessage({ command: 'analysisError', error: String(e) });
        }
      } else if (message.command === 'applyFix') {
        // ── Apply a fix to the source file ──
        (async () => {
          try {
            const fix = message.fix;
            if (!fix || !fix.file_path) {
              vscode.window.showErrorMessage('No fix data received');
              return;
            }

            const filePath = fix.file_path;
            outputChannel.appendLine(`─── Applying fix: ${fix.strategy} to ${filePath} ───`);

            // Read the original file
            const fs = require('fs');
            const original = fs.readFileSync(filePath, 'utf-8');
            const lines = original.split('\n');

            // Apply the patch
            const patchedLines = fix.patched_lines || {};
            const insertBefore = fix.insert_before || {};
            const insertAfter = fix.insert_after || {};

            // Build the new content line by line
            const result = [];
            for (let i = 0; i < lines.length; i++) {
              const lineNum = i + 1; // 1-indexed

              // Insert before this line
              if (insertBefore[lineNum]) {
                result.push(insertBefore[lineNum]);
              }

              // Replace or keep the line
              if (patchedLines[lineNum] !== undefined) {
                result.push(patchedLines[lineNum]);
              } else {
                result.push(lines[i]);
              }

              // Insert after this line
              if (insertAfter[lineNum]) {
                result.push(insertAfter[lineNum]);
              }
            }

            const patchedContent = result.join('\n');

            // Write the patched file
            fs.writeFileSync(filePath, patchedContent, 'utf-8');
            outputChannel.appendLine(`  ✅ File patched: ${filePath}`);

            // Open the file in the editor so user can see the change
            const doc = await vscode.workspace.openTextDocument(filePath);
            await vscode.window.showTextDocument(doc, { preview: false });

            vscode.window.showInformationMessage(
              `Fix applied: ${fix.strategy} → ${path.basename(filePath)}`
            );
          } catch (e) {
            outputChannel.appendLine(`[applyFix error] ${e}`);
            vscode.window.showErrorMessage(`Failed to apply fix: ${e.message}`);
          }
        })();
      } else if (message.command === 'applyAllFixes') {
        // ── Apply all selected fixes to the source file ──
        (async () => {
          try {
            const fixes = message.fixes || [];
            if (!Array.isArray(fixes) || fixes.length === 0) {
              vscode.window.showErrorMessage('No fixes received');
              return;
            }

            const filePath = fixes[0].file_path;
            if (!filePath) {
              vscode.window.showErrorMessage('Fixes did not include a file path');
              return;
            }

            const multipleFiles = fixes.some(f => f.file_path && f.file_path !== filePath);
            if (multipleFiles) {
              vscode.window.showErrorMessage('Fixes target multiple files; apply fixes per file.');
              return;
            }

            outputChannel.appendLine(`─── Applying ${fixes.length} fixes to ${filePath} ───`);
            applyFixesToFile(filePath, fixes, outputChannel);
            outputChannel.appendLine(`  ✅ File patched: ${filePath}`);

            const doc = await vscode.workspace.openTextDocument(filePath);
            await vscode.window.showTextDocument(doc, { preview: false });

            vscode.window.showInformationMessage(
              `Applied ${fixes.length} fixes to ${path.basename(filePath)}`
            );
          } catch (e) {
            outputChannel.appendLine(`[applyAllFixes error] ${e}`);
            vscode.window.showErrorMessage(`Failed to apply fixes: ${e.message}`);
          }
        })();
      } else if (message.command === 'applyFullFix') {
        // ── Apply the full-file LLM fix ──
        (async () => {
          try {
            const filePath = message.filePath;
            const content = message.content;
            if (!filePath || !content) {
              vscode.window.showErrorMessage('No full-file fix content received');
              return;
            }

            outputChannel.appendLine(`─── Applying full-file fix to ${filePath} ───`);
            applyFullFileFix(filePath, content);
            outputChannel.appendLine(`  ✅ File replaced: ${filePath}`);

            const doc = await vscode.workspace.openTextDocument(filePath);
            await vscode.window.showTextDocument(doc, { preview: false });

            vscode.window.showInformationMessage(
              `Applied full-file fix to ${path.basename(filePath)}`
            );
          } catch (e) {
            outputChannel.appendLine(`[applyFullFix error] ${e}`);
            vscode.window.showErrorMessage(`Failed to apply full fix: ${e.message}`);
          }
        })();
      }
    }, undefined, context.subscriptions);
  });

  context.subscriptions.push(disposable);
}

// ---------------------------------------------------------------------------
// Webview HTML  (injected URIs come from extension context)
// ---------------------------------------------------------------------------
function getWebviewContent(scriptUri, styleUri) {
  return `<!doctype html>
<html>
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <link rel="stylesheet" href="${styleUri}">
</head>
<body>
  <!-- Top toolbar -->
  <div id="toolbar">
    <button id="analyzeBtn" title="Analyze file for concurrency issues (uses LLM for explanations)">
      <span class="icon">▶</span> Analyze Current File
    </button>
    <button id="sidebarToggleBtn" class="toolbar-toggle" title="Toggle fixes sidebar" aria-pressed="false">
      <span class="icon">🧰</span> Fixes Panel
    </button>
    <span id="fileLabel">No file selected</span>
    <div id="spinnerWrap" class="hidden">
      <div class="spinner"></div>
      <span>Analyzing…</span>
    </div>
  </div>

  <!-- Summary bar (hidden until results arrive) -->
  <div id="summaryBar" class="hidden"></div>

  <!-- Main Content Area: Split between Graph and Sidebar -->
  <div id="mainContent">
    
    <!-- Left side: Graph -->
    <div id="graphContainer">
      <div id="graph"></div>
      
      <!-- Detail panel (click a node to inspect) -->
      <div id="detailPanel" class="hidden">
        <div id="detailTitle">Details</div>
        <div id="detailBody"></div>
      </div>

      <!-- Legend -->
      <div id="legend">
        <span class="legend-item"><span class="dot" style="background:#4fc3f7"></span>Thread</span>
        <span class="legend-item"><span class="dot" style="background:#ffb74d"></span>Variable</span>
        <span class="legend-item"><span class="dot" style="background:#81c784"></span>Sync</span>
        <span class="legend-item"><span class="dot dot-triangle" style="background:#e57373"></span>Finding</span>
        <span class="legend-item"><span class="dot" style="background:#ce93d8"></span>File</span>
      </div>
    </div>

  </div>

  <!-- Problems & Fixes Sidebar (outside mainContent to avoid layout conflicts) -->
  <div id="problemsSidebar" class="hidden">
    <div id="sidebarResizer" class="sidebar-resizer" title="Drag to resize"></div>
    <div class="sidebar-header">
      <h3>🔧 Problems &amp; Fixes</h3>
      <span id="problemCount" class="badge">0</span>
    </div>
    <div id="problemsList"></div>
  </div>

  <script src="https://unpkg.com/cytoscape@3.21.1/dist/cytoscape.min.js"></script>
  <script src="${scriptUri}"></script>
</body>
</html>`;
}

exports.activate = activate;
function deactivate() {}
module.exports = { activate, deactivate };
