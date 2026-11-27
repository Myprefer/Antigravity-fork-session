(() => {
  const urlParams = new URLSearchParams(window.location.search);
  const sidecarToken = urlParams.get('token') || '';
  const initialConvId = (
    urlParams.get('conversationId') ||
    (window.sidecar && window.sidecar.conversationId) ||
    ''
  ).trim().toLowerCase();

  // Sync light/dark theme with Antigravity host
  function applyHostTheme(payload) {
    if (!payload || typeof payload !== 'object') return;
    const vars = payload.cssVariables || payload.variables || {};
    for (const [k, v] of Object.entries(vars)) {
      if (k.startsWith('--') && typeof v === 'string') {
        document.documentElement.style.setProperty(k, v);
      }
    }
    const mode = payload.theme || payload.colorScheme || payload.mode;
    if (mode === 'dark' || mode === 'light') {
      document.documentElement.setAttribute('data-theme', mode);
    } else if (typeof vars['--background'] === 'string') {
      const bg = vars['--background'].trim().toLowerCase();
      if (bg.startsWith('#') && bg.length >= 7) {
        const r = parseInt(bg.slice(1, 3), 16);
        const g = parseInt(bg.slice(3, 5), 16);
        const b = parseInt(bg.slice(5, 7), 16);
        const lum = 0.2126 * r + 0.7152 * g + 0.0722 * b;
        document.documentElement.setAttribute('data-theme', lum < 128 ? 'dark' : 'light');
      }
    }
  }

  // Default to clean light theme matching Antigravity Desktop; only switch on host theme-change
  document.documentElement.setAttribute('data-theme', 'light');

  const ICONS = {
    fork: '<svg class="icon" viewBox="0 0 24 24"><circle cx="6" cy="6" r="3"/><circle cx="18" cy="6" r="3"/><circle cx="6" cy="18" r="3"/><path d="M6 9v6"/><path d="M18 9c0 6-12 3-12 9"/></svg>',
    undo: '<svg class="icon" viewBox="0 0 24 24"><polyline points="9 14 4 9 9 4"/><path d="M20 20v-7a4 4 0 0 0-4-4H4"/></svg>',
    turns: '<svg class="icon" viewBox="0 0 24 24"><line x1="8" y1="6" x2="21" y2="6"/><line x1="8" y1="12" x2="21" y2="12"/><line x1="8" y1="18" x2="21" y2="18"/><circle cx="4" cy="6" r="1"/><circle cx="4" cy="12" r="1"/><circle cx="4" cy="18" r="1"/></svg>',
    open: '<svg class="icon" viewBox="0 0 24 24"><path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"/><polyline points="15 3 21 3 21 9"/><line x1="10" y1="14" x2="21" y2="3"/></svg>',
    sync: '<svg class="icon" viewBox="0 0 24 24"><polyline points="17 1 21 5 17 9"/><path d="M3 11V9a4 4 0 0 1 4-4h14"/><polyline points="7 23 3 19 7 15"/><path d="M21 13v2a4 4 0 0 1-4 4H3"/></svg>',
    edit: '<svg class="icon" viewBox="0 0 24 24"><path d="M12 20h9"/><path d="M16.5 3.5a2.12 2.12 0 1 1 3 3L7 19l-4 1 1-4Z"/></svg>',
    copy: '<svg class="icon" viewBox="0 0 24 24"><rect x="9" y="9" width="13" height="13" rx="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></svg>',
    export: '<svg class="icon" viewBox="0 0 24 24"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></svg>',
  };

  const state = {
    currentConvId: initialConvId,
    activeView: initialConvId ? 'current' : 'all',
    sessions: [],
    projects: [],
    backups: [],
    currentTurns: [],
    surfaceFilter: 'all',
    projectFilter: 'all',
    search: '',
    hideEmpty: true,
    showSub: false,
    turnsContext: null,
    forkContext: null,
    syncContext: null,
    renameContext: null,
    importContext: { file: null, inspected: null },
  };

  const $ = (id) => document.getElementById(id);

  function esc(str) {
    return String(str ?? '')
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;');
  }

  function renderInlineMd(raw) {
    const codeSlots = [];
    let s = esc(raw).replace(/`([^`\n]+)`/g, (_, code) => {
      const idx = codeSlots.length;
      codeSlots.push(`<code>${code}</code>`);
      return `\u0000C${idx}\u0000`;
    });
    // Links: [label](url)
    s = s.replace(/\[([^\]]+)\]\(([^)\s]+)\)/g, (_, label, url) => {
      if (url.startsWith('http://') || url.startsWith('https://')) {
        return `<a href="${url}" target="_blank" rel="noopener noreferrer">${label}</a>`;
      }
      if (url.startsWith('file://') || url.startsWith('conversation://') || url.startsWith('sidecar://') || url.startsWith('customization://')) {
        return `<span class="md-file-chip">${label}</span>`;
      }
      return label;
    });
    // Bold & Italic
    s = s.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');
    s = s.replace(/(^|[^*])\*([^*\n]+)\*(?!\*)/g, '$1<em>$2</em>');
    // Restore inline code
    s = s.replace(/\u0000C(\d+)\u0000/g, (_, idx) => codeSlots[Number(idx)] || '');
    return s;
  }

  function renderMarkdown(md) {
    if (!md) return '';
    const text = String(md).replace(/\r\n?/g, '\n');
    // Split fenced code blocks first
    const parts = text.split(/(```[^\n]*\n[\s\S]*?```)/g);
    const html = [];

    for (const part of parts) {
      if (!part) continue;
      if (part.startsWith('```') && part.endsWith('```')) {
        const firstNl = part.indexOf('\n');
        const codeBody = firstNl >= 0 ? part.slice(firstNl + 1, -3) : part.slice(3, -3);
        html.push(`<pre><code>${esc(codeBody)}</code></pre>`);
        continue;
      }

      const lines = part.split('\n');
      let listTag = null;
      let paraLines = [];

      const flushPara = () => {
        if (!paraLines.length) return;
        html.push(`<p>${paraLines.map(renderInlineMd).join('<br>')}</p>`);
        paraLines = [];
      };
      const closeList = () => {
        if (!listTag) return;
        html.push(`</${listTag}>`);
        listTag = null;
      };

      for (const line of lines) {
        const trimmed = line.trim();
        if (!trimmed) {
          flushPara();
          closeList();
          continue;
        }
        if (/^([-*_])\1{2,}$/.test(trimmed)) {
          flushPara();
          closeList();
          html.push('<hr>');
          continue;
        }
        const hMatch = trimmed.match(/^(#{1,4})\s+(.+)$/);
        if (hMatch) {
          flushPara();
          closeList();
          const lvl = hMatch[1].length;
          html.push(`<h${lvl}>${renderInlineMd(hMatch[2])}</h${lvl}>`);
          continue;
        }
        const bqMatch = trimmed.match(/^>\s?(.*)$/);
        if (bqMatch) {
          flushPara();
          closeList();
          html.push(`<blockquote>${renderInlineMd(bqMatch[1])}</blockquote>`);
          continue;
        }
        const ulMatch = line.match(/^\s*[-*•]\s+(.+)$/);
        if (ulMatch) {
          flushPara();
          if (listTag !== 'ul') {
            closeList();
            listTag = 'ul';
            html.push('<ul>');
          }
          html.push(`<li>${renderInlineMd(ulMatch[1])}</li>`);
          continue;
        }
        const olMatch = line.match(/^\s*\d+\.\s+(.+)$/);
        if (olMatch) {
          flushPara();
          if (listTag !== 'ol') {
            closeList();
            listTag = 'ol';
            html.push('<ol>');
          }
          html.push(`<li>${renderInlineMd(olMatch[1])}</li>`);
          continue;
        }
        closeList();
        paraLines.push(trimmed);
      }
      flushPara();
      closeList();
    }
    return html.join('');
  }

  function formatBytes(bytes) {
    if (!bytes || bytes < 1024) return `${bytes || 0} B`;
    const units = ['KB', 'MB', 'GB'];
    let v = bytes / 1024;
    let i = 0;
    while (v >= 1024 && i < units.length - 1) { v /= 1024; i++; }
    return `${v.toFixed(v >= 10 ? 1 : 2)} ${units[i]}`;
  }

  function formatRelTime(epochSec) {
    if (!epochSec) return '';
    const diffSec = Math.max(0, Math.floor(Date.now() / 1000 - epochSec));
    if (diffSec < 60) return '刚刚';
    if (diffSec < 3600) return `${Math.floor(diffSec / 60)}m ago`;
    if (diffSec < 86400) return `${Math.floor(diffSec / 3600)}h ago`;
    return `${Math.floor(diffSec / 86400)}d ago`;
  }

  function toast(msg, isError = false) {
    const el = document.createElement('div');
    el.className = `toast${isError ? ' error' : ''}`;
    el.textContent = msg;
    $('toastContainer').appendChild(el);
    setTimeout(() => el.remove(), 3000);
  }

  async function copyText(text, label = '已复制') {
    try {
      await navigator.clipboard.writeText(text);
      toast(label);
    } catch {
      const ta = document.createElement('textarea');
      ta.value = text;
      document.body.appendChild(ta);
      ta.select();
      document.execCommand('copy');
      ta.remove();
      toast(label);
    }
  }

  function canToggleDesktopConv() {
    return Boolean((window.sidecar && window.sidecar.ui && window.sidecar.ui.toggleConversation) || window.parent !== window);
  }

  function openDesktopConversation(convId) {
    if (window.sidecar && window.sidecar.ui && typeof window.sidecar.ui.toggleConversation === 'function') {
      window.sidecar.ui.toggleConversation(convId);
      return true;
    }
    if (window.parent !== window) {
      window.parent.postMessage({ type: 'toggle-conversation', payload: { conversationId: convId } }, '*');
      return true;
    }
    copyText(`conversation://${convId}`, '已复制 conversation:// 链接');
    return false;
  }

  async function api(url, opts = {}) {
    const headers = { 'Content-Type': 'application/json', ...(opts.headers || {}) };
    if (sidecarToken) headers['X-Sidecar-Token'] = sidecarToken;
    const res = await fetch(url, { ...opts, headers });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      const err = new Error(data.error || `请求失败 (${res.status})`);
      err.status = res.status;
      throw err;
    }
    return data;
  }

  function scrollToBottom() {
    requestAnimationFrame(() => {
      window.scrollTo({ top: document.documentElement.scrollHeight, behavior: 'auto' });
    });
  }

  function switchView(view) {
    state.activeView = view;
    document.querySelectorAll('#viewTabs .nav-tab').forEach((b) => {
      b.classList.toggle('active', b.dataset.view === view);
    });
    $('viewCurrent').classList.toggle('hidden', view !== 'current');
    $('viewAll').classList.toggle('hidden', view !== 'all');
    $('viewBackups').classList.toggle('hidden', view !== 'backups');
    if (view === 'backups') renderBackups();
    if (view === 'current' && !$('currentTurnsSearch').value.trim()) {
      scrollToBottom();
    }
  }

  async function loadOverview(silent = false) {
    try {
      const data = await api('/api/overview');
      state.sessions = data.sessions || [];
      state.projects = data.projects || [];
      state.backups = data.backups || [];

      const dCnt = data.stats?.desktop_count ?? 0;
      const cCnt = data.stats?.cli_count ?? 0;
      $('statDesktop').textContent = dCnt;
      $('statCli').textContent = cCnt;
      $('badgeAllCount').textContent = dCnt + cCnt;
      $('badgeBackups').textContent = data.stats?.backup_count ?? 0;

      if (!state.currentConvId) {
        const latestDesktop = state.sessions.find((s) => s.surface === 'desktop' && !s.is_subsession && s.step_count > 0);
        if (latestDesktop) state.currentConvId = latestDesktop.id;
      }

      populateProjectFilter();
      renderSessions();
      if (state.activeView === 'backups') renderBackups();
      await loadCurrentSessionView();
      if (!silent) toast('已刷新');
    } catch (err) {
      toast(err.message, true);
    }
  }

  async function loadCurrentSessionView() {
    const cur = findSession('desktop', state.currentConvId);
    if (!cur) {
      $('badgeCurrentTurns').textContent = '0';
      $('currentSessionBanner').innerHTML = `<div class="section-hint" style="margin:0">未定位到当前桌面端会话，可切到「全部会话」选择。</div>`;
      $('currentTurnsList').innerHTML = '';
      return;
    }

    $('currentSessionBanner').innerHTML = `
      <h2 class="current-title">${esc(cur.display_title)}</h2>
      <div class="inline-meta">
        <span class="tag">${esc(cur.project_name)}</span>
        <span>${cur.step_count} 步</span>
        <span>·</span>
        <span>${formatBytes(cur.size_bytes)}</span>
        <span>·</span>
        <span class="mono-id" id="curMonoId" title="点击复制完整 ID">${cur.id.slice(0, 8)}</span>
      </div>
    `;
    const mono = $('curMonoId');
    if (mono) mono.onclick = () => copyText(cur.id, '已复制会话 ID');

    try {
      const tdata = await api(`/api/sessions/desktop/${cur.id}/turns`);
      state.currentTurns = tdata.turns || [];
      $('badgeCurrentTurns').textContent = state.currentTurns.length;
      renderCurrentTurns();
      if (state.activeView === 'current' && !$('currentTurnsSearch').value.trim()) {
        scrollToBottom();
      }
    } catch (err) {
      $('currentTurnsList').innerHTML = `<div class="empty-state">加载轮次失败：${esc(err.message)}</div>`;
    }
  }

  function renderTurnCards(turns, searchQuery, targetEl) {
    const q = (searchQuery || '').trim().toLowerCase();
    const list = turns.filter((t) => !q || `${t.user_text} ${t.assistant_text || t.assistant_preview}`.toLowerCase().includes(q));
    if (!list.length) {
      targetEl.innerHTML = `<div class="empty-state">没有匹配的对话轮次</div>`;
      return;
    }
    targetEl.innerHTML = list
      .map((t) => {
        const beforeBtn =
          t.turn_number > 1 && t.keep_steps_before > 0
            ? `<button class="msg-action" data-turn-fork="before" data-keep="${t.keep_steps_before}" data-tnum="${t.turn_number}" title="回退到此轮之前 Fork（保留前 ${t.keep_steps_before} 步）">${ICONS.undo}<span>此前 Fork</span></button>`
            : '';
        const workedText = t.tool_steps > 0
          ? `Turn ${t.turn_number} · ${t.step_count} steps (${t.tool_steps} tool calls)`
          : `Turn ${t.turn_number} · ${t.step_count} steps`;
        const rawReply = t.assistant_text || t.assistant_preview || '';
        const isLong = rawReply.length > 480 || (rawReply.match(/\n/g) || []).length > 8;
        const replyHtml = rawReply
          ? `<div class="turn-reply md-body${isLong ? ' collapsed' : ''}">${renderMarkdown(rawReply)}</div>${isLong ? '<button type="button" class="expand-btn" data-toggle-expand>展开全文</button>' : ''}`
          : '';
        return `
          <div class="turn-item">
            <div class="turn-user-row">
              <div class="turn-user-bubble">${esc(t.user_text || '(空提示词)')}</div>
            </div>
            <div class="turn-worked">${workedText}</div>
            ${replyHtml}
            <div class="turn-footer">
              <button class="msg-action" data-turn-fork="after" data-keep="${t.keep_steps_after}" data-tnum="${t.turn_number}" title="保留到此轮回复后 Fork（共 ${t.keep_steps_after} 步）">
                ${ICONS.fork}<span>从此轮 Fork</span>
              </button>
              ${beforeBtn}
              ${t.timestamp ? `<span class="turn-time">${formatRelTime(t.timestamp)}</span>` : ''}
            </div>
          </div>
        `;
      })
      .join('');
  }

  function renderCurrentTurns() {
    renderTurnCards(state.currentTurns, $('currentTurnsSearch').value, $('currentTurnsList'));
  }

  function populateProjectFilter() {
    const sel = $('projectFilter');
    const cur = sel.value || 'all';
    const visibleProjects = state.projects.filter((p) => !p.is_sidecar);
    sel.innerHTML =
      `<option value="all">全部项目</option>` +
      visibleProjects.map((p) => `<option value="${esc(p.id)}">${esc(p.name)}</option>`).join('');
    if ([...sel.options].some((o) => o.value === cur)) sel.value = cur;
  }

  function formatFolderUri(uri) {
    if (!uri) return '';
    if (/^file:\/\/\/[A-Za-z]:/.test(uri)) {
      return decodeURIComponent(uri.slice(8)).replace(/\//g, '\\');
    }
    return decodeURIComponent(uri.replace(/^file:\/\//, ''));
  }

  function fillProjectSelect(selectEl, defaultProjectId, origName = '', includeKeep = true) {
    const visibleProjects = state.projects.filter((p) => !p.is_sidecar);
    let html = includeKeep
      ? `<option value="">保持原项目 (${esc(origName || defaultProjectId || '原归属')})</option>`
      : '';
    html += visibleProjects
      .map((p) => {
        const pathHint = p.folder_uris?.[0] ? ` · ${formatFolderUri(p.folder_uris[0])}` : '';
        return `<option value="${esc(p.id)}">${esc(p.name)}${esc(pathHint)}</option>`;
      })
      .join('');
    selectEl.innerHTML = html;
    selectEl.value = includeKeep ? '' : (defaultProjectId || 'outside-of-project');
  }

  function filteredSessions() {
    const q = state.search.trim().toLowerCase();
    return state.sessions.filter((s) => {
      if (state.surfaceFilter !== 'all' && s.surface !== state.surfaceFilter) return false;
      if (state.projectFilter !== 'all' && s.project_id !== state.projectFilter) return false;
      if (state.hideEmpty && s.step_count === 0) return false;
      if (!state.showSub && s.is_subsession) return false;
      if (q) {
        const hay = `${s.display_title} ${s.preview} ${s.id} ${s.project_name}`.toLowerCase();
        if (!hay.includes(q)) return false;
      }
      return true;
    });
  }

  function renderSessions() {
    const list = filteredSessions();
    const container = $('sessionList');
    if (!list.length) {
      container.innerHTML = `<div class="empty-state">没有匹配的会话</div>`;
      return;
    }

    const showOpenBtn = canToggleDesktopConv();
    container.innerHTML = list
      .map((s) => {
        const isDesktop = s.surface === 'desktop';
        const isCurrent = isDesktop && s.id === state.currentConvId;
        const otherLabel = isDesktop ? 'CLI' : '桌面端';
        const crossTag = s.exists_in_other
          ? `<span class="tag" title="对端存在同 ID 会话">${otherLabel} ${s.other_step_count}步</span>`
          : '';
        const liveTag = s.active ? `<span class="tag tag-live"><span class="dot-live"></span>活跃</span>` : '';
        const curTag = isCurrent ? `<span class="tag tag-accent">当前</span>` : '';

        const actions = isDesktop
          ? `
            <button class="msg-action" data-act="fork" data-surface="${s.surface}" data-id="${s.id}">${ICONS.fork}<span>Fork</span></button>
            <button class="msg-action" data-act="turns" data-surface="${s.surface}" data-id="${s.id}">${ICONS.turns}<span>轮次</span></button>
            ${showOpenBtn && !isCurrent ? `<button class="msg-action" data-act="open" data-id="${s.id}">${ICONS.open}<span>打开</span></button>` : ''}
            <button class="msg-action" data-act="sync" data-surface="${s.surface}" data-id="${s.id}">${ICONS.sync}<span>CLI</span></button>
          `
          : `
            <button class="msg-action" data-act="sync" data-surface="${s.surface}" data-id="${s.id}">${ICONS.sync}<span>导入桌面</span></button>
            <button class="msg-action" data-act="fork-to-desktop" data-surface="${s.surface}" data-id="${s.id}">${ICONS.fork}<span>Fork到桌面</span></button>
            <button class="msg-action" data-act="turns" data-surface="${s.surface}" data-id="${s.id}">${ICONS.turns}<span>轮次</span></button>
            <button class="msg-action" data-act="copy-cli" data-id="${s.id}">${ICONS.copy}<span>agy</span></button>
          `;

        return `
          <article class="session-row${isCurrent ? ' is-current' : ''}">
            <div class="session-row-top">
              <h2 class="session-title">${esc(s.display_title)}</h2>
              <span class="session-time">${formatRelTime(s.mtime)}</span>
            </div>
            ${s.preview && s.preview !== s.display_title ? `<p class="session-preview">${esc(s.preview)}</p>` : ''}
            <div class="session-row-foot">
              <div class="session-meta">
                <span class="tag">${isDesktop ? '桌面端' : 'CLI'}</span>
                ${curTag}
                <span>${esc(s.project_name)}</span>
                <span>· ${s.step_count} 步</span>
                ${liveTag}
                ${crossTag}
                <span class="mono-id" data-act="copy-id" data-id="${s.id}" title="点击复制完整 ID">${s.id.slice(0, 8)}</span>
              </div>
              <div class="session-actions">
                ${actions}
                <button class="msg-action" data-act="export" data-surface="${s.surface}" data-id="${s.id}" title="导出会话包">${ICONS.export}</button>
                <button class="msg-action" data-act="rename" data-surface="${s.surface}" data-id="${s.id}" title="重命名">${ICONS.edit}</button>
              </div>
            </div>
          </article>
        `;
      })
      .join('');
  }

  function findSession(surface, id) {
    return state.sessions.find((s) => s.surface === surface && s.id === id);
  }

  async function openTurnsDrawer(surface, id) {
    const s = findSession(surface, id);
    if (!s) return;
    state.turnsContext = { session: s, turns: [] };
    $('turnsSurfaceTag').textContent = s.surface === 'desktop' ? '桌面端' : 'CLI';
    $('turnsConvId').textContent = s.id.slice(0, 8);
    $('turnsConvId').onclick = () => copyText(s.id, '已复制会话 ID');
    $('turnsTitle').textContent = s.display_title;
    $('turnsSearch').value = '';
    $('turnsBody').innerHTML = `<div class="empty-state">正在读取轮次...</div>`;
    $('turnsBackdrop').classList.remove('hidden');

    try {
      const data = await api(`/api/sessions/${surface}/${id}/turns`);
      state.turnsContext.turns = data.turns || [];
      renderTurnCards(state.turnsContext.turns, '', $('turnsBody'));
    } catch (err) {
      $('turnsBody').innerHTML = `<div class="empty-state">读取失败：${esc(err.message)}</div>`;
    }
  }

  function openForkModal(session, opts = {}) {
    const { targetSurface = 'desktop', keepSteps = null, turnLabel = '' } = opts;
    state.forkContext = { session, keepSteps, turnLabel };

    const isTrunc = keepSteps !== null && keepSteps < session.step_count;
    $('forkModalTitle').textContent = isTrunc ? '按轮次 Fork' : 'Fork 会话';
    $('forkSummaryBanner').innerHTML = `
      <div><strong>来源：</strong>${esc(session.display_title)}</div>
      <div style="margin-top:3px"><strong>范围：</strong>${
        isTrunc
          ? `${esc(turnLabel)}（保留前 ${keepSteps} / ${session.step_count} 步）`
          : `完整历史（共 ${session.step_count} 步）`
      }</div>
    `;

    document.querySelectorAll('#forkForm input[name="targetSurface"]').forEach((r) => {
      r.checked = r.value === targetSurface;
    });

    const baseTitle = session.title || session.display_title;
    $('forkTitleInput').value = isTrunc
      ? `${baseTitle} (Fork ${turnLabel || `@${keepSteps}`})`
      : `${baseTitle} (Fork)`;

    fillProjectSelect($('forkProjectSelect'), session.project_id, session.project_name);
    $('forkModalBackdrop').classList.remove('hidden');
    $('forkTitleInput').focus();
  }

  async function submitFork(ev) {
    ev.preventDefault();
    const ctx = state.forkContext;
    if (!ctx) return;
    const btn = $('btnSubmitFork');
    btn.disabled = true;
    btn.textContent = '处理中...';
    try {
      const targetSurface = document.querySelector('#forkForm input[name="targetSurface"]:checked')?.value || 'desktop';
      const title = $('forkTitleInput').value.trim();
      const projectId = $('forkProjectSelect').value;

      const res = await api('/api/fork', {
        method: 'POST',
        body: JSON.stringify({
          source_surface: ctx.session.surface,
          source_id: ctx.session.id,
          target_surface: targetSurface,
          title,
          project_id: projectId || null,
          keep_steps: ctx.keepSteps,
        }),
      });

      $('forkModalBackdrop').classList.add('hidden');
      $('turnsBackdrop').classList.add('hidden');
      await loadOverview(true);
      showResultModal('Fork 完成', res);
    } catch (err) {
      toast(err.message, true);
    } finally {
      btn.disabled = false;
      btn.textContent = '确认 Fork';
    }
  }

  function openSyncModal(session) {
    const targetSurface = session.surface === 'cli' ? 'desktop' : 'cli';
    state.syncContext = { session, targetSurface };
    $('syncModalTitle').textContent = targetSurface === 'desktop' ? '导入到桌面端' : '同步到 CLI';
    $('syncSummaryBanner').innerHTML = `
      <div><strong>来源：</strong>${esc(session.display_title)}</div>
      <div style="margin-top:3px"><strong>ID：</strong><code>${session.id}</code>（${session.step_count} 步）</div>
    `;
    const warn = $('syncExistsWarn');
    if (session.exists_in_other) {
      warn.classList.remove('hidden');
      warn.innerHTML = `目标端已存在同 ID 会话（${session.other_step_count} 步）。覆盖前会自动备份旧版；也可点击左下角改用新 ID Fork。`;
      $('btnSubmitSync').textContent = '备份并覆盖';
    } else {
      warn.classList.add('hidden');
      $('btnSubmitSync').textContent = targetSurface === 'desktop' ? '导入桌面端' : '同步到 CLI';
    }
    $('syncTitleInput').value = session.title || '';
    fillProjectSelect($('syncProjectSelect'), session.project_id, session.project_name);
    $('syncModalBackdrop').classList.remove('hidden');
  }

  async function submitSync(ev) {
    ev.preventDefault();
    const ctx = state.syncContext;
    if (!ctx) return;
    const btn = $('btnSubmitSync');
    btn.disabled = true;
    try {
      const res = await api('/api/sync', {
        method: 'POST',
        body: JSON.stringify({
          source_surface: ctx.session.surface,
          source_id: ctx.session.id,
          target_surface: ctx.targetSurface,
          overwrite: Boolean(ctx.session.exists_in_other),
          title: $('syncTitleInput').value.trim() || null,
          project_id: $('syncProjectSelect').value || null,
        }),
      });
      $('syncModalBackdrop').classList.add('hidden');
      await loadOverview(true);
      showResultModal(ctx.targetSurface === 'desktop' ? '已导入桌面端' : '已同步到 CLI', res);
    } catch (err) {
      toast(err.message, true);
    } finally {
      btn.disabled = false;
    }
  }

  function openRenameModal(session) {
    state.renameContext = session;
    $('renameTitleInput').value = session.title || session.display_title;
    $('renameModalBackdrop').classList.remove('hidden');
    $('renameTitleInput').focus();
    $('renameTitleInput').select();
  }

  async function submitRename(ev) {
    ev.preventDefault();
    const s = state.renameContext;
    if (!s) return;
    const title = $('renameTitleInput').value.trim();
    if (!title) return;
    try {
      await api('/api/rename', {
        method: 'POST',
        body: JSON.stringify({ surface: s.surface, conv_id: s.id, title }),
      });
      $('renameModalBackdrop').classList.add('hidden');
      toast('标题已更新');
      await loadOverview(true);
    } catch (err) {
      toast(err.message, true);
    }
  }

  async function exportSession(surface, id) {
    toast('正在打包导出会话...');
    try {
      const headers = {};
      if (sidecarToken) headers['X-Sidecar-Token'] = sidecarToken;
      const res = await fetch(`/api/export/${surface}/${id}?include_brain=1`, { headers });
      if (!res.ok) {
        const errData = await res.json().catch(() => ({}));
        throw new Error(errData.error || `导出失败 (${res.status})`);
      }
      const cd = res.headers.get('Content-Disposition') || '';
      let filename = `session_${id.slice(0, 8)}.agysess.zip`;
      const mStar = cd.match(/filename\*=UTF-8''([^;]+)/i);
      const mPlain = cd.match(/filename="([^"]+)"/i);
      if (mStar) filename = decodeURIComponent(mStar[1]);
      else if (mPlain) filename = mPlain[1];

      const blob = await res.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = filename;
      document.body.appendChild(a);
      a.click();
      a.remove();
      setTimeout(() => URL.revokeObjectURL(url), 4000);
      toast(`已导出 ${filename}`);
    } catch (err) {
      toast(err.message, true);
    }
  }

  function updateImportConflictWarn() {
    const info = state.importContext.inspected;
    const warn = $('importExistsWarn');
    if (!info) {
      warn.classList.add('hidden');
      return;
    }
    const target = document.querySelector('#importForm input[name="importTargetSurface"]:checked')?.value || 'desktop';
    const idMode = document.querySelector('#importForm input[name="importIdMode"]:checked')?.value || 'new';
    const exists = target === 'desktop' ? info.exists_in_desktop : info.exists_in_cli;
    if (idMode === 'keep' && exists) {
      warn.classList.remove('hidden');
      warn.textContent = `本机目标端已存在相同 ID (${info.original_id.slice(0, 8)}) 的会话；导入时会自动备份旧版本再覆盖。`;
    } else {
      warn.classList.add('hidden');
    }
  }

  function openImportModal() {
    state.importContext = { file: null, inspected: null };
    $('importFileInput').value = '';
    $('importDropzoneText').innerHTML = '点击选择或拖入 <code>.agysess.zip</code> / <code>.db</code> 文件';
    $('importInspectBanner').classList.add('hidden');
    $('importExistsWarn').classList.add('hidden');
    $('importTitleInput').value = '';
    $('btnSubmitImport').disabled = true;
    fillProjectSelect($('importProjectSelect'), 'outside-of-project', '', false);
    $('importModalBackdrop').classList.remove('hidden');
  }

  async function handleImportFileSelected(file) {
    if (!file) return;
    state.importContext = { file, inspected: null };
    $('importDropzoneText').textContent = `正在解析 ${file.name} (${formatBytes(file.size)})...`;
    $('btnSubmitImport').disabled = true;
    $('importInspectBanner').classList.add('hidden');
    $('importExistsWarn').classList.add('hidden');

    try {
      const headers = { 'Content-Type': 'application/octet-stream', 'X-Filename': encodeURIComponent(file.name) };
      if (sidecarToken) headers['X-Sidecar-Token'] = sidecarToken;
      const res = await fetch('/api/import/inspect', { method: 'POST', headers, body: file });
      const info = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(info.error || `解析失败 (${res.status})`);

      state.importContext.inspected = info;
      $('importDropzoneText').textContent = `已选择：${file.name} (${formatBytes(file.size)})`;
      $('importInspectBanner').innerHTML = `
        <div><strong>会话：</strong>${esc(info.title || info.preview || info.original_id || file.name)}</div>
        <div style="margin-top:3px"><strong>详情：</strong>${info.step_count} 步 · 来源 ${esc(info.source_surface)} (${esc(info.source_os)})${info.has_brain ? ' · 含脑图/附件' : ''}</div>
      `;
      $('importInspectBanner').classList.remove('hidden');
      $('importTitleInput').value = info.title || '';
      $('btnSubmitImport').disabled = false;
      updateImportConflictWarn();
    } catch (err) {
      $('importDropzoneText').innerHTML = '点击选择或拖入 <code>.agysess.zip</code> / <code>.db</code> 文件';
      toast(err.message, true);
    }
  }

  async function submitImport(ev) {
    ev.preventDefault();
    const { file, inspected } = state.importContext;
    if (!file || !inspected) return;
    const btn = $('btnSubmitImport');
    btn.disabled = true;
    btn.textContent = '导入中...';
    try {
      const target = document.querySelector('#importForm input[name="importTargetSurface"]:checked')?.value || 'desktop';
      const idMode = document.querySelector('#importForm input[name="importIdMode"]:checked')?.value || 'new';
      const newId = idMode === 'new' ? '1' : '0';
      const overwrite = idMode === 'keep' ? '1' : '0';
      const title = $('importTitleInput').value.trim();
      const projectId = $('importProjectSelect').value || 'outside-of-project';

      const params = new URLSearchParams({
        target_surface: target,
        new_id: newId,
        overwrite,
        project_id: projectId,
      });
      if (title) params.set('title', title);

      const headers = { 'Content-Type': 'application/octet-stream' };
      if (sidecarToken) headers['X-Sidecar-Token'] = sidecarToken;
      const res = await fetch(`/api/import?${params.toString()}`, { method: 'POST', headers, body: file });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(data.error || `导入失败 (${res.status})`);

      $('importModalBackdrop').classList.add('hidden');
      await loadOverview(true);
      showResultModal('导入完成', data);
    } catch (err) {
      toast(err.message, true);
    } finally {
      btn.disabled = false;
      btn.textContent = '确认导入';
    }
  }

  function showResultModal(heading, res) {
    const newId = res.new_id || res.id;
    const target = res.target_surface;
    const proj = state.projects.find((p) => p.id === res.project_id);
    const projName = proj ? proj.name : (res.project_id || '');
    const isDesktop = target === 'desktop';

    $('resultModalTitle').textContent = heading;
    const lines = [
      `<div class="callout">`,
      `<div><strong>会话 ID：</strong><code>${esc(newId)}</code></div>`,
      res.step_count != null ? `<div style="margin-top:3px"><strong>步骤：</strong>${res.step_count} 步${res.truncated ? `（由 ${res.original_step_count} 步截断）` : ''}</div>` : '',
      projName ? `<div style="margin-top:3px"><strong>项目：</strong>${esc(projName)}</div>` : '',
      res.backup ? `<div style="margin-top:3px"><strong>旧版备份：</strong><code>${esc(res.backup.backup_name)}</code></div>` : '',
      `</div>`,
    ];

    if (isDesktop) {
      const hint = res.desktop_notified
        ? '已通知桌面端刷新，左侧边栏可直接看到，也可点击下方立即打开。'
        : '已写入桌面端目录；若侧边栏未刷新，点击打开或重启桌面端即可。';
      lines.push(`<p class="section-hint">${hint}</p>`);
      lines.push(`
        <div class="code-copy-box">
          <code>conversation://${esc(newId)}</code>
          <button type="button" class="btn btn-secondary btn-sm" data-copy="conversation://${esc(newId)}">复制</button>
        </div>
      `);
      $('resultModalBody').innerHTML = lines.join('');
      $('resultModalFooter').innerHTML = `
        <div class="spacer"></div>
        <button type="button" class="btn btn-secondary btn-sm" id="btnCloseRes2">关闭</button>
        <button type="button" class="btn btn-primary btn-sm" id="btnOpenNewConv">立即打开</button>
      `;
      $('btnCloseRes2').onclick = () => $('resultModalBackdrop').classList.add('hidden');
      $('btnOpenNewConv').onclick = () => {
        openDesktopConversation(newId);
        $('resultModalBackdrop').classList.add('hidden');
      };
    } else {
      const cmd = `agy --conversation ${newId}`;
      const rcCmd = `agy --conversation ${newId} --remote-control`;
      lines.push(`<p class="section-hint">在终端运行以下命令恢复 CLI 会话：</p>`);
      lines.push(`
        <div class="code-copy-box">
          <code>${esc(cmd)}</code>
          <button type="button" class="btn btn-secondary btn-sm" data-copy="${esc(cmd)}">复制</button>
        </div>
        <div class="code-copy-box">
          <code>${esc(rcCmd)}</code>
          <button type="button" class="btn btn-ghost btn-sm" data-copy="${esc(rcCmd)}">远程</button>
        </div>
      `);
      $('resultModalBody').innerHTML = lines.join('');
      $('resultModalFooter').innerHTML = `
        <div class="spacer"></div>
        <button type="button" class="btn btn-primary btn-sm" id="btnCloseRes2">好的</button>
      `;
      $('btnCloseRes2').onclick = () => $('resultModalBackdrop').classList.add('hidden');
    }

    $('resultModalBackdrop').classList.remove('hidden');
  }

  function renderBackups() {
    const body = $('backupsBody');
    if (!state.backups.length) {
      body.innerHTML = `<div class="empty-state">暂无备份记录</div>`;
      return;
    }
    body.innerHTML = state.backups
      .map(
        (b) => `
        <div class="backup-item">
          <div style="min-width:0">
            <div class="inline-meta" style="margin-top:0;margin-bottom:2px">
              <span class="tag">${b.surface === 'desktop' ? '桌面端' : 'CLI'}</span>
              <span class="mono-id">${esc(b.conv_id.slice(0, 8))}</span>
              <span>${formatBytes(b.size_bytes)}</span>
            </div>
            <div style="font-weight:500;font-size:12.5px">${esc(b.title || b.backup_name)}</div>
            <div class="inline-meta" style="margin-top:1px">${esc(b.reason)} · ${esc((b.created_at || '').replace('T', ' ').slice(0, 19))}</div>
          </div>
          <button class="btn btn-secondary btn-sm" data-restore="${esc(b.backup_name)}">恢复</button>
        </div>
      `
      )
      .join('');
  }

  // Wiring
  $('viewTabs').addEventListener('click', (e) => {
    const btn = e.target.closest('.nav-tab');
    if (btn) switchView(btn.dataset.view);
  });

  $('currentTurnsSearch').addEventListener('input', renderCurrentTurns);
  $('btnCurrentFullFork').addEventListener('click', () => {
    const cur = findSession('desktop', state.currentConvId);
    if (cur) openForkModal(cur, { targetSurface: 'desktop' });
  });
  $('btnCurrentSyncCli').addEventListener('click', () => {
    const cur = findSession('desktop', state.currentConvId);
    if (cur) openSyncModal(cur);
  });
  $('btnCurrentExport').addEventListener('click', () => {
    const cur = findSession('desktop', state.currentConvId);
    if (cur) exportSession('desktop', cur.id);
  });
  $('btnCurrentRename').addEventListener('click', () => {
    const cur = findSession('desktop', state.currentConvId);
    if (cur) openRenameModal(cur);
  });
  $('btnOpenImport').addEventListener('click', openImportModal);
  $('btnCloseImportModal').addEventListener('click', () => $('importModalBackdrop').classList.add('hidden'));
  $('btnCancelImport').addEventListener('click', () => $('importModalBackdrop').classList.add('hidden'));
  $('importDropzone').addEventListener('click', () => $('importFileInput').click());
  $('importFileInput').addEventListener('change', (e) => handleImportFileSelected(e.target.files?.[0]));
  $('importDropzone').addEventListener('dragover', (e) => { e.preventDefault(); $('importDropzone').classList.add('dragover'); });
  $('importDropzone').addEventListener('dragleave', () => $('importDropzone').classList.remove('dragover'));
  $('importDropzone').addEventListener('drop', (e) => {
    e.preventDefault();
    $('importDropzone').classList.remove('dragover');
    handleImportFileSelected(e.dataTransfer?.files?.[0]);
  });
  $('importForm').addEventListener('change', updateImportConflictWarn);
  $('importForm').addEventListener('submit', submitImport);
  function handleExpandToggle(e) {
    const exp = e.target.closest('[data-toggle-expand]');
    if (!exp) return false;
    const body = exp.previousElementSibling;
    if (body && body.classList.contains('turn-reply')) {
      const collapsed = body.classList.toggle('collapsed');
      exp.textContent = collapsed ? '展开全文' : '收起';
    }
    return true;
  }

  $('currentTurnsList').addEventListener('click', (e) => {
    if (handleExpandToggle(e)) return;
    const btn = e.target.closest('[data-turn-fork]');
    const cur = findSession('desktop', state.currentConvId);
    if (!btn || !cur) return;
    const mode = btn.dataset.turnFork;
    const keepSteps = parseInt(btn.dataset.keep, 10);
    const tnum = btn.dataset.tnum;
    const turnLabel = mode === 'after' ? `截至第 ${tnum} 轮` : `第 ${tnum} 轮之前`;
    openForkModal(cur, { targetSurface: 'desktop', keepSteps, turnLabel });
  });

  $('surfaceTabs').addEventListener('click', (e) => {
    const btn = e.target.closest('.segment');
    if (!btn) return;
    document.querySelectorAll('#surfaceTabs .segment').forEach((b) => b.classList.remove('active'));
    btn.classList.add('active');
    state.surfaceFilter = btn.dataset.surface;
    renderSessions();
  });
  $('projectFilter').addEventListener('change', (e) => { state.projectFilter = e.target.value; renderSessions(); });
  $('searchInput').addEventListener('input', (e) => { state.search = e.target.value; renderSessions(); });
  $('chkHideEmpty').addEventListener('change', (e) => { state.hideEmpty = e.target.checked; renderSessions(); });
  $('chkShowSub').addEventListener('change', (e) => { state.showSub = e.target.checked; renderSessions(); });
  $('btnRefresh').addEventListener('click', () => loadOverview(false));

  $('backupsBody').addEventListener('click', async (e) => {
    const btn = e.target.closest('[data-restore]');
    if (!btn) return;
    const bname = btn.dataset.restore;
    if (!confirm(`确定恢复备份 ${bname}？`)) return;
    btn.disabled = true;
    try {
      await api('/api/backups/restore', { method: 'POST', body: JSON.stringify({ backup_name: bname }) });
      toast('备份已恢复');
      await loadOverview(true);
    } catch (err) {
      toast(err.message, true);
    } finally {
      btn.disabled = false;
    }
  });

  $('sessionList').addEventListener('click', (e) => {
    const el = e.target.closest('[data-act]');
    if (!el) return;
    const act = el.dataset.act;
    const id = el.dataset.id;
    const surface = el.dataset.surface;
    if (act === 'copy-id') return copyText(id, '已复制完整 ID');
    if (act === 'copy-cli') return copyText(`agy --conversation ${id}`, '已复制 agy 命令');
    if (act === 'open') return openDesktopConversation(id);
    const session = findSession(surface, id);
    if (!session) return;
    if (act === 'fork') return openForkModal(session, { targetSurface: session.surface });
    if (act === 'fork-to-desktop') return openForkModal(session, { targetSurface: 'desktop' });
    if (act === 'turns') return openTurnsDrawer(surface, id);
    if (act === 'sync') return openSyncModal(session);
    if (act === 'export') return exportSession(surface, id);
    if (act === 'rename') return openRenameModal(session);
  });

  $('btnCloseTurns').addEventListener('click', () => $('turnsBackdrop').classList.add('hidden'));
  $('turnsBackdrop').addEventListener('click', (e) => { if (e.target === $('turnsBackdrop')) $('turnsBackdrop').classList.add('hidden'); });
  $('turnsSearch').addEventListener('input', () => {
    if (state.turnsContext) renderTurnCards(state.turnsContext.turns, $('turnsSearch').value, $('turnsBody'));
  });
  $('btnFullForkFromDrawer').addEventListener('click', () => {
    if (state.turnsContext?.session) openForkModal(state.turnsContext.session, { targetSurface: 'desktop' });
  });
  $('turnsBody').addEventListener('click', (e) => {
    if (handleExpandToggle(e)) return;
    const btn = e.target.closest('[data-turn-fork]');
    if (!btn || !state.turnsContext?.session) return;
    const mode = btn.dataset.turnFork;
    const keepSteps = parseInt(btn.dataset.keep, 10);
    const tnum = btn.dataset.tnum;
    const turnLabel = mode === 'after' ? `截至第 ${tnum} 轮` : `第 ${tnum} 轮之前`;
    openForkModal(state.turnsContext.session, { targetSurface: 'desktop', keepSteps, turnLabel });
  });

  $('forkForm').addEventListener('submit', submitFork);
  $('btnCloseForkModal').addEventListener('click', () => $('forkModalBackdrop').classList.add('hidden'));
  $('btnCancelFork').addEventListener('click', () => $('forkModalBackdrop').classList.add('hidden'));
  $('btnPickTurnInFork').addEventListener('click', () => {
    const s = state.forkContext?.session;
    $('forkModalBackdrop').classList.add('hidden');
    if (s) openTurnsDrawer(s.surface, s.id);
  });

  $('syncForm').addEventListener('submit', submitSync);
  $('btnCloseSyncModal').addEventListener('click', () => $('syncModalBackdrop').classList.add('hidden'));
  $('btnCancelSync').addEventListener('click', () => $('syncModalBackdrop').classList.add('hidden'));
  $('btnSyncAsFork').addEventListener('click', () => {
    const ctx = state.syncContext;
    $('syncModalBackdrop').classList.add('hidden');
    if (ctx) openForkModal(ctx.session, { targetSurface: ctx.targetSurface });
  });

  $('renameForm').addEventListener('submit', submitRename);
  $('btnCloseRenameModal').addEventListener('click', () => $('renameModalBackdrop').classList.add('hidden'));
  $('btnCancelRename').addEventListener('click', () => $('renameModalBackdrop').classList.add('hidden'));
  $('btnCloseResultModal').addEventListener('click', () => $('resultModalBackdrop').classList.add('hidden'));
  $('resultModalBody').addEventListener('click', (e) => {
    const btn = e.target.closest('[data-copy]');
    if (btn) copyText(btn.dataset.copy, '已复制');
  });

  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') {
      ['resultModalBackdrop', 'importModalBackdrop', 'renameModalBackdrop', 'syncModalBackdrop', 'forkModalBackdrop', 'turnsBackdrop'].forEach(
        (id) => $(id).classList.add('hidden')
      );
      return;
    }
    const tag = (document.activeElement?.tagName || '').toLowerCase();
    if (tag === 'input' || tag === 'textarea' || tag === 'select') return;
    if (e.key === '/') {
      e.preventDefault();
      if (state.activeView === 'current') $('currentTurnsSearch').focus();
      else $('searchInput').focus();
    } else if (e.key === 'r' || e.key === 'R') {
      loadOverview(false);
    }
  });

  window.addEventListener('message', (event) => {
    if (!event.data || typeof event.data !== 'object') return;
    if (event.data.type === 'theme-change') {
      applyHostTheme(event.data.payload);
    } else if (event.data.type === 'conversation-change' && event.data.payload?.conversationId) {
      state.currentConvId = String(event.data.payload.conversationId).toLowerCase();
      loadCurrentSessionView();
      renderSessions();
    }
  });

  switchView(state.activeView);
  loadOverview(true);
})();
