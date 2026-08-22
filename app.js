(function () {
  'use strict';

  const $ = (selector, root) => (root || document).querySelector(selector);
  const $$ = (selector, root) => Array.from((root || document).querySelectorAll(selector));
  const state = { runId: null, timer: null, config: {}, runs: [], selectedCall: 0, tasks: [], batchRunIds: [] };
  const roleNames = ['策划', '研究', '用户', '财务', '风险', '执行', '批判', '验证', '评审', '裁判'];
  const roleDescriptions = ['拆解目标与约束', '补充事实与方案', '代入实际使用者', '量化成本与回报', '寻找失败路径', '转化执行步骤', '检查逻辑与偏差', '验证约束与结果', '比较方案与修正', '独立量表裁决'];
  const colors = ['#6477d7', '#5b9b88', '#bf8354', '#9b72b1', '#4f8ea7', '#c4656f', '#6c7b8d', '#4f9b78', '#8766ad', '#b17c3e'];
  const baselineCaps = {
    B0: [],
    B1: ['vote'],
    B2: ['vote'],
    B3: ['reflect', 'judge'],
    B4: ['vote', 'debate', 'reflect', 'memory', 'judge', 'redistribute']
  };
  const baselineNames = { B0: '单 Agent', B1: '多次采样', B2: '同质并行', B3: '固定流水线', B4: '动态协作' };
  const taskTypeNames = { code_repair: '代码修复', information_integration: '信息整合', dynamic_perturbation: '动态扰动' };
  const modelProfiles = {
    'deepseek-v4-flash': { maxInputTokens: 128000, maxOutputTokens: 8192 }
  };

  function escapeHtml(value) {
    return String(value == null ? '' : value).replace(/[&<>'"]/g, (char) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;' }[char]));
  }
  function formatNumber(value, digits) {
    if (value == null || value === '') return '—';
    const number = Number(value);
    return Number.isFinite(number) ? number.toFixed(digits == null ? 0 : digits) : '—';
  }
  function formatDate(value) {
    if (!value) return '—';
    try { return new Date(value).toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' }); } catch (_) { return value; }
  }
  async function api(path, options) {
    const response = await fetch(path, Object.assign({ headers: { 'Content-Type': 'application/json' } }, options || {}));
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.error || `请求失败 (${response.status})`);
    return data;
  }
  function setBackendStatus(text, type) {
    $('#backendStatus').textContent = text;
    $('#backendDot').style.background = type === 'error' ? 'var(--danger)' : type === 'ok' ? 'var(--teal)' : '#c2702f';
    $('#backendDot').style.boxShadow = type === 'error' ? '0 0 0 3px #ffe7e9' : type === 'ok' ? '0 0 0 3px #e1f5f0' : '0 0 0 3px #fff0df';
  }
  function setError(message) {
    const box = $('#runError');
    box.textContent = message || '';
    box.classList.toggle('show', Boolean(message));
  }
  function showToast(message) {
    const toast = $('#toast');
    toast.textContent = message;
    toast.classList.add('show');
    window.setTimeout(() => toast.classList.remove('show'), 2400);
  }
  function saveLocalConfig() {
    localStorage.setItem('emergence.baseUrl', $('#baseUrl').value.trim());
    localStorage.setItem('emergence.model', $('#modelName').value.trim());
    localStorage.setItem('emergence.timeout', $('#apiTimeout').value.trim());
    localStorage.setItem('emergence.maxInputTokens', $('#maxInputTokens').value.trim());
    localStorage.setItem('emergence.maxOutputTokens', $('#maxOutputTokens').value.trim());
  }
  function loadLocalConfig() {
    const base = localStorage.getItem('emergence.baseUrl');
    const model = localStorage.getItem('emergence.model');
    const timeout = localStorage.getItem('emergence.timeout');
    const maxInputTokens = localStorage.getItem('emergence.maxInputTokens');
    const maxOutputTokens = localStorage.getItem('emergence.maxOutputTokens');
    if (base) $('#baseUrl').value = base;
    if (model) $('#modelName').value = model;
    if (timeout) $('#apiTimeout').value = timeout;
    if (maxInputTokens) $('#maxInputTokens').value = maxInputTokens;
    if (maxOutputTokens) $('#maxOutputTokens').value = maxOutputTokens;
    const profile = modelProfiles[String($('#modelName').value || '').trim().toLowerCase()];
    if (profile) {
      if (!maxInputTokens) $('#maxInputTokens').value = profile.maxInputTokens;
      if (!maxOutputTokens || Number(maxOutputTokens) <= 1600) $('#maxOutputTokens').value = profile.maxOutputTokens;
    }
  }
  function selectedCapabilities() {
    return $$('.capability input:checked').map((input) => input.dataset.cap);
  }
  function currentTask() {
    const id = $('#taskSelect').value;
    return state.tasks.find((task) => task.id === id) || null;
  }
  function renderTaskOptions() {
    const select = $('#taskSelect');
    const groups = [
      { key: 'code_repair', label: '代码修复（12）' },
      { key: 'information_integration', label: '信息整合与规划（10）' },
      { key: 'dynamic_perturbation', label: '动态扰动（8）' }
    ];
    select.innerHTML = '<option value="">不使用任务库 · 自定义问题</option>' + groups.map((group) => {
      const options = state.tasks.filter((task) => task.type === group.key).map((task) => `<option value="${escapeHtml(task.id)}">${escapeHtml(task.id)} · ${escapeHtml(task.title)}</option>`).join('');
      return options ? `<optgroup label="${group.label}">${options}</optgroup>` : '';
    }).join('');
  }
  function applySelectedTask() {
    const task = currentTask();
    if (!task) { $('#taskNote').textContent = '任务库内置 12 个代码修复、10 个信息整合与 8 个动态扰动任务。选择任务后会自动填充研究问题、耦合度与扰动条件。'; return; }
    $('#scenario').value = task.problem || $('#scenario').value;
    $('#couplingSelect').value = task.coupling || 'low';
    $('#perturbSelect').value = task.perturbation || 'none';
    const typeName = taskTypeNames[task.type] || task.type || '综合';
    const roleName = task.role === 'core' ? '核心判别任务' : (task.role === 'boundary' ? '边界任务' : '未分类');
    $('#taskNote').textContent = `${task.id} · ${typeName} · ${task.coupling === 'high' ? '高耦合' : '低耦合'} · ${task.difficulty || '?'} · ${roleName} · ${task.perturbation ? '扰动 ' + task.perturbation : '无扰动'}\n成功标准：${task.success_criteria || '由裁判模型按通用质量标准评估'}`;
    updateHint();
  }
  async function loadTasks() {
    try {
      state.tasks = await api('/api/tasks');
      renderTaskOptions();
    } catch (error) {
      $('#taskNote').textContent = '任务库加载失败：' + error.message;
    }
  }
  function renderRoster() {
    const count = Number($('#agentRange').value || 5);
    $('#agentValue').textContent = count;
    $('#agentChips').innerHTML = roleNames.slice(0, count).map((name) => `<span class="agent-chip selected">${escapeHtml(name)}</span>`).join('');
    $('#agentRoster').innerHTML = roleNames.slice(0, count).map((name, index) => `<div class="agent-row"><div class="agent-meta"><span class="agent-avatar" style="background:${colors[index]}">${escapeHtml(name.slice(0, 1))}</span><div><div class="agent-name">${escapeHtml(name)} Agent</div><div class="agent-role">${escapeHtml(roleDescriptions[index])}</div></div></div><span class="cell-value">${escapeHtml($('#modelName').value || '未设置')}</span><span class="cell-value cell-muted">${index === count - 1 ? '裁判 / 评估' : '协作节点'}</span></div>`).join('');
  }
  function updateHint() {
    const baseline = $('#baselineSelect').value;
    const coupling = $('#couplingSelect').value === 'high' ? '高耦合' : '低耦合';
    const perturb = $('#perturbSelect').value;
    const taskId = $('#taskSelect').value;
    $('#runHint').textContent = `真实运行 · ${baseline} · ${coupling} · ${perturb === 'none' ? '无扰动' : perturb}${taskId ? ' · ' + taskId : ''} · Token 以接口 usage 为准`;
    $('#voteSummary').textContent = baseline === 'B0' ? '单 Agent · 不启用投票' : '等待真实投票';
  }
  function applyBaseline() {
    const baseline = $('#baselineSelect').value;
    const desired = baselineCaps[baseline] || [];
    $$('.capability input').forEach((input) => {
      input.checked = desired.includes(input.dataset.cap);
      input.closest('.capability').classList.toggle('enabled', input.checked);
    });
    $$('.baseline-card').forEach((card) => card.classList.toggle('current', card.dataset.baseline === baseline));
    const notes = {
      B0: 'B0 是单体基础线：结果只来自一次 Agent 调用与独立评估。',
      B1: 'B1 只增加独立采样与投票，用于排除“只是多次采样”的收益。',
      B2: 'B2 让同质 Agent 并行后汇总，用于区分并行集成与协作。',
      B3: 'B3 使用异质角色固定顺序，用于测量预设工作流收益。',
      B4: 'B4 开启共享记忆、辩论、反馈、投票与动态重分配，真实记录协作机制。'
    };
    $('#studyNote').textContent = notes[baseline];
    updateHint();
  }
  function configPayload() {
    return {
      problem: $('#scenario').value.trim(),
      task_id: $('#taskSelect').value,
      baseline: $('#baselineSelect').value,
      agent_count: Number($('#agentRange').value),
      capabilities: selectedCapabilities(),
      coupling: $('#couplingSelect').value,
      perturbation: $('#perturbSelect').value,
      base_url: $('#baseUrl').value.trim(),
      model: $('#modelName').value.trim(),
      api_key: $('#apiKey').value,
      timeout: Number($('#apiTimeout').value || 120),
      max_input_tokens: Number($('#maxInputTokens').value || 128000),
      max_output_tokens: Number($('#maxOutputTokens').value || 8192)
    };
  }
  function setRunning(running) {
    $('#runBtn').disabled = running;
    $('#compareBtn').disabled = running;
    $('#batchBtn').disabled = running;
    $('#resetBtn').disabled = running;
    $('#liveBadge').textContent = running ? '运行中' : ($('#liveBadge').textContent === '运行中' ? '待运行' : $('#liveBadge').textContent);
    if (running) { $('#liveBadge').style.background = 'var(--orange-soft)'; $('#liveBadge').style.color = 'var(--orange)'; }
  }
  async function startRun(mode) {
    if (state.runId) return;
    const payload = configPayload();
    if (!payload.problem) { setError('研究问题不能为空。'); return; }
    if (!payload.model) { $('#settingsPanel').classList.add('open'); setError('请先设置模型名称。'); return; }
    if (mode === 'compare' && !window.confirm('B0–B4 会顺序执行 5 个真实条件，Token 消耗和耗时约为单次的 5 倍。继续吗？')) return;
    setError('');
    state.selectedCall = 0;
    setRunning(true);
    setBackendStatus('实验运行中', 'working');
    $('#liveBadge').textContent = '排队中';
    $('#liveBadge').style.background = 'var(--orange-soft)';
    $('#liveBadge').style.color = 'var(--orange)';
    $('#eventLog').innerHTML = '<div class="log-empty">正在创建运行任务…</div>';
    $('#decisionText').textContent = '模型正在执行协作流程…';
    $('#decisionText').classList.add('is-empty');
    try {
      const created = await api(mode === 'compare' ? '/api/compare' : '/api/runs', { method: 'POST', body: JSON.stringify(payload) });
      state.runId = created.run_id;
      await pollRun();
    } catch (error) {
      state.runId = null;
      setRunning(false);
      setBackendStatus('后端请求失败', 'error');
      setError(error.message);
    }
  }
  async function startBatch(mode) {
    const payload = configPayload();
    const repeats = Number($('#repeatCount').value || 1);
    if (!payload.problem) { setError('研究问题不能为空。'); return; }
    if (!payload.model) { $('#settingsPanel').classList.add('open'); setError('请先设置模型名称。'); return; }
    if (mode === 'compare' && !window.confirm(`将批量执行 B0–B4 对比 ${repeats} 次，共 ${repeats * 5} 个真实条件，Token 消耗和耗时约为单次的 ${repeats * 5} 倍。继续吗？`)) return;
    if (mode === 'single' && !window.confirm(`将对当前条件重复运行 ${repeats} 次，Token 消耗和耗时约为单次的 ${repeats} 倍。继续吗？`)) return;
    setError('');
    setBackendStatus('批量任务创建中', 'working');
    try {
      const created = await api('/api/runs/batch', { method: 'POST', body: JSON.stringify(Object.assign({}, payload, { mode, repeats })) });
      state.batchRunIds = created.run_ids || [];
      showToast(`已创建 ${state.batchRunIds.length} 个批量运行`);
      pollBatch();
    } catch (error) {
      setBackendStatus('批量创建失败', 'error');
      setError(error.message);
    }
  }
  async function pollBatch() {
    if (!state.batchRunIds.length) return;
    try {
      const runs = await api('/api/runs');
      const pending = state.batchRunIds.filter((runId) => {
        const run = runs.find((item) => item.id === runId);
        return run && run.status !== 'completed' && run.status !== 'failed';
      });
      state.runs = runs;
      renderHistory();
      if (pending.length) {
        $('#backendStatus').textContent = `批量运行中 · 剩余 ${pending.length} 个`;
        window.setTimeout(pollBatch, 2000);
      } else {
        const failed = state.batchRunIds.filter((runId) => { const run = runs.find((item) => item.id === runId); return run && run.status === 'failed'; });
        setBackendStatus('后端已连接', 'ok');
        showToast(`批量运行结束 · ${state.batchRunIds.length - failed.length} 成功 / ${failed.length} 失败`);
        state.batchRunIds = [];
      }
    } catch (error) {
      window.setTimeout(pollBatch, 2500);
    }
  }

  async function pollRun() {
    if (!state.runId) return;
    try {
      const run = await api(`/api/runs/${state.runId}`);
      renderLive(run);
      if (run.status === 'queued' || run.status === 'running') {
        state.timer = window.setTimeout(pollRun, 900);
      } else {
        state.runId = null;
        setRunning(false);
        setBackendStatus(run.status === 'completed' ? '后端已连接' : '运行失败', run.status === 'completed' ? 'ok' : 'error');
        await loadHistory();
      }
    } catch (error) {
      state.timer = window.setTimeout(pollRun, 1500);
      setError(error.message);
    }
  }
  function renderEvents(events) {
    const list = $('#eventLog');
    if (!events || !events.length) { list.innerHTML = '<div class="log-empty">等待运行事件。</div>'; return; }
    list.innerHTML = events.slice(-24).reverse().map((event, index) => `<div class="event"><span class="event-dot ${index < 2 ? 'active' : ''}"></span><div class="event-copy"><div class="event-main"><b>${escapeHtml(event.agent || event.kind)}</b> ${escapeHtml(event.message)}</div><div class="event-time">${escapeHtml(formatDate(event.created_at))}${event.tokens ? ` · ${event.tokens} Token` : ''}</div></div></div>`).join('');
  }
  function renderCallDetail(call) {
    const detail = $('#callDetail');
    if (!call) { detail.innerHTML = '<div class="trace-empty">选择左侧调用后查看完整上下文和模型输出。</div>'; return; }
    const tokenText = call.usage_missing ? 'usage 未提供' : `${call.total_tokens || 0} Token · 输入 ${call.prompt_tokens || 0} · 输出 ${call.completion_tokens || 0}`;
    const statusText = call.status === 'empty' ? '空响应（已计费）' : '成功';
    const responseShape = [call.top_level_fields && `顶层: ${call.top_level_fields.join(', ')}`, call.choice_fields && `choice: ${call.choice_fields.join(', ')}`, call.message_fields && `message: ${call.message_fields.join(', ')}`].filter(Boolean).join('\n');
    const requestLimit = call.requested_max_tokens ? `${Number(call.requested_max_tokens).toLocaleString()} Token` : '未记录';
    detail.innerHTML = `<div class="trace-detail-head"><div class="trace-detail-agent">${escapeHtml(call.agent || 'Agent')} <span class="call-seq">#${escapeHtml(call.sequence)}</span></div><div class="trace-detail-meta">${escapeHtml(statusText)} · 第 ${escapeHtml(call.attempt || 1)} 次尝试<br>${escapeHtml(tokenText)}<br>请求上限 ${escapeHtml(requestLimit)} · ${formatNumber(call.duration_seconds, 1)}s</div></div><div class="trace-section-label">Agent 系统角色</div><div class="trace-text">${escapeHtml(call.system || '')}</div><div class="trace-section-label">输入上下文</div><div class="trace-text">${escapeHtml(call.prompt || '')}</div><div class="trace-section-label">模型实际输出${call.content_source ? ` · ${escapeHtml(call.content_source)}` : ''}</div><div class="trace-text">${escapeHtml(call.output || call.error || '（空）')}</div><div class="trace-section-label">响应诊断</div><div class="trace-text">finish_reason: ${escapeHtml(call.finish_reason == null ? '未提供' : call.finish_reason)}\n${escapeHtml(responseShape || '响应结构未记录')}${call.status === 'empty' && call.response_preview ? `\n\n安全截断预览:\n${escapeHtml(call.response_preview)}` : ''}</div><div class="trace-section-label">请求地址</div><div class="trace-text">${escapeHtml(call.endpoint || '—')}</div>`;
  }
  function renderCalls(calls) {
    const list = $('#callList');
    const items = Array.isArray(calls) ? calls : [];
    if (!items.length) { $('#traceSummary').textContent = '暂无 Agent 调用'; list.innerHTML = '<div class="trace-empty">暂无 Agent 调用。</div>'; renderCallDetail(null); return; }
    const total = items.reduce((sum, item) => sum + Number(item.total_tokens || 0), 0);
    const missing = items.filter((item) => item.usage_missing).length;
    $('#traceSummary').textContent = `${items.length} 次模型调用 · ${missing ? '部分 usage 未提供' : `${total.toLocaleString()} Token`}`;
    if (state.selectedCall >= items.length) state.selectedCall = items.length - 1;
    list.innerHTML = items.map((call, index) => `<button type="button" class="call-item ${index === state.selectedCall ? 'selected' : ''}" data-call-index="${index}"><div class="call-item-top"><span class="call-agent">${escapeHtml(call.agent || 'Agent')}</span><span class="call-seq">#${escapeHtml(call.sequence || index + 1)}</span></div><div class="call-item-meta"><span>${call.status === 'empty' ? '空响应 · ' : ''}${call.usage_missing ? 'usage 未提供' : `${Number(call.total_tokens || 0).toLocaleString()} Token`}</span><span>${formatNumber(call.duration_seconds, 1)}s</span></div></button>`).join('');
    $$('#callList .call-item').forEach((button) => button.addEventListener('click', () => { state.selectedCall = Number(button.dataset.callIndex); $$('#callList .call-item').forEach((item) => item.classList.remove('selected')); button.classList.add('selected'); renderCallDetail(items[state.selectedCall]); }));
    renderCallDetail(items[state.selectedCall]);
  }
  function renderVotes(result) {
    const votes = result && result.votes ? result.votes : [];
    const candidates = result && result.candidates ? result.candidates : [];
    if (!votes.length) { $('#voteSummary').textContent = '未启用投票或没有有效投票'; $('#voteBars').innerHTML = '<div class="log-empty">此条件没有产生有效投票。</div>'; return; }
    const counts = {};
    votes.forEach((vote) => { const choice = Number(vote.choice || 1); counts[choice] = (counts[choice] || 0) + 1; });
    const total = votes.length;
    $('#voteSummary').textContent = `${total} 份真实投票已提交`;
    $('#voteBars').innerHTML = Object.keys(counts).sort((a, b) => counts[b] - counts[a]).slice(0, 5).map((choice, index) => { const pct = Math.round(counts[choice] / total * 100); const label = candidates[Number(choice) - 1] ? candidates[Number(choice) - 1].agent : `候选 ${choice}`; return `<div class="vote-row"><span class="vote-agent">${escapeHtml(label)}</span><span class="bar-track"><i class="bar-fill ${index ? 'alt' : ''}" style="width:${pct}%"></i></span><span class="vote-pct">${pct}%</span></div>`; }).join('');
  }
  function renderEvidence(evidence) {
    const keys = ['non_preconfigured', 'system_gain', 'interaction_dependency', 'repeatability', 'traceable'];
    let count = 0;
    $$('#evidenceList .evidence').forEach((item, index) => {
      const value = evidence ? evidence[keys[index]] : null;
      const pass = value === true;
      const unknown = value == null;
      if (pass) count += 1;
      item.classList.toggle('pass', pass);
      item.querySelector('.evidence-mark').textContent = pass ? '✓' : unknown ? '?' : '—';
    });
    const totalKnown = evidence ? keys.filter((key) => evidence[key] != null).length : 0;
    $('#emergenceScore').textContent = `${count} / 5 · ${count === 5 ? '可识别' : totalKnown ? '证据未齐' : '未检验'}`;
    $('#emergenceScore').style.color = count === 5 ? 'var(--teal)' : 'var(--orange)';
  }
  function renderProcess(events) {
    const counts = { Plan: 0, Delegate: 0, Share: 0, Critique: 0, Revise: 0, Verify: 0, Negotiate: 0, Loop: 0 };
    (events || []).forEach((event) => {
      const agent = event.agent || '';
      if (event.kind === 'reassign') { counts.Delegate += 1; counts.Negotiate += 1; }
      else if (event.kind === 'debate') counts.Critique += 1;
      else if (event.kind === 'reflect') counts.Revise += 1;
      else if (event.kind === 'evaluation') counts.Verify += 1;
      else if (event.kind === 'agent_error' || event.kind === 'vote_error') counts.Loop += 1;
      else if (event.kind === 'agent_complete' && /规划|策划|planner/i.test(agent)) counts.Plan += 1;
      else if (event.kind === 'agent_complete' && /共享|研究|用户|成本/i.test(agent)) counts.Share += 1;
    });
    const values = Object.values(counts);
    const max = Math.max(1, ...values);
    $$('#processGrid .process-item').forEach((item, index) => { const value = values[index]; item.querySelector('.process-label span').textContent = value; item.querySelector('.process-fill').style.width = `${Math.round(value / max * 100)}%`; });
  }
  function renderComparisonCards(result) {
    const comparisons = result && result.comparisons ? result.comparisons : [];
    comparisons.forEach((comparison) => {
      const card = $(`.baseline-card[data-baseline="${comparison.baseline}"]`);
      if (!card) return;
      const score = comparison.evaluation && comparison.evaluation.score;
      const usage = comparison._usage || {};
      card.querySelector('.baseline-value strong').textContent = score == null ? '—' : formatNumber(score, 2);
      card.querySelector('.baseline-cost').textContent = usage.total_tokens ? `${formatNumber(usage.total_tokens / 1000, 1)}k Token` : usage.usage_missing ? 'usage 未提供' : '0 Token';
    });
  }
  function renderLive(run) {
    $('#runId').textContent = run.id || '—';
    $('#traceRunId').textContent = run.id || '—';
    renderEvents(run.events || []);
    const resultCalls = run.result && run.result.comparisons ? run.result.comparisons.flatMap((comparison) => (comparison.calls || []).map((call) => Object.assign({}, call, { agent: `${comparison.baseline} · ${call.agent || 'Agent'}` }))) : (run.calls || (run.result && run.result.calls) || []);
    renderCalls(resultCalls);
    renderProcess(run.events || []);
    if (run.status === 'queued' || run.status === 'running') {
      $('#liveBadge').textContent = run.status === 'queued' ? '排队中' : '运行中';
      return;
    }
    if (run.status === 'failed') {
      setError(run.error || '运行失败');
      $('#liveBadge').textContent = '失败';
      $('#liveBadge').style.background = '#fff0f1';
      $('#liveBadge').style.color = 'var(--danger)';
      return;
    }
    const result = run.result || {};
    const evaluation = result.evaluation || {};
    const score = evaluation.score;
    $('#liveBadge').textContent = '已完成';
    $('#liveBadge').style.background = 'var(--teal-soft)';
    $('#liveBadge').style.color = 'var(--teal)';
    $('#decisionText').textContent = result.final_answer || '模型没有返回最终答案。';
    $('#decisionText').classList.remove('is-empty');
    $('#judgeScore').textContent = score == null ? '—' : `${formatNumber(score, 2)} / 100`;
    $('#qualityValue').textContent = score == null ? '—' : formatNumber(score, 2);
    $('#qualityFill').style.width = score == null ? '0' : `${Math.max(0, Math.min(100, score))}%`;
    $('#confidence').textContent = evaluation.confidence == null ? '—' : formatNumber(evaluation.confidence, 2);
    $('#promptTokens').textContent = run.usage && run.usage.prompt_tokens ? run.usage.prompt_tokens.toLocaleString() : run.usage && run.usage.usage_missing ? '未提供' : '0';
    $('#completionTokens').textContent = run.usage && run.usage.completion_tokens ? run.usage.completion_tokens.toLocaleString() : run.usage && run.usage.usage_missing ? '未提供' : '0';
    $('#totalTokens').textContent = run.usage && run.usage.total_tokens ? run.usage.total_tokens.toLocaleString() : run.usage && run.usage.usage_missing ? '未提供' : '0';
    $('#elapsed').textContent = run.duration_seconds == null ? '—' : `${formatNumber(run.duration_seconds, 1)}s`;
    renderVotes(result);
    renderEvidence(result.emergence_evidence);
    renderComparisonCards(result);
    if (result.comparisons && result.comparisons.length) $('#studyNote').textContent = 'B0–B4 对比已完成。卡片中的 Q 与 Token 均来自本次真实运行，不复用论文数字。';
  }
  function statusTag(status) {
    const text = status === 'completed' ? '已完成' : status === 'failed' ? '失败' : status === 'running' ? '运行中' : '排队中';
    const cls = status === 'completed' ? 'green' : status === 'failed' ? 'gray' : 'blue';
    return `<span class="tag ${cls}">${text}</span>`;
  }
  function renderHistory() {
    const filter = $('#filterSelect').value;
    const rows = state.runs.filter((run) => filter === 'all' || run.status === filter);
    if (!rows.length) { $('#recordBody').innerHTML = '<tr id="emptyRow"><td colspan="8" class="text-center cell-muted">暂无符合条件的真实实验记录</td></tr>'; return; }
    $('#recordBody').innerHTML = rows.map((run) => { const config = run.config || {}; const baseline = run.mode === 'compare' ? 'B0–B4' : run.baseline; const count = run.baseline === 'B0' ? 1 : (config.agent_count || '—'); const tokens = run.usage && run.usage.total_tokens ? `${(run.usage.total_tokens / 1000).toFixed(1)}k` : run.usage && run.usage.usage_missing ? '未提供' : '0'; const score = run.score == null ? '—' : formatNumber(run.score, 2); return `<tr data-run-id="${escapeHtml(run.id)}"><td><div class="exp-name">${escapeHtml(run.id)}</div><div class="exp-sub">${escapeHtml(formatDate(run.created_at))}</div></td><td>${statusTag(run.status)}</td><td><span class="tag ${baseline === 'B4' ? 'green' : 'blue'}">${escapeHtml(baseline)}</span></td><td>${escapeHtml(count)}</td><td>${escapeHtml(tokens)}</td><td>${escapeHtml(score)}</td><td>${run.duration_seconds == null ? '—' : `${formatNumber(run.duration_seconds, 1)}s`}</td><td>${escapeHtml(config.model || '—')}</td></tr>`; }).join('');
    $$('#recordBody tr[data-run-id]').forEach((row) => row.addEventListener('click', async () => { try { const run = await api(`/api/runs/${row.dataset.runId}`); renderLive(run); window.scrollTo({ top: 0, behavior: 'smooth' }); } catch (error) { setError(error.message); } }));
  }
  async function loadHistory() { try { state.runs = await api('/api/runs'); renderHistory(); } catch (error) { setBackendStatus('历史读取失败', 'error'); } }
  async function exportHistory() {
    try {
      const response = await fetch('/api/export?format=csv');
      if (!response.ok) { const data = await response.json().catch(() => ({})); throw new Error(data.error || `导出失败 (${response.status})`); }
      const blob = await response.blob();
      const link = document.createElement('a'); link.href = URL.createObjectURL(blob); link.download = 'emergence-runs.csv'; link.click(); window.setTimeout(() => URL.revokeObjectURL(link.href), 1000); showToast('完整运行记录已导出');
    } catch (error) { setError(error.message); }
  }
  async function bootstrap() {
    loadLocalConfig();
    try {
      const config = await api('/api/config');
      state.config = config;
      if (!localStorage.getItem('emergence.baseUrl')) $('#baseUrl').value = config.base_url;
      if (!localStorage.getItem('emergence.model')) $('#modelName').value = config.model;
      if (!localStorage.getItem('emergence.maxInputTokens')) $('#maxInputTokens').value = config.max_input_tokens || 128000;
      if (!localStorage.getItem('emergence.maxOutputTokens')) $('#maxOutputTokens').value = config.max_output_tokens || 8192;
      setBackendStatus(config.has_server_key ? '后端已连接 · 服务端密钥' : '后端已连接 · 等待密钥', 'ok');
      $('#settingsStatus').textContent = config.has_server_key ? '服务端已发现 API Key；也可以在这里临时覆盖。' : '密钥仅用于本次浏览器会话，不写入数据库。';
      $('#settingsStatus').classList.add('ok');
    } catch (error) { setBackendStatus('后端未连接', 'error'); setError(error.message); }
    renderRoster(); applyBaseline(); await Promise.all([loadTasks(), loadHistory()]);
  }

  $('#modelBtn').addEventListener('click', () => $('#settingsPanel').classList.toggle('open'));
  $('#closeSettings').addEventListener('click', () => $('#settingsPanel').classList.remove('open'));
  $('#saveModelSettings').addEventListener('click', () => { saveLocalConfig(); renderRoster(); $('#settingsPanel').classList.remove('open'); showToast('模型连接设置已应用'); });
  $('#modelName').addEventListener('change', () => {
    const profile = modelProfiles[$('#modelName').value.trim().toLowerCase()];
    if (!profile) return;
    $('#maxInputTokens').value = profile.maxInputTokens;
    $('#maxOutputTokens').value = profile.maxOutputTokens;
  });
  $('#taskSelect').addEventListener('change', applySelectedTask);
  $('#batchBtn').addEventListener('click', () => startBatch('single'));
  $('#baselineSelect').addEventListener('change', applyBaseline);
  $('#agentRange').addEventListener('input', () => { renderRoster(); updateHint(); });
  $('#couplingSelect').addEventListener('change', updateHint);
  $('#perturbSelect').addEventListener('change', updateHint);
  $$('.capability input').forEach((input) => input.addEventListener('change', () => { input.closest('.capability').classList.toggle('enabled', input.checked); }));
  $('#resetBtn').addEventListener('click', () => { $('#baselineSelect').value = 'B4'; $('#agentRange').value = 7; applyBaseline(); renderRoster(); showToast('已恢复 B4 能力配置'); });
  $('#runBtn').addEventListener('click', () => startRun('single'));
  $('#compareBtn').addEventListener('click', () => startRun('compare'));
  $('#filterSelect').addEventListener('change', renderHistory);
  $('#exportBtn').addEventListener('click', exportHistory);
  $$('.nav button').forEach((button) => button.addEventListener('click', () => { $$('.nav button').forEach((item) => item.classList.remove('active')); button.classList.add('active'); if (button.textContent.includes('运行记录')) document.querySelector('.compare').scrollIntoView({ behavior: 'smooth' }); else if (button.textContent.includes('实验台')) window.scrollTo({ top: 0, behavior: 'smooth' }); }));
  bootstrap();
}());
