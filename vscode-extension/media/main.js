/* ── Concurrency Analyzer · Webview Script (D3.js renderer) ────────── */
(() => {
  const vscode = typeof acquireVsCodeApi === 'function' ? acquireVsCodeApi() : null;

  // ── DOM refs ──
  const analyzeBtn      = document.getElementById('analyzeBtn');
  const fileLabel       = document.getElementById('fileLabel');
  const spinnerWrap     = document.getElementById('spinnerWrap');
  const summaryBar      = document.getElementById('summaryBar');
  const graphDiv        = document.getElementById('graph');
  const detailPanel     = document.getElementById('detailPanel');
  const detailTitle     = document.getElementById('detailTitle');
  const detailBody      = document.getElementById('detailBody');
  const sidebar         = document.getElementById('problemsSidebar');
  const sidebarToggleBtn = document.getElementById('sidebarToggleBtn');
  const sidebarResizer  = document.getElementById('sidebarResizer');

  let activePath        = null;
  let cy                = null;   // holds { _sim } for D3 simulation cleanup
  let lastData          = null;
  let sidebarInitialized = false;
  let isSidebarOpen     = false;
  const MIN_SIDEBAR_WIDTH = 260;
  const MAX_SIDEBAR_WIDTH = 640;

  // ── Helpers ──
  function show(el) { el.classList.remove('hidden'); }
  function hide(el) { el.classList.add('hidden'); }
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

  setSidebarWidth(380);
  if (sidebarToggleBtn) {
    sidebarToggleBtn.addEventListener('click', () => {
      if (sidebar && sidebar.classList.contains('hidden')) return;
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
      function onMove(evt) { const delta = startX - evt.clientX; setSidebarWidth(startWidth + delta); }
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
    if (!activePath) { showError('No file is open. Open a source file first.'); return; }
    if (vscode) vscode.postMessage({ command: 'analyzeFile', path: activePath });
  });

  // ── Deep Analyze (LLM) button ──
  const deepAnalyzeBtn = document.getElementById('deepAnalyzeBtn');
  if (deepAnalyzeBtn) {
    deepAnalyzeBtn.addEventListener('click', () => {
      if (!activePath) { showError('No file is open. Open a source file first.'); return; }
      if (vscode) vscode.postMessage({ command: 'analyzeFile', path: activePath, useLLM: true });
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

    const elements = data.elements || [];
    if (elements.length === 0) {
      graphDiv.innerHTML = '<p class="empty-msg">No graph elements produced. The file may not contain concurrency constructs.</p>';
      return;
    }

    buildGraph(elements);

    try { renderSidebar(data); } catch (err) {
      const list = document.getElementById('problemsList');
      if (sidebar && list) {
        sidebar.classList.remove('hidden');
        list.innerHTML = `<div style="color:#e57373;padding:16px;">Sidebar error: ${String(err)}</div>`;
      }
    }
  }

  function tag(label, value) {
    return `<span class="summary-tag">${label}: <b>${value ?? '–'}</b></span>`;
  }

  // ══════════════════════════════════════════════════════════════════════
  //  D3.js Thread Interaction Graph
  // ══════════════════════════════════════════════════════════════════════
  function buildGraph(elements) {
    // Stop previous simulation
    if (cy && cy._sim) { try { cy._sim.stop(); } catch(e) {} }
    cy = null;
    graphDiv.innerHTML = '';

    // ── Parse Cytoscape-format elements ──────────────────────────────
    const nodeData = elements
      .filter(e => e.data && !e.data.source)
      .map(e => ({ ...e.data }));

    const linkData = elements
      .filter(e => e.data && e.data.source)
      .map(e => ({ ...e.data }));

    if (!nodeData.length) {
      graphDiv.innerHTML = '<p class="empty-msg">No graph elements produced.</p>';
      return;
    }

    // Validate links reference existing nodes
    const nodeById = new Map(nodeData.map(n => [n.id, n]));
    const links = linkData.filter(l => nodeById.has(l.source) && nodeById.has(l.target));

    const W = graphDiv.clientWidth  || 900;
    const H = graphDiv.clientHeight || 600;

    // ── SVG root ─────────────────────────────────────────────────────
    const svg = d3.select(graphDiv)
      .append('svg')
      .attr('width', '100%')
      .attr('height', '100%')
      .style('cursor', 'grab');

    // ── Defs: grid, glow filters, arrow markers ───────────────────────
    const defs = svg.append('defs');

    // Dot grid background pattern
    const gridPat = defs.append('pattern')
      .attr('id', 'tig-grid')
      .attr('width', 30).attr('height', 30)
      .attr('patternUnits', 'userSpaceOnUse');
    gridPat.append('circle')
      .attr('cx', 15).attr('cy', 15).attr('r', 1)
      .attr('fill', 'rgba(255,255,255,0.055)');

    // Glow filters per node type
    [
      { id: 'gf-thread',  rgb: [0.30, 0.76, 0.97], std: 5  },
      { id: 'gf-vbad',    rgb: [1.00, 0.44, 0.26],  std: 7  },
      { id: 'gf-vok',     rgb: [0.40, 0.73, 0.40],  std: 5  },
      { id: 'gf-sync',    rgb: [0.00, 0.90, 1.00],  std: 6  },
      { id: 'gf-finding', rgb: [0.94, 0.32, 0.31],  std: 9  },
      { id: 'gf-file',    rgb: [0.70, 0.62, 0.85],  std: 4  },
    ].forEach(({ id, rgb, std }) => {
      const f = defs.append('filter')
        .attr('id', id)
        .attr('x', '-70%').attr('y', '-70%')
        .attr('width', '240%').attr('height', '240%');
      f.append('feGaussianBlur')
        .attr('in', 'SourceGraphic').attr('stdDeviation', std).attr('result', 'b');
      f.append('feColorMatrix').attr('in', 'b').attr('type', 'matrix')
        .attr('values', `0 0 0 0 ${rgb[0]}  0 0 0 0 ${rgb[1]}  0 0 0 0 ${rgb[2]}  0 0 0 0.9 0`)
        .attr('result', 'c');
      const m = f.append('feMerge');
      m.append('feMergeNode').attr('in', 'c');
      m.append('feMergeNode').attr('in', 'SourceGraphic');
    });

    // Arrow markers — one per edge type
    [
      { id: 'am-access',   color: '#ffb74d' },
      { id: 'am-acquires', color: '#66bb6a' },
      { id: 'am-sync',     color: '#00bcd4' },
      { id: 'am-spawns',   color: '#4fc3f7' },
      { id: 'am-contains', color: '#9575cd' },
      { id: 'am-issue',    color: '#ef5350' },
      { id: 'am-protect',  color: '#4caf50' },
      { id: 'am-default',  color: '#777'    },
    ].forEach(({ id, color }) => {
      defs.append('marker')
        .attr('id', id)
        .attr('markerWidth', 8).attr('markerHeight', 8)
        .attr('refX', 6).attr('refY', 3)
        .attr('orient', 'auto')
        .append('path').attr('d', 'M0,0 L6,3 L0,6 z')
        .attr('fill', color).attr('opacity', 0.88);
    });

    // ── Background layers ─────────────────────────────────────────────
    svg.append('rect').attr('width', '100%').attr('height', '100%').attr('fill', '#0d1117');
    svg.append('rect').attr('width', '100%').attr('height', '100%').attr('fill', 'url(#tig-grid)');

    // ── Zoom-able root group ──────────────────────────────────────────
    const root = svg.append('g').attr('class', 'tig-root');
    const zoom = d3.zoom()
      .scaleExtent([0.06, 7])
      .on('zoom', evt => { root.attr('transform', evt.transform); svg.style('cursor', 'grabbing'); })
      .on('end',  ()   => svg.style('cursor', 'grab'));
    svg.call(zoom);

    // ── Edge visual config ────────────────────────────────────────────
    const ECfg = {
      may_access:        { color: '#ffb74d', arrow: 'am-access',   dash: null,   w: 1.5, anim: false },
      acquires:          { color: '#66bb6a', arrow: 'am-acquires',  dash: null,   w: 2.0, anim: false },
      synchronized_with: { color: '#00bcd4', arrow: 'am-sync',      dash: '6 3',  w: 1.5, anim: false },
      spawns:            { color: '#4fc3f7', arrow: 'am-spawns',    dash: null,   w: 2.0, anim: false },
      contains:          { color: '#9575cd', arrow: 'am-contains',  dash: '3 5',  w: 1.0, anim: false },
      detected_issue:    { color: '#ef5350', arrow: 'am-issue',     dash: '8 4',  w: 2.5, anim: true  },
      protected_by:      { color: '#4caf50', arrow: 'am-protect',   dash: '5 3',  w: 1.5, anim: false },
    };
    const ec = t => ECfg[t] || { color: '#666', arrow: 'am-default', dash: null, w: 1, anim: false };

    // ── Draw edges ────────────────────────────────────────────────────
    const edgeG = root.append('g').attr('class', 'tig-edges');

    const edgeLine = edgeG.selectAll('line')
      .data(links).enter().append('line')
      .attr('stroke',           d => ec(d.type).color)
      .attr('stroke-width',     d => ec(d.type).w)
      .attr('stroke-dasharray', d => ec(d.type).dash || null)
      .attr('stroke-opacity',   d => ec(d.type).anim ? 0.75 : 0.5)
      .attr('marker-end',       d => `url(#${ec(d.type).arrow})`)
      .attr('class',            d => ec(d.type).anim ? 'tig-edge-anim' : '');

    const edgeLabel = edgeG.selectAll('text.tig-edge-label')
      .data(links).enter().append('text')
      .attr('class', 'tig-edge-label')
      .attr('text-anchor', 'middle')
      .attr('font-size', '7.5px')
      .attr('fill', 'rgba(160,160,160,0.45)')
      .attr('font-family', '"Segoe UI", system-ui, sans-serif')
      .attr('pointer-events', 'none')
      .text(d => d.label || '');

    // ── Node radius for arrow offset / collision ──────────────────────
    function nodeR(type) {
      if (type === 'thread')  return 30;
      if (type === 'sync')    return 28;
      if (type === 'finding') return 28;
      return 22;  // variable, file
    }

    // ── Draw nodes ────────────────────────────────────────────────────
    const nodeG = root.append('g').attr('class', 'tig-nodes');

    const nodeEl = nodeG.selectAll('g.tig-node')
      .data(nodeData).enter()
      .append('g')
      .attr('class', d => `tig-node tig-node-${d.type || 'file'}`)
      .style('cursor', 'pointer')
      .call(d3.drag()
        .on('start', (evt, d) => {
          if (!evt.active) sim.alphaTarget(0.3).restart();
          d.fx = d.x; d.fy = d.y;
          svg.style('cursor', 'grabbing');
        })
        .on('drag', (evt, d) => { d.fx = evt.x; d.fy = evt.y; })
        .on('end',  (evt, d) => {
          if (!evt.active) sim.alphaTarget(0);
          // Keep d.fx / d.fy pinned at the drop position.
          // The node will stay exactly where the user placed it.
          // Only the ⊙ reset button clears all pins.
          svg.style('cursor', 'grab');
        })
      )
      .on('click', (evt, d) => {
        evt.stopPropagation();
        highlightNeighborhood(d.id);
        showDetail(d);
      })
      .on('mouseover', (evt, d) => showTip(evt, d.label || d.id))
      .on('mouseout',  ()       => hideTip());

    // Render shapes inside each node group
    nodeEl.each(function(d) {
      const g    = d3.select(this);
      const type = d.type || 'file';

      if (type === 'thread') {
        // Outer ambient ring
        g.append('circle').attr('r', 36)
          .attr('fill', 'none')
          .attr('stroke', '#4fc3f7').attr('stroke-width', 0.75)
          .attr('opacity', 0.18);
        // Inner accent ring
        g.append('circle').attr('r', 18)
          .attr('fill', 'none')
          .attr('stroke', 'rgba(79,195,247,0.22)').attr('stroke-width', 1);
        // Main circle
        g.append('circle').attr('r', 26)
          .attr('fill', '#04192e')
          .attr('stroke', '#4fc3f7').attr('stroke-width', 2)
          .attr('filter', 'url(#gf-thread)');

      } else if (type === 'variable') {
        const isUnsafe = d.safe !== 'true';
        const stroke = isUnsafe ? '#ff7043' : '#66bb6a';
        const fill   = isUnsafe ? '#1c0808' : '#041a09';
        const glow   = isUnsafe ? 'gf-vbad'  : 'gf-vok';

        const lines  = (d.label || '').split(/\\n|\n/);
        const maxLen = Math.max(...lines.map(l => l.length));
        const w = Math.max(68, maxLen * 8.5 + 24);
        const h = lines.length > 1 ? 44 : 30;
        d._bw = w; d._bh = h;

        if (isUnsafe) {
          g.append('rect').attr('class', 'tig-pulse-halo')
            .attr('x', -w / 2 - 9).attr('y', -h / 2 - 9)
            .attr('width', w + 18).attr('height', h + 18)
            .attr('rx', 12).attr('ry', 12)
            .attr('fill', 'none')
            .attr('stroke', '#ff5722').attr('stroke-width', 2)
            .attr('opacity', 0.3);
        }
        g.append('rect')
          .attr('x', -w / 2).attr('y', -h / 2)
          .attr('width', w).attr('height', h)
          .attr('rx', 6).attr('ry', 6)
          .attr('fill', fill)
          .attr('stroke', stroke).attr('stroke-width', 2)
          .attr('filter', `url(#${glow})`);

      } else if (type === 'sync') {
        const s = 22;
        // Outer glow ring (diamond orientation)
        g.append('rect')
          .attr('x', -(s + 9)).attr('y', -(s + 9))
          .attr('width', (s + 9) * 2).attr('height', (s + 9) * 2)
          .attr('rx', 2).attr('transform', 'rotate(45)')
          .attr('fill', 'none')
          .attr('stroke', 'rgba(0,229,255,0.18)').attr('stroke-width', 1);
        // Main diamond
        g.append('rect')
          .attr('x', -s).attr('y', -s)
          .attr('width', s * 2).attr('height', s * 2)
          .attr('rx', 3).attr('transform', 'rotate(45)')
          .attr('fill', '#020e18')
          .attr('stroke', '#00e5ff').attr('stroke-width', 2)
          .attr('filter', 'url(#gf-sync)');

      } else if (type === 'finding') {
        // Pulsing halo
        g.append('polygon').attr('class', 'tig-pulse-halo')
          .attr('points', '0,-40 33,22 -33,22')
          .attr('fill', 'none')
          .attr('stroke', '#ef5350').attr('stroke-width', 2)
          .attr('opacity', 0.25);
        // Main triangle
        g.append('polygon')
          .attr('points', '0,-28 24,16 -24,16')
          .attr('fill', '#1c0505')
          .attr('stroke', '#ef5350').attr('stroke-width', 2.5)
          .attr('filter', 'url(#gf-finding)');
        // Warning symbol
        g.append('text')
          .attr('text-anchor', 'middle').attr('y', 8)
          .attr('font-size', '13px').attr('font-weight', 'bold')
          .attr('fill', '#ef5350').attr('pointer-events', 'none')
          .text('!');

      } else { // file
        const txt = d.label || '';
        const w   = Math.max(82, txt.length * 6.5 + 30);
        const h   = 26;
        d._bw = w; d._bh = h;
        g.append('rect')
          .attr('x', -w / 2).attr('y', -h / 2)
          .attr('width', w).attr('height', h)
          .attr('rx', 13).attr('ry', 13)
          .attr('fill', '#0e0820')
          .attr('stroke', '#b39ddb').attr('stroke-width', 1.5)
          .attr('filter', 'url(#gf-file)')
          .attr('opacity', 0.88);
      }

      // ── Label text (all node types except finding) ──
      if (type !== 'finding') {
        const textColor = type === 'thread'   ? '#9ddeff'
                        : type === 'sync'     ? '#88f8ff'
                        : type === 'file'     ? '#d8c8f4'
                        : d.safe !== 'true'   ? '#ffc4ad'
                        : '#bdf0cc';

        const raw   = d.label || d.id.split(':').slice(1).join(':') || d.id;
        const lines = raw.split(/\\n|\n/);

        const textEl = g.append('text')
          .attr('text-anchor', 'middle')
          .attr('font-size', type === 'file' ? '9px' : '10px')
          .attr('font-weight', '600')
          .attr('font-family', '"Segoe UI", system-ui, sans-serif')
          .attr('fill', textColor)
          .attr('pointer-events', 'none');

        if (lines.length > 1) {
          lines.forEach((line, i) => {
            textEl.append('tspan')
              .attr('x', 0)
              .attr('dy', i === 0 ? `${-(lines.length - 1) * 0.55}em` : '1.15em')
              .attr('font-size', i === 0 ? '10px' : '8px')
              .attr('fill',      i === 0 ? textColor : 'rgba(150,210,150,0.75)')
              .text(line);
          });
        } else {
          textEl.attr('dominant-baseline', 'central').text(lines[0]);
        }
      }
    });

    // ── Force simulation ──────────────────────────────────────────────
    const sim = d3.forceSimulation(nodeData)
      .force('link',    d3.forceLink(links).id(d => d.id).distance(145).strength(0.32))
      .force('charge',  d3.forceManyBody().strength(-650).distanceMin(40))
      .force('center',  d3.forceCenter(W / 2, H / 2))
      .force('collide', d3.forceCollide().radius(d => nodeR(d.type) + 30).strength(0.88))
      .force('x',       d3.forceX(W / 2).strength(0.04))
      .force('y',       d3.forceY(H / 2).strength(0.04))
      .alphaDecay(0.014)
      .on('tick', tick);

    cy = { _sim: sim };

    // ── Zoom control buttons ──────────────────────────────────────────
    const zoomInBtn  = document.getElementById('zoomInBtn');
    const zoomOutBtn = document.getElementById('zoomOutBtn');
    const zoomFitBtn = document.getElementById('zoomFitBtn');

    if (zoomInBtn)  zoomInBtn.onclick  = () => svg.transition().duration(260).call(zoom.scaleBy, 1.45);
    if (zoomOutBtn) zoomOutBtn.onclick = () => svg.transition().duration(260).call(zoom.scaleBy, 1 / 1.45);
    if (zoomFitBtn) zoomFitBtn.onclick = () => {
      if (!nodeData.length) return;
      // ── Reset: unpin all dragged nodes and let simulation re-settle ──
      nodeData.forEach(d => { d.fx = null; d.fy = null; });
      sim.alpha(0.45).alphaTarget(0).restart();

      // After the simulation has settled (~900 ms), fit the view
      setTimeout(() => {
        const xs = nodeData.map(d => d.x || 0);
        const ys = nodeData.map(d => d.y || 0);
        const pad = 80;
        const x0 = Math.min(...xs) - pad, y0 = Math.min(...ys) - pad;
        const x1 = Math.max(...xs) + pad, y1 = Math.max(...ys) + pad;
        const bW = x1 - x0 || 1, bH = y1 - y0 || 1;
        const fullW = graphDiv.clientWidth  || W;
        const fullH = graphDiv.clientHeight || H;
        const scale = Math.min(fullW / bW, fullH / bH, 3) * 0.88;
        const tx    = fullW / 2 - scale * (x0 + bW / 2);
        const ty    = fullH / 2 - scale * (y0 + bH / 2);
        svg.transition().duration(450).call(zoom.transform, d3.zoomIdentity.translate(tx, ty).scale(scale));
      }, 900);
    };

    function tick() {
      edgeLine
        .attr('x1', d => d.source.x)
        .attr('y1', d => d.source.y)
        .attr('x2', d => {
          const dx = d.target.x - d.source.x, dy = d.target.y - d.source.y;
          const len = Math.sqrt(dx * dx + dy * dy) || 1;
          return d.target.x - (dx / len) * (nodeR(d.target.type) + 2);
        })
        .attr('y2', d => {
          const dx = d.target.x - d.source.x, dy = d.target.y - d.source.y;
          const len = Math.sqrt(dx * dx + dy * dy) || 1;
          return d.target.y - (dy / len) * (nodeR(d.target.type) + 2);
        });

      edgeLabel
        .attr('x', d => (d.source.x + d.target.x) / 2)
        .attr('y', d => (d.source.y + d.target.y) / 2 - 5);

      nodeEl.attr('transform', d => `translate(${d.x},${d.y})`);
    }

    // ── Highlight neighborhood ────────────────────────────────────────
    function highlightNeighborhood(nodeId) {
      const conn = new Set([nodeId]);
      links.forEach(l => {
        const sid = l.source.id || l.source;
        const tid = l.target.id || l.target;
        if (sid === nodeId) conn.add(tid);
        if (tid === nodeId) conn.add(sid);
      });
      nodeEl.style('opacity',       n => conn.has(n.id) ? 1 : 0.1);
      edgeLine.style('opacity',     l => {
        const s = l.source.id || l.source, t = l.target.id || l.target;
        return conn.has(s) && conn.has(t) ? 0.9 : 0.04;
      });
      edgeLabel.style('opacity',    l => {
        const s = l.source.id || l.source, t = l.target.id || l.target;
        return conn.has(s) && conn.has(t) ? 0.7 : 0;
      });
    }

    // Reset on background click
    svg.on('click', () => {
      nodeEl.style('opacity', 1);
      edgeLine.style('opacity', d => ec(d.type).anim ? 0.75 : 0.5);
      edgeLabel.style('opacity', 1);
      hide(detailPanel);
    });

    // ── Floating tooltip ─────────────────────────────────────────────
    const tip = d3.select(graphDiv).append('div').attr('class', 'tig-tooltip hidden');
    function showTip(evt, text) {
      tip.classed('hidden', false)
        .style('left', (evt.offsetX + 14) + 'px')
        .style('top',  (evt.offsetY - 14) + 'px')
        .text(text);
    }
    function hideTip() { tip.classed('hidden', true); }

    // ── Detail panel (same logic as before) ──────────────────────────
    function showDetail(d) {
      detailTitle.textContent = d.label || d.id;

      const skipKeys = new Set(['id','label','color','shape','_bw','_bh','x','y','vx','vy','index','fx','fy']);
      let html = Object.entries(d)
        .filter(([k]) => !skipKeys.has(k))
        .map(([k, v]) => `<div><b>${k}:</b> ${escapeHtml(String(v))}</div>`)
        .join('');

      // KG queries for variable nodes
      if (d.type === 'variable' && lastData && lastData.knowledge_graph) {
        const varName = (d.label || '').split(/\\n|\n/)[0] || d.id.replace('var:', '');
        const summaries = lastData.knowledge_graph.variable_summaries || [];
        const match = summaries.find(s => s.variable === varName);
        if (match) {
          html += '<div class="kg-section"><b>── KG Queries ──</b></div>';
          html += `<div><b>Threads:</b> ${match.threads.length ? match.threads.join(', ') : 'none'}</div>`;
          html += `<div><b>Locks:</b> ${match.locks.length ? match.locks.join(', ') : 'none'}</div>`;
          html += `<div><b>Protected:</b> ${match.is_protected ? '✅ Yes' : '<span style="color:#e57373">⚠ No</span>'}</div>`;
          html += `<div><b>Findings:</b> ${match.finding_count}</div>`;
          if (match.findings && match.findings.length) {
            match.findings.forEach(f => {
              html += `<div style="margin-left:8px;color:#e57373">- ${escapeHtml(f.subtype || f.id)}</div>`;
            });
          }
        }
      }

      // Finding node: details + fixes + LLM
      if (d.type === 'finding' && lastData) {
        if (lastData.knowledge_graph) {
          const unguarded = lastData.knowledge_graph.unguarded_writes || [];
          const match = unguarded.find(u => u.id === d.id);
          if (match) {
            html += '<div class="kg-section"><b>── Finding Detail ──</b></div>';
            html += `<div><b>Subtype:</b> ${escapeHtml(match.subtype || '?')}</div>`;
            html += `<div><b>Severity:</b> ${escapeHtml(match.severity || '?')}</div>`;
            if (match.line) html += `<div><b>Line:</b> ${match.line}</div>`;
          }
        }
        const fixes = (lastData.fixes || []).filter(f => f.finding_id === d.id);
        if (fixes.length) {
          html += '<div class="kg-section"><b>── Fix Suggestions ──</b></div>';
          fixes.forEach((fix, i) => {
            const badge = fix.validated
              ? (fix.validation_result && fix.validation_result.includes('removes')
                ? '<span style="color:#81c784"> [VERIFIED]</span>'
                : '<span style="color:#ffb74d"> [checked]</span>')
              : '';
            html += `<div class="fix-item">
              <div><b>${fix.strategy}</b> (${(fix.confidence * 100).toFixed(0)}%)${badge}</div>
              <div style="font-size:11px;color:#aaa">${escapeHtml(fix.description)}</div>
              ${fix.diff ? `<pre class="diff-block">${escapeHtml(fix.diff)}</pre>` : ''}
              <button class="apply-fix-btn" data-fix-index="${i}" data-finding-id="${d.id}">⚡ Apply Fix</button>
            </div>`;
          });
        }
        // LLM analysis
        const agentFindings = ((lastData.agent_results || {}).results || []);
        if (agentFindings.length) {
          const varName = d.id.replace(/^finding:/, '').split('_')[0];
          const matched = agentFindings.find(r => {
            const rv = (r.finding || {}).variable || '';
            return rv === varName || d.id.includes(rv);
          });
          if (matched) {
            const analysis = (matched.analyst || {}).analysis || {};
            const source   = (matched.analyst || {}).source   || '?';
            html += '<div class="kg-section"><b>── LLM Analysis ──</b></div>';
            html += `<div><b>Source:</b> <span style="color:${source === 'llm' ? '#81c784' : '#ffb74d'}">${source}</span></div>`;
            if (analysis.is_real_race !== undefined) html += `<div><b>Real race?</b> ${analysis.is_real_race ? '⚠️ YES' : '✅ No'}</div>`;
            if (analysis.severity)       html += `<div><b>Severity:</b> ${analysis.severity}</div>`;
            if (analysis.confidence)     html += `<div><b>Confidence:</b> ${analysis.confidence}%</div>`;
            if (analysis.root_cause)     html += `<div><b>Root cause:</b> <span style="font-size:11px;color:#ccc">${escapeHtml(analysis.root_cause)}</span></div>`;
            if (analysis.recommended_fix) html += `<div><b>Recommended:</b> <span style="font-size:11px;color:#90caf9">${escapeHtml(analysis.recommended_fix)}</span></div>`;
          }
        }
      }

      detailBody.innerHTML = html;
      show(detailPanel);

      // Wire up Apply Fix buttons
      detailBody.querySelectorAll('.apply-fix-btn').forEach(btn => {
        btn.addEventListener('click', () => {
          const findingId = btn.getAttribute('data-finding-id');
          const fixIndex  = parseInt(btn.getAttribute('data-fix-index'), 10);
          const fixes     = (lastData.fixes || []).filter(f => f.finding_id === findingId);
          const fix       = fixes[fixIndex];
          if (fix && vscode) {
            vscode.postMessage({ command: 'applyFix', fix });
            btn.textContent = '✅ Applied!';
            btn.disabled = true;
            btn.classList.add('applied');
          }
        });
      });
    }
  } // end buildGraph

  // ══════════════════════════════════════════════════════════════════════
  //  Sidebar — Problems & Fixes (unchanged)
  // ══════════════════════════════════════════════════════════════════════
  function renderSidebar(data) {
    const sidebar = document.getElementById('problemsSidebar');
    if (!sidebar) return;

    const list       = document.getElementById('problemsList');
    const countBadge = document.getElementById('problemCount');

    const fixes        = data.fixes || [];
    const kg           = data.knowledge_graph || {};
    const unguarded    = kg.unguarded_writes || [];
    const varSummaries = kg.variable_summaries || [];
    const agentResults = data.agent_results || {};
    const agentFindings = agentResults.results || [];

    if (fixes.length === 0 && unguarded.length === 0) {
      countBadge.textContent = '0 issues';
      countBadge.title = '0 fixable';
      list.innerHTML = '<div style="color:#81c784;padding:24px;text-align:center;font-size:13px;">✅ No concurrency issues detected!<br><span style="color:#888;font-size:11px">Your code looks safe.</span></div>';
      show(sidebar);
      if (!sidebarInitialized) { setSidebarOpen(true); sidebarInitialized = true; }
      return;
    }

    show(sidebar);
    if (!sidebarInitialized) { setSidebarOpen(true); sidebarInitialized = true; }

    const kgLookup  = {};
    unguarded.forEach(u => { kgLookup[u.id] = u; });

    const varLookup = {};
    varSummaries.forEach(vs => { varLookup[vs.variable] = vs; });

    const agentLookup = {};
    agentFindings.forEach(r => {
      const v = (r.finding || {}).variable || '';
      if (v) agentLookup[v] = (r.analyst || {}).analysis || {};
    });

    const llmFixLookup = {};
    (data.llm_fix_analysis || []).forEach(a => {
      const v = a.variable || '';
      if (v) llmFixLookup[v] = a;
    });

    const globalFixes = [];
    const varFixes    = [];
    fixes.forEach((fix, index) => {
      if (fix.finding_id && fix.finding_id.startsWith('finding:llm_')) {
        globalFixes.push({ ...fix, _originalIndex: index });
      } else {
        varFixes.push({ ...fix, _originalIndex: index });
      }
    });

    const problems = new Map();

    unguarded.forEach(u => {
      const varName = u.variable || 'unknown';
      problems.set(u.id, {
        id: u.id, variable: varName, line: u.line,
        severity: u.severity || 'medium', subtype: u.subtype || 'unprotected_access',
        agentAnalysis: agentLookup[varName] || {},
        llmFixAnalysis: llmFixLookup[varName] || {},
        fixes: [],
      });
    });

    varFixes.forEach(fix => {
      const fid = fix.finding_id;
      if (!problems.has(fid)) {
        const kgInfo  = kgLookup[fid] || {};
        const varName = kgInfo.variable || fid.split('_')[0].replace('finding:', '');
        problems.set(fid, {
          id: fid, variable: varName, line: kgInfo.line || null,
          severity: kgInfo.severity || 'medium', subtype: kgInfo.subtype || 'unprotected_access',
          agentAnalysis: agentLookup[varName] || {},
          llmFixAnalysis: llmFixLookup[varName] || {},
          fixes: [],
        });
      }
      problems.get(fid).fixes.push(fix);
    });

    const totalIssues = problems.size;
    countBadge.textContent = `${totalIssues} issues`;
    countBadge.title = `${totalIssues} issues`;

    let html = '';

    const fullFix    = globalFixes.find(f => f.strategy === 'llm_full_file' || f.full_file_content);
    const pragmaFix  = globalFixes.find(f => f.strategy === 'llm_pragma_clause');
    const combinedFix = globalFixes.find(f => f.strategy === 'llm_combined');
    const topFix     = pragmaFix || combinedFix || fullFix;

    if (topFix) {
      html += `<div style="padding:8px 0 12px">
        <div style="font-size:12px;color:#81c784;margin-bottom:6px;"><b>💡 LLM Recommended Fix</b></div>
        <button id="applyFullFixBtn" class="apply-fix-btn" style="width:100%;padding:10px;font-size:14px;font-weight:bold;">⚡ Apply ${topFix.strategy === 'llm_pragma_clause' ? 'Pragma' : 'Full'} Fix</button>
      </div>`;
    } else {
      html += `<div style="padding:8px 0 12px">
        <button id="retryFullFixBtn" class="apply-fix-btn" style="width:100%;padding:10px;font-size:14px;font-weight:bold;">↻ Retry LLM Full Fix</button>
      </div>`;
    }

    const sevOrder = { high: 0, medium: 1, low: 2 };
    const sorted = [...problems.values()].sort((a, b) => (sevOrder[a.severity] || 1) - (sevOrder[b.severity] || 1));

    sorted.forEach((prob) => {
      const sevColor = prob.severity === 'high' ? '#e57373' : prob.severity === 'medium' ? '#ffb74d' : '#81c784';
      const sevLabel = (prob.severity || 'MEDIUM').toUpperCase();
      const llmFix   = prob.llmFixAnalysis || {};
      const agent    = prob.agentAnalysis  || {};

      html += `<div class="problem-card">
        <div class="problem-card-header">
          <span style="background:${sevColor};color:#000;padding:1px 6px;border-radius:3px;font-size:10px;font-weight:bold;margin-right:6px;">${sevLabel}</span>
          Unprotected access to <code style="color:#ffb74d;background:#333;padding:1px 4px;border-radius:2px;">${escapeHtml(prob.variable)}</code>
          ${prob.line ? `<span style="color:#888;font-size:11px;">(Line ${prob.line})</span>` : ''}
        </div>`;

      if (llmFix.root_cause) {
        html += `<div class="problem-desc"><b>Why:</b> ${escapeHtml(llmFix.root_cause)}</div>`;
        if (llmFix.strategy) html += `<div style="font-size:11px;color:#90caf9;margin:4px 0;"><b>LLM Strategy:</b> <code>${escapeHtml(llmFix.strategy)}</code><br/>${escapeHtml(llmFix.reasoning || '')}</div>`;
      } else if (agent.root_cause) {
        html += `<div class="problem-desc"><b>Why:</b> ${escapeHtml(agent.root_cause)}</div>`;
      } else {
        html += `<div class="problem-desc"><b>Why:</b> The variable <code>${escapeHtml(prob.variable)}</code> is accessed by multiple threads at line ${prob.line || '?'} without any synchronization (lock/critical section). This can cause unpredictable values.</div>`;
      }

      if (prob.fixes.length) {
        const bestFix = prob.fixes[0];
        html += `<div class="problem-fix-area">
          <div class="fix-title">Rule-based fix: ${escapeHtml(bestFix.strategy)}</div>
          <div style="font-size:11px;color:#aaa;">${escapeHtml(bestFix.description)}</div>
        </div>`;
      } else if (agent.recommended_fix) {
        html += `<div class="problem-fix-area">
          <div class="fix-title">Recommendation</div>
          <div style="font-size:11px;color:#aaa;">${escapeHtml(agent.recommended_fix)}</div>
        </div>`;
      }

      html += '</div>';
    });

    list.innerHTML = html;

    const applyFullFixBtn = document.getElementById('applyFullFixBtn');
    if (applyFullFixBtn) {
      applyFullFixBtn.addEventListener('click', () => {
        const fullFixNow = (lastData.fixes || []).find(f => f.full_file_content);
        if (fullFixNow && vscode) {
          vscode.postMessage({ command: 'applyFullFix', filePath: fullFixNow.file_path, content: fullFixNow.full_file_content });
          applyFullFixBtn.textContent = '✅ Applied full fix';
          applyFullFixBtn.disabled = true;
          applyFullFixBtn.style.background = '#2e7d32';
          setTimeout(() => {
            if (activePath && vscode) vscode.postMessage({ command: 'analyzeFile', path: activePath, quick: true });
          }, 2500);
        }
      });
    }

    const retryFullFixBtn = document.getElementById('retryFullFixBtn');
    if (retryFullFixBtn) {
      retryFullFixBtn.addEventListener('click', () => {
        retryFullFixBtn.textContent = '⏳ Retrying LLM…';
        retryFullFixBtn.disabled = true;
        if (activePath && vscode) vscode.postMessage({ command: 'analyzeFile', path: activePath });
      });
    }
  }

})();
