/* ── Concurrency Analyzer · Webview Script ────────────────────────── */
(() => {
  const vscode = typeof acquireVsCodeApi === 'function' ? acquireVsCodeApi() : null;

  // ── DOM refs ──
  const analyzeBtn   = document.getElementById('analyzeBtn');
  const fileLabel    = document.getElementById('fileLabel');
  const spinnerWrap  = document.getElementById('spinnerWrap');
  const summaryBar   = document.getElementById('summaryBar');
  const graphDiv     = document.getElementById('graph');
  const detailPanel  = document.getElementById('detailPanel');
  const detailTitle  = document.getElementById('detailTitle');
  const detailBody   = document.getElementById('detailBody');
  const sidebar      = document.getElementById('problemsSidebar');
  const sidebarToggleBtn = document.getElementById('sidebarToggleBtn');
  const sidebarResizer = document.getElementById('sidebarResizer');

  let activePath = null;
  let cy = null;   // Cytoscape instance
  let lastData = null;  // Last analysis result (for KG queries)
  let sidebarInitialized = false;
  let isSidebarOpen = false;
  const MIN_SIDEBAR_WIDTH = 260;
  const MAX_SIDEBAR_WIDTH = 640;

  // ── Helpers ──
  function show(el)  { el.classList.remove('hidden'); }
  function hide(el)  { el.classList.add('hidden'); }
  function basename(p) { return p ? p.replace(/^.*[\\/]/, '') : ''; }

  function setSidebarWidth(widthPx) {
    const clamped = Math.max(MIN_SIDEBAR_WIDTH, Math.min(MAX_SIDEBAR_WIDTH, widthPx));
    document.documentElement.style.setProperty('--sidebar-width', `${clamped}px`);
    return clamped;
  }

  function setSidebarOpen(open) {
    if (!sidebar) return;
    isSidebarOpen = open;
    if (open) {
      sidebar.classList.add('open');
      document.body.classList.add('sidebar-open');
      if (sidebarToggleBtn) sidebarToggleBtn.setAttribute('aria-pressed', 'true');
    } else {
      sidebar.classList.remove('open');
      document.body.classList.remove('sidebar-open');
      if (sidebarToggleBtn) sidebarToggleBtn.setAttribute('aria-pressed', 'false');
    }
  }

  // Initialize sidebar sizing and toggle
  setSidebarWidth(380);
  if (sidebarToggleBtn) {
    sidebarToggleBtn.addEventListener('click', () => {
      if (sidebar && sidebar.classList.contains('hidden')) {
        return;
      }
      setSidebarOpen(!isSidebarOpen);
    });
  }

  if (sidebarResizer) {
    sidebarResizer.addEventListener('mousedown', (e) => {
      if (!isSidebarOpen) return;
      e.preventDefault();
      const startX = e.clientX;
      const startWidth = sidebar.getBoundingClientRect().width;
      document.body.classList.add('sidebar-resizing');

      function onMove(evt) {
        const delta = startX - evt.clientX;
        setSidebarWidth(startWidth + delta);
      }

      function onUp() {
        document.body.classList.remove('sidebar-resizing');
        window.removeEventListener('mousemove', onMove);
        window.removeEventListener('mouseup', onUp);
      }

      window.addEventListener('mousemove', onMove);
      window.addEventListener('mouseup', onUp);
    });
  }

  // ── Analyze button ──
  analyzeBtn.addEventListener('click', () => {
    if (!activePath) {
      showError('No file is open. Open a source file first.');
      return;
    }
    if (vscode) {
      vscode.postMessage({ command: 'analyzeFile', path: activePath });
    }
  });

  // ── Deep Analyze (LLM) button ──
  const deepAnalyzeBtn = document.getElementById('deepAnalyzeBtn');
  if (deepAnalyzeBtn) {
    deepAnalyzeBtn.addEventListener('click', () => {
      if (!activePath) {
        showError('No file is open. Open a source file first.');
        return;
      }
      if (vscode) {
        vscode.postMessage({ command: 'analyzeFile', path: activePath, useLLM: true });
      }
    });
  }


  // ── Message handler ──
  window.addEventListener('message', (event) => {
    const msg = event.data;

    switch (msg.command) {
      case 'init':
        activePath = msg.activePath || null;
        fileLabel.textContent = activePath ? basename(activePath) : 'No file selected';
        break;

      case 'analysisStarted':
        show(spinnerWrap);
        hide(summaryBar);
        hide(detailPanel);
        analyzeBtn.disabled = true;
        if (deepAnalyzeBtn) deepAnalyzeBtn.disabled = true;
        break;

      case 'analysisResult':
        hide(spinnerWrap);
        analyzeBtn.disabled = false;
        if (deepAnalyzeBtn) deepAnalyzeBtn.disabled = false;
        renderResult(msg.data || {});
        break;

      case 'analysisError':
        hide(spinnerWrap);
        analyzeBtn.disabled = false;
        showError(msg.error || 'Unknown error');
        break;
    }
  });

  // ── Error display ──
  function showError(text) {
    summaryBar.innerHTML = `<span class="error-text">⚠ ${escapeHtml(text)}</span>`;
    show(summaryBar);
  }

  function escapeHtml(s) {
    const d = document.createElement('div');
    d.textContent = s;
    return d.innerHTML;
  }

  // ── Render pipeline result ──
  function renderResult(data) {
    lastData = data;

    // Summary bar
    const s = data.summary || {};
    const unguarded = (data.knowledge_graph || {}).unguarded_writes || [];
    const fixes = data.fixes || [];
    const verifiedFixes = s.fixes_verified || fixes.filter(f => f.validated && f.validation_result && f.validation_result.includes('removes')).length;
    summaryBar.innerHTML = [
      tag('Threads', s.threads),
      tag('Variables', s.variables),
      tag('Sync Points', s.sync_points),
      tag('TIG', `${s.tig_nodes || 0}n/${s.tig_edges || 0}e`),
      tag('KG', `${s.kg_nodes || 0}n/${s.kg_edges || 0}e`),
      `<span class="summary-tag ${(s.openmp_races || 0) > 0 ? 'alert' : ''}">${s.openmp_races || 0} Races</span>`,
      `<span class="summary-tag ${unguarded.length > 0 ? 'alert' : ''}">${unguarded.length} Unguarded</span>`,
      `<span class="summary-tag" style="color:#81c784">${verifiedFixes} Fixes verified</span>`,
    ].join('');
    show(summaryBar);

    // Elements
    const elements = data.elements || [];
    if (elements.length === 0) {
      graphDiv.innerHTML = '<p class="empty-msg">No graph elements produced. The file may not contain concurrency constructs.</p>';
      return;
    }

    buildGraph(elements);

    // Populate the problems sidebar
    try {
      renderSidebar(data);
    } catch (err) {
      const sidebar = document.getElementById('problemsSidebar');
      const list = document.getElementById('problemsList');
      if (sidebar && list) {
        sidebar.classList.remove('hidden');
        list.innerHTML = '<div style="color:#e57373;padding:16px;">Sidebar error: ' + String(err) + '</div>';
      }
    }
  }

  function tag(label, value) {
    return `<span class="summary-tag">${label}: <b>${value ?? '–'}</b></span>`;
  }

  // ── Cytoscape graph ──
  function buildGraph(elements) {
    // Destroy previous instance
    if (cy) { cy.destroy(); cy = null; }
    graphDiv.innerHTML = '';

    cy = cytoscape({
      container: graphDiv,
      elements: elements,
      minZoom: 0.2,
      maxZoom: 4,
      style: [
        // ── Nodes ──
        {
          selector: 'node',
          style: {
            'label': 'data(label)',
            'text-valign': 'center',
            'text-halign': 'center',
            'font-size': '11px',
            'font-family': 'Segoe UI, system-ui, sans-serif',
            'color': '#fff',
            'text-outline-width': 1.5,
            'text-outline-color': '#333',
            'width': 40,
            'height': 40,
            'border-width': 2,
            'border-color': '#222',
          }
        },
        {
          selector: 'node[type="thread"]',
          style: {
            'background-color': '#4fc3f7',
            'shape': 'ellipse',
            'border-color': '#0288d1',
          }
        },
        {
          selector: 'node[type="variable"]',
          style: {
            'background-color': '#ffb74d',
            'shape': 'round-rectangle',
            'border-color': '#f57c00',
            'width': 'label',
            'height': 32,
            'padding': '8px',
            'text-wrap': 'wrap',
            'text-max-width': '120px',
          }
        },
        {
          selector: 'node[type="variable"][safe="true"]',
          style: {
            'background-color': '#81c784',
            'border-color': '#388e3c',
          }
        },
        {
          selector: 'node[type="sync"]',
          style: {
            'background-color': '#81c784',
            'shape': 'diamond',
            'border-color': '#388e3c',
            'width': 36,
            'height': 36,
          }
        },
        {
          selector: 'node[type="finding"]',
          style: {
            'background-color': '#e57373',
            'shape': 'triangle',
            'border-color': '#c62828',
            'width': 44,
            'height': 44,
            'font-weight': 'bold',
          }
        },
        {
          selector: 'node[type="file"]',
          style: {
            'background-color': '#ce93d8',
            'shape': 'round-rectangle',
            'border-color': '#7b1fa2',
            'width': 'label',
            'height': 28,
            'padding': '8px',
            'font-size': '10px',
          }
        },

        // ── Edges ──
        {
          selector: 'edge',
          style: {
            'width': 1.5,
            'line-color': '#666',
            'target-arrow-color': '#666',
            'target-arrow-shape': 'triangle',
            'curve-style': 'bezier',
            'label': 'data(label)',
            'font-size': '8px',
            'color': '#aaa',
            'text-rotation': 'autorotate',
            'text-margin-y': -8,
          }
        },
        {
          selector: 'edge[type="may_access"]',
          style: { 'line-color': '#ffb74d', 'target-arrow-color': '#ffb74d' }
        },
        {
          selector: 'edge[type="contains"]',
          style: { 'line-color': '#ce93d8', 'target-arrow-color': '#ce93d8', 'line-style': 'dotted' }
        },
        {
          selector: 'edge[type="acquires"]',
          style: { 'line-color': '#81c784', 'target-arrow-color': '#81c784' }
        },
        {
          selector: 'edge[type="protected_by"]',
          style: { 'line-color': '#4caf50', 'target-arrow-color': '#4caf50', 'line-style': 'dashed' }
        },
        {
          selector: 'edge[type="detected_issue"]',
          style: { 'line-color': '#e57373', 'target-arrow-color': '#e57373', 'width': 2.5 }
        },
        {
          selector: 'edge[type="spawns"]',
          style: { 'line-color': '#4fc3f7', 'target-arrow-color': '#4fc3f7', 'width': 2 }
        },
        {
          selector: 'edge[type="synchronized_with"]',
          style: { 'line-color': '#00bcd4', 'target-arrow-color': '#00bcd4', 'line-style': 'dashed' }
        },
        {
          selector: 'edge[type="happens_before"]',
          style: { 'line-style': 'dashed', 'line-color': '#90a4ae', 'target-arrow-color': '#90a4ae' }
        },

        // ── Interaction states ──
        {
          selector: '.faded',
          style: { 'opacity': 0.12 }
        },
        {
          selector: '.highlighted',
          style: { 'border-width': 4, 'border-color': '#ffd600', 'z-index': 999 }
        },
      ],

      layout: {
        name: 'cose',
        animate: true,
        animationDuration: 600,
        nodeDimensionsIncludeLabels: true,
        idealEdgeLength: 100,
        nodeRepulsion: 8000,
        gravity: 0.3,
        padding: 30,
      }
    });

    // ── Tap node → highlight neighbourhood & show detail ──
    cy.on('tap', 'node', (evt) => {
      const node = evt.target;
      cy.elements().removeClass('highlighted faded');
      cy.elements().addClass('faded');
      node.removeClass('faded').addClass('highlighted');
      node.neighborhood().removeClass('faded');

      // Show details
      const d = node.data();
      detailTitle.textContent = d.label || d.id;

      let html = Object.entries(d)
        .filter(([k]) => !['id', 'label', 'color', 'shape'].includes(k))
        .map(([k, v]) => `<div><b>${k}:</b> ${escapeHtml(String(v))}</div>`)
        .join('');

      // If this is a variable node, show KG query results
      if (d.type === 'variable' && lastData && lastData.knowledge_graph) {
        const varName = d.label || d.id.replace('var:', '');
        const summaries = lastData.knowledge_graph.variable_summaries || [];
        const match = summaries.find(s => s.variable === varName);
        if (match) {
          html += '<div class="kg-section"><b>-- KG Queries --</b></div>';
          html += `<div><b>Threads:</b> ${match.threads.length > 0 ? match.threads.join(', ') : 'none'}</div>`;
          html += `<div><b>Locks:</b> ${match.locks.length > 0 ? match.locks.join(', ') : 'none'}</div>`;
          html += `<div><b>Protected:</b> ${match.is_protected ? 'Yes' : '<span style="color:#e57373">No</span>'}</div>`;
          html += `<div><b>Findings:</b> ${match.finding_count}</div>`;
          if (match.findings && match.findings.length > 0) {
            match.findings.forEach(f => {
              html += `<div style="margin-left:8px;color:#e57373">- ${escapeHtml(f.subtype || f.id)}</div>`;
            });
          }
        }
      }

      // If this is a finding node, show severity and fix suggestions
      if (d.type === 'finding' && lastData) {
        // KG detail
        if (lastData.knowledge_graph) {
          const unguarded = lastData.knowledge_graph.unguarded_writes || [];
          const match = unguarded.find(u => u.id === d.id);
          if (match) {
            html += '<div class="kg-section"><b>-- Finding Detail --</b></div>';
            html += `<div><b>Subtype:</b> ${escapeHtml(match.subtype || '?')}</div>`;
            html += `<div><b>Severity:</b> ${escapeHtml(match.severity || '?')}</div>`;
            if (match.line) html += `<div><b>Line:</b> ${match.line}</div>`;
          }
        }

        // Fix suggestions
        const fixes = (lastData.fixes || []).filter(f => f.finding_id === d.id);
        if (fixes.length > 0) {
          html += '<div class="kg-section"><b>-- Fix Suggestions --</b></div>';
          fixes.forEach((fix, i) => {
            const badge = fix.validated
              ? (fix.validation_result && fix.validation_result.includes('removes')
                ? '<span style="color:#81c784"> [VERIFIED]</span>'
                : '<span style="color:#ffb74d"> [checked]</span>')
              : '';
            html += `<div class="fix-item">`;
            html += `<div><b>${fix.strategy}</b> (${(fix.confidence * 100).toFixed(0)}%)${badge}</div>`;
            html += `<div style="font-size:11px;color:#aaa">${escapeHtml(fix.description)}</div>`;
            if (fix.diff) {
              html += `<pre class="diff-block">${escapeHtml(fix.diff)}</pre>`;
            }
            // Apply Fix button
            html += `<button class="apply-fix-btn" data-fix-index="${i}" data-finding-id="${d.id}">⚡ Apply Fix</button>`;
            html += `</div>`;
          });
        }

        // LLM analysis (from Phase 5 agent validation)
        const agentResults = lastData.agent_results || {};
        const agentFindings = (agentResults.results || []);
        if (agentFindings.length > 0) {
          // Try to match by variable name from the finding
          const varName = d.id.replace(/^finding:/, '').split('_')[0];
          const matched = agentFindings.find(r => {
            const rv = (r.finding || {}).variable || '';
            return rv === varName || d.id.includes(rv);
          });
          if (matched) {
            const analysis = (matched.analyst || {}).analysis || {};
            const source = (matched.analyst || {}).source || '?';
            html += '<div class="kg-section"><b>-- LLM Analysis --</b></div>';
            html += `<div><b>Source:</b> <span style="color:${source === 'llm' ? '#81c784' : '#ffb74d'}">${source}</span></div>`;
            if (analysis.is_real_race !== undefined) {
              html += `<div><b>Real race?</b> ${analysis.is_real_race ? '⚠️ YES' : '✅ No'}</div>`;
            }
            if (analysis.severity) html += `<div><b>Severity:</b> ${analysis.severity}</div>`;
            if (analysis.confidence) html += `<div><b>Confidence:</b> ${analysis.confidence}%</div>`;
            if (analysis.root_cause) html += `<div><b>Root cause:</b> <span style="font-size:11px;color:#ccc">${escapeHtml(analysis.root_cause)}</span></div>`;
            if (analysis.recommended_fix) html += `<div><b>Recommended:</b> <span style="font-size:11px;color:#90caf9">${escapeHtml(analysis.recommended_fix)}</span></div>`;
          }
        }
      }

      detailBody.innerHTML = html;
      show(detailPanel);

      // Wire up Apply Fix buttons
      detailBody.querySelectorAll('.apply-fix-btn').forEach(btn => {
        btn.addEventListener('click', (e) => {
          const findingId = btn.getAttribute('data-finding-id');
          const fixIndex = parseInt(btn.getAttribute('data-fix-index'), 10);
          const fixes = (lastData.fixes || []).filter(f => f.finding_id === findingId);
          const fix = fixes[fixIndex];
          if (fix && vscode) {
            vscode.postMessage({
              command: 'applyFix',
              fix: fix,
            });
            btn.textContent = '✅ Applied!';
            btn.disabled = true;
            btn.classList.add('applied');
          }
        });
      });
    });

    // ── Tap background → reset ──
    cy.on('tap', (evt) => {
      if (evt.target === cy) {
        cy.elements().removeClass('highlighted faded');
        hide(detailPanel);
      }
    });
  }

  // ── Render Problems & Fixes Sidebar ──
  function renderSidebar(data) {
    const sidebar = document.getElementById('problemsSidebar');
    if (!sidebar) return;

    const list = document.getElementById('problemsList');
    const countBadge = document.getElementById('problemCount');

    const fixes = data.fixes || [];
    const kg = data.knowledge_graph || {};
    const unguarded = kg.unguarded_writes || [];
    const varSummaries = kg.variable_summaries || [];
    const agentResults = data.agent_results || {};
    const agentFindings = agentResults.results || [];

    // If no findings at all, show "all clear"
    if (fixes.length === 0 && unguarded.length === 0) {
      countBadge.textContent = '0 issues';
      countBadge.title = '0 fixable';
      list.innerHTML = '<div style="color:#81c784;padding:24px;text-align:center;font-size:13px;">✅ No concurrency issues detected!<br><span style="color:#888;font-size:11px">Your code looks safe.</span></div>';
      show(sidebar);
      if (!sidebarInitialized) {
        setSidebarOpen(true);
        sidebarInitialized = true;
      }
      return;
    }

    show(sidebar);
    if (!sidebarInitialized) {
      setSidebarOpen(true);
      sidebarInitialized = true;
    }

    // Build a lookup: finding_id -> KG info (line, severity, variable, subtype)
    const kgLookup = {};
    unguarded.forEach(u => {
      kgLookup[u.id] = u;
    });

    // Build a lookup: variable -> KG variable summary
    const varLookup = {};
    varSummaries.forEach(vs => {
      varLookup[vs.variable] = vs;
    });

    // Build agent analysis lookup: variable -> analysis
    const agentLookup = {};
    agentFindings.forEach(r => {
      const v = (r.finding || {}).variable || '';
      if (v) agentLookup[v] = (r.analyst || {}).analysis || {};
    });

    // Build LLM fix analysis lookup: variable -> {root_cause, strategy, reasoning}
    const llmFixLookup = {};
    (data.llm_fix_analysis || []).forEach(a => {
      const v = a.variable || '';
      if (v) llmFixLookup[v] = a;
    });

    // Separate global LLM fixes from variable-specific fixes
    const globalFixes = [];
    const varFixes = [];
    fixes.forEach((fix, index) => {
      if (fix.finding_id && fix.finding_id.startsWith('finding:llm_')) {
        globalFixes.push({ ...fix, _originalIndex: index });
      } else {
        varFixes.push({ ...fix, _originalIndex: index });
      }
    });

    const problems = new Map();

    // 1. Add all unguarded writes (actual concurrency issues)
    unguarded.forEach(u => {
      const varName = u.variable || 'unknown';
      problems.set(u.id, {
        id: u.id,
        variable: varName,
        line: u.line,
        severity: u.severity || 'medium',
        subtype: u.subtype || 'unprotected_access',
        agentAnalysis: agentLookup[varName] || {},
        llmFixAnalysis: llmFixLookup[varName] || {},
        fixes: [],
      });
    });

    // 2. Add rule-based specific fixes to their corresponding problems
    varFixes.forEach(fix => {
      const fid = fix.finding_id;
      if (!problems.has(fid)) {
        const kgInfo = kgLookup[fid] || {};
        const varName = kgInfo.variable || fid.split('_')[0].replace('finding:', '');
        problems.set(fid, {
          id: fid,
          variable: varName,
          line: kgInfo.line || null,
          severity: kgInfo.severity || 'medium',
          subtype: kgInfo.subtype || 'unprotected_access',
          agentAnalysis: agentLookup[varName] || {},
          llmFixAnalysis: llmFixLookup[varName] || {},
          fixes: [],
        });
      }
      problems.get(fid).fixes.push(fix);
    });

    const totalIssues = problems.size;
    const fixableCount = [...problems.values()].filter(p => p.fixes.length > 0).length;

    countBadge.textContent = `${totalIssues} issues`;
    countBadge.title = `${totalIssues} issues`;

    // Build HTML
    let html = '';

    // "Fix All" button at the top using global fixes
    const fullFix = globalFixes.find(f => f.strategy === 'llm_full_file' || f.full_file_content);
    const pragmaFix = globalFixes.find(f => f.strategy === 'llm_pragma_clause');
    const combinedFix = globalFixes.find(f => f.strategy === 'llm_combined');
    const topFix = pragmaFix || combinedFix || fullFix;

    if (topFix) {
      html += `<div style="padding:8px 0 12px">`;
      html += `<div style="font-size:12px;color:#81c784;margin-bottom:6px;"><b>💡 LLM Recommended Fix</b></div>`;
      html += `<button id="applyFullFixBtn" class="apply-fix-btn" style="width:100%;padding:10px;font-size:14px;font-weight:bold;">⚡ Apply ${topFix.strategy === 'llm_pragma_clause' ? 'Pragma' : 'Full'} Fix</button>`;
      html += `</div>`;
    } else {
      html += `<div style="padding:8px 0 12px">`;
      html += `<button id="retryFullFixBtn" class="apply-fix-btn" style="width:100%;padding:10px;font-size:14px;font-weight:bold;">↻ Retry LLM Full Fix</button>`;
      html += `</div>`;
    }

    // Severity sort: high first
    const sevOrder = { high: 0, medium: 1, low: 2 };
    const sorted = [...problems.values()].sort((a, b) => (sevOrder[a.severity] || 1) - (sevOrder[b.severity] || 1));

    sorted.forEach((prob) => {
      const sevColor = prob.severity === 'high' ? '#e57373' : prob.severity === 'medium' ? '#ffb74d' : '#81c784';
      const sevLabel = prob.severity ? prob.severity.toUpperCase() : 'MEDIUM';

      html += `<div class="problem-card">`;

      // Header with severity badge
      html += `<div class="problem-card-header">`;
      html += `<span style="background:${sevColor};color:#000;padding:1px 6px;border-radius:3px;font-size:10px;font-weight:bold;margin-right:6px;">${sevLabel}</span>`;
      html += `Unprotected access to <code style="color:#ffb74d;background:#333;padding:1px 4px;border-radius:2px;">${escapeHtml(prob.variable)}</code>`;
      if (prob.line) html += ` <span style="color:#888;font-size:11px;">(Line ${prob.line})</span>`;
      html += `</div>`;

      // Beginner-friendly explanation (prioritize LLM fix analysis > agent > heuristic)
      const llmFix = prob.llmFixAnalysis || {};
      const agent = prob.agentAnalysis || {};

      if (llmFix.root_cause) {
        html += `<div class="problem-desc"><b>Why:</b> ${escapeHtml(llmFix.root_cause)}</div>`;
        if (llmFix.strategy) {
          html += `<div style="font-size:11px;color:#90caf9;margin:4px 0;"><b>LLM Strategy:</b> <code>${escapeHtml(llmFix.strategy)}</code><br/>${escapeHtml(llmFix.reasoning || '')}</div>`;
        }
      } else if (agent.root_cause) {
        html += `<div class="problem-desc"><b>Why:</b> ${escapeHtml(agent.root_cause)}</div>`;
      } else {
        html += `<div class="problem-desc"><b>Why:</b> The variable <code>${escapeHtml(prob.variable)}</code> is accessed by multiple threads at line ${prob.line || '?'} without any synchronization (lock/critical section). This can cause unpredictable values.</div>`;
      }

      // Fix area
      if (prob.fixes.length > 0) {
        const bestFix = prob.fixes[0];
        html += `<div class="problem-fix-area">`;
        html += `<div class="fix-title">Rule-based fix: ${escapeHtml(bestFix.strategy)}</div>`;
        html += `<div style="font-size:11px;color:#aaa;">${escapeHtml(bestFix.description)}</div>`;
        html += `</div>`;
      } else {
        // No auto-fix available
        if (agent.recommended_fix) {
          html += `<div class="problem-fix-area">`;
          html += `<div class="fix-title">Recommendation</div>`;
          html += `<div style="font-size:11px;color:#aaa;">${escapeHtml(agent.recommended_fix)}</div>`;
          html += `</div>`;
        }
      }

      html += `</div>`;
    });

    list.innerHTML = html;

    // Wire up the single Apply All Fixes button
    const applyFullFixBtn = document.getElementById('applyFullFixBtn');
    if (applyFullFixBtn) {
      applyFullFixBtn.addEventListener('click', () => {
        const fullFixNow = (lastData.fixes || []).find(f => f.full_file_content);
        if (fullFixNow && vscode) {
          vscode.postMessage({
            command: 'applyFullFix',
            filePath: fullFixNow.file_path,
            content: fullFixNow.full_file_content,
          });
          applyFullFixBtn.textContent = '✅ Applied full fix';
          applyFullFixBtn.disabled = true;
          applyFullFixBtn.style.background = '#2e7d32';

          setTimeout(() => {
            if (activePath && vscode) {
              vscode.postMessage({ command: 'analyzeFile', path: activePath, quick: true });
            }
          }, 2500);
        }
      });
    }

    const retryFullFixBtn = document.getElementById('retryFullFixBtn');
    if (retryFullFixBtn) {
      retryFullFixBtn.addEventListener('click', () => {
        retryFullFixBtn.textContent = '⏳ Retrying LLM...';
        retryFullFixBtn.disabled = true;
        if (activePath && vscode) {
          vscode.postMessage({ command: 'analyzeFile', path: activePath });
        }
      });
    }
  }

})();
