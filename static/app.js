/**
 * arXiv CS.CL Paper Matcher — SPA JS Engine
 * Handled via FastAPI REST APIs & Live Server-Sent Events (SSE) streaming.
 */

document.addEventListener('DOMContentLoaded', () => {
  const app = {
    currentResults: [],
    currentSchedules: [],
    pastEvaluations: [],

    init() {
      this.bindTabNavigation();
      this.bindFormControls();
      this.fetchConfig();
      this.loadSchedules();
      this.loadHistory();
    },

    // ── Navigation ──
    bindTabNavigation() {
      // Main Page Tabs
      const tabs = document.querySelectorAll('.tab-btn');
      tabs.forEach(tab => {
        tab.addEventListener('click', () => {
          tabs.forEach(t => t.classList.remove('active'));
          document.querySelectorAll('.tab-pane').forEach(p => p.classList.remove('active'));

          tab.classList.add('active');
          const paneId = tab.getAttribute('data-tab');
          const pane = document.getElementById(paneId);
          if (pane) pane.classList.add('active');

          // Refresh data on tab switch
          if (paneId === 'tab-history') {
            this.loadHistory();
          } else if (paneId === 'tab-recurring') {
            this.loadSchedules();
          }
        });
      });

      // Subtabs in results
      const subtabs = document.querySelectorAll('.subtab-btn');
      subtabs.forEach(st => {
        st.addEventListener('click', () => {
          subtabs.forEach(t => t.classList.remove('active'));
          document.querySelectorAll('.subtab-pane').forEach(p => p.classList.remove('active'));

          st.classList.add('active');
          const paneId = st.getAttribute('data-subtab');
          const pane = document.getElementById(paneId);
          if (pane) pane.classList.add('active');
        });
      });
    },

    // ── Config ──
    async fetchConfig() {
      try {
        const res = await fetch('/api/config');
        const data = await res.json();

        const keyBadge = document.getElementById('status-api-key');
        if (data.has_api_key) {
          keyBadge.textContent = 'API key loaded from Secret Manager';
        } else {
          keyBadge.textContent = '⚠️ API Key Missing';
        }

        const cloudBadge = document.getElementById('status-cloud-sync');
        if (data.gcs_bucket || data.s3_bucket) {
          cloudBadge.textContent = `Persistent DB: ${data.gcs_bucket || data.s3_bucket}`;
        }
      } catch (e) { console.error('Config fetch error:', e); }
    },

    // ── Controls & Sliders ──
    bindFormControls() {
      const bindVal = (id, targetId) => {
        const input = document.getElementById(id);
        const target = document.getElementById(targetId);
        if (input && target) {
          input.addEventListener('input', () => { target.textContent = input.value; });
        }
      };

      bindVal('max-papers', 'val-max-papers');
      bindVal('days-back', 'val-days-back');
      bindVal('min-score', 'val-min-score');
      bindVal('max-concurrent', 'val-max-concurrent');

      // Fetch Mode Segmented Control
      document.querySelectorAll('input[name="fetchmode"]').forEach(radio => {
        radio.addEventListener('change', (e) => {
          const isCount = e.target.value === 'count';
          document.getElementById('group-paper-count').classList.toggle('hidden', !isCount);
          document.getElementById('group-days-back').classList.toggle('hidden', isCount);
        });
      });

      // Evaluation Action Buttons
      document.getElementById('btn-run-all').addEventListener('click', () => this.startLiveStreamEvaluation());

      document.getElementById('btn-clear-results').addEventListener('click', () => {
        this.currentResults = [];
        document.getElementById('dashboard-container').classList.add('hidden');
        document.getElementById('progress-card').classList.add('hidden');
      });

      document.getElementById('btn-add-recurring').addEventListener('click', () => {
        const problem = document.getElementById('problem-statement').value.trim();
        if (!problem) return alert('Please describe your research problem first.');

        // Switch to recurring tab
        document.querySelector('.tab-btn[data-tab="tab-recurring"]').click();
        document.getElementById('sch-problem').value = problem;
      });

      // Export JSON
      document.getElementById('btn-export-json').addEventListener('click', () => {
        if (!this.currentResults.length) return;
        const blob = new Blob([JSON.stringify(this.currentResults, null, 2)], { type: 'application/json' });
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = 'arxiv_paper_matches.json';
        a.click();
      });

      // History Search & Filter Inputs
      const historySearch = document.getElementById('history-search');
      const historyFilterScore = document.getElementById('history-filter-score');
      if (historySearch) historySearch.addEventListener('input', () => this.renderHistory());
      if (historyFilterScore) historyFilterScore.addEventListener('change', () => this.renderHistory());

      // Create Schedule Form
      document.getElementById('form-schedule').addEventListener('submit', async (e) => {
        e.preventDefault();
        const payload = {
          label: document.getElementById('sch-label').value.trim(),
          problem_text: document.getElementById('sch-problem').value.trim(),
          model_name: document.getElementById('sch-model').value,
          run_time: document.getElementById('sch-time').value,
          max_papers: 50,
          fetch_mode: 'count',
        };
        await fetch('/api/schedules', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(payload),
        });
        alert('Created recurring schedule!');
        document.getElementById('form-schedule').reset();
        this.loadSchedules();
      });

      // Edit Schedule Modal Form
      document.getElementById('form-edit-schedule').addEventListener('submit', async (e) => {
        e.preventDefault();
        const id = document.getElementById('edit-sch-id').value;
        const payload = {
          label: document.getElementById('edit-sch-label').value.trim(),
          problem_text: document.getElementById('edit-sch-problem').value.trim(),
          model_name: document.getElementById('edit-sch-model').value,
          run_time: document.getElementById('edit-sch-time').value,
          max_papers: 50,
          fetch_mode: 'count',
        };
        await fetch(`/api/schedules/${id}`, {
          method: 'PUT',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(payload),
        });
        document.getElementById('modal-edit-schedule').classList.add('hidden');
        this.loadSchedules();
      });

      document.getElementById('btn-cancel-edit').addEventListener('click', () => {
        document.getElementById('modal-edit-schedule').classList.add('hidden');
      });
    },

    // ── Live Stream Evaluation (SSE) ──
    async startLiveStreamEvaluation() {
      const problem = document.getElementById('problem-statement').value.trim();
      if (!problem) return alert('Please describe your research problem.');

      const model = document.getElementById('model-name').value;
      const fetchMode = document.querySelector('input[name="fetchmode"]:checked').value;
      const maxPapers = fetchMode === 'count' ? parseInt(document.getElementById('max-papers').value) : null;
      const daysBack = fetchMode === 'days' ? parseInt(document.getElementById('days-back').value) : null;
      const keyword = document.getElementById('keyword-filter').value.trim();
      const concurrent = parseInt(document.getElementById('max-concurrent').value);

      const progressCard = document.getElementById('progress-card');
      const progressStage = document.getElementById('progress-stage');
      const progressFill = document.getElementById('progress-fill');
      const dashboard = document.getElementById('dashboard-container');

      progressCard.classList.remove('hidden');
      progressStage.textContent = 'Fetching papers from arXiv CS.CL...';
      progressFill.style.width = '0%';
      dashboard.classList.add('hidden');
      this.currentResults = [];

      try {
        const response = await fetch('/api/evaluate/stream', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            problem_statement: problem,
            model_name: model,
            max_papers: maxPapers,
            days_back: daysBack,
            keyword_filter: keyword,
            max_concurrent: concurrent,
          }),
        });

        if (!response.ok) {
          const err = await response.json();
          progressStage.textContent = `❌ Error: ${err.detail || 'Evaluation failed.'}`;
          return;
        }

        const reader = response.body.getReader();
        const decoder = new TextDecoder('utf-8');
        let buffer = '';

        while (true) {
          const { done, value } = await reader.read();
          if (done) break;

          buffer += decoder.decode(value, { stream: true });
          const lines = buffer.split('\n');
          buffer = lines.pop();

          let currentEvent = 'message';
          for (const line of lines) {
            if (line.startsWith('event:')) {
              currentEvent = line.replace('event:', '').trim();
            } else if (line.startsWith('data:')) {
              const dataStr = line.replace('data:', '').trim();
              if (dataStr) {
                try {
                  const data = JSON.parse(dataStr);
                  this.handleStreamEvent(currentEvent, data, progressStage, progressFill);
                } catch (err) {
                  console.error('SSE Error:', err);
                }
              }
            }
          }
        }
      } catch (e) {
        progressStage.textContent = `❌ Stream error: ${e.message}`;
      }
    },

    handleStreamEvent(event, data, progressStage, progressFill) {
      if (event === 'stage') {
        progressStage.textContent = data.message;
      } else if (event === 'paper_start') {
        const pct = Math.round((data.paper_index / data.total_papers) * 100);
        progressFill.style.width = `${pct}%`;
        progressStage.textContent = `Evaluating paper ${data.paper_index} / ${data.total_papers}: "${data.title.substring(0, 60)}..."`;
      } else if (event === 'paper_done') {
        this.currentResults.push(data);
      } else if (event === 'eval_complete') {
        progressStage.textContent = `Completed evaluation of ${data.total_evaluated} papers!`;
        progressFill.style.width = '100%';
        this.renderResultsDashboard();
        // Immediately reload history so newly stored evaluation appears
        this.loadHistory();
      }
    },

    // ── Results Dashboard ──
    renderResultsDashboard() {
      const results = this.currentResults;
      document.getElementById('dashboard-container').classList.remove('hidden');

      document.getElementById('metric-total').textContent = results.length;
      document.getElementById('metric-high').textContent = results.filter(r => r.avg_score >= 7).length;
      document.getElementById('metric-mid').textContent = results.filter(r => r.avg_score >= 4 && r.avg_score < 7).length;
      document.getElementById('metric-low').textContent = results.filter(r => r.avg_score < 4).length;

      const minScore = parseInt(document.getElementById('min-score').value);
      this.renderPaperCards('papers-list-all', results, minScore);
      this.renderPaperCards('papers-list-top', results.filter(r => r.avg_score >= minScore), minScore);
      this.renderDebateDetails('papers-list-debate', results);
    },

    renderPaperCards(containerId, list, minScore) {
      const container = document.getElementById(containerId);
      container.innerHTML = '';

      if (!list.length) {
        container.innerHTML = '<p style="opacity:.6">No papers match this threshold.</p>';
        return;
      }

      list.forEach(r => {
        const scoreTier = r.avg_score >= 7 ? 'high' : r.avg_score >= 4 ? 'mid' : 'low';
        const isHighlight = r.avg_score >= minScore;
        const chipsHtml = (r.judge_scores || []).map(j => {
          const cls = j.score >= 7 ? 'high' : j.score >= 4 ? 'mid' : 'low';
          return `<span class="judge-chip ${cls}">J${j.run}: ${j.score}</span>`;
        }).join(' ');

        // Advocate vs Skeptic Debate Transcripts
        let debatesHtml = '';
        if (r.rounds && r.rounds.length) {
          debatesHtml = r.rounds.map((rnd, i) => `
            <div class="debate-panel debate-advocate" style="margin-bottom:8px">
              <div style="font-weight:800;font-size:12px;color:oklch(38% 0.15 155);margin-bottom:4px">🟢 Advocate (Round ${i + 1})</div>
              <div style="font-size:13px">${rnd.advocate}</div>
            </div>
            <div class="debate-panel debate-skeptic" style="margin-bottom:8px">
              <div style="font-weight:800;font-size:12px;color:var(--color-accent);margin-bottom:4px">🔴 Skeptic (Round ${i + 1})</div>
              <div style="font-size:13px">${rnd.skeptic}</div>
            </div>
          `).join('');
        }

        // 5-Judge Panel Transcripts
        let judgesHtml = '';
        if (r.judge_scores && r.judge_scores.length) {
          judgesHtml = r.judge_scores.map(j => {
            const cls = j.score >= 7 ? 'high' : j.score >= 4 ? 'mid' : 'low';
            const reasonsList = Array.isArray(j.reasons) ? j.reasons.map(reason => `<li>${reason}</li>`).join('') : '';
            return `
              <div class="debate-panel" style="background:var(--color-surface);border-left:3px solid var(--color-accent);margin-bottom:8px">
                <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:4px">
                  <strong style="font-size:13px">⚖️ Judge #${j.run} (Seed: ${j.seed || 'N/A'})</strong>
                  <span class="score-badge ${cls}" style="font-size:12px;padding:2px 6px">${j.score}/10</span>
                </div>
                <div style="font-size:12.5px;margin-bottom:4px"><strong>Verdict:</strong> ${j.verdict || 'N/A'}</div>
                ${reasonsList ? `<ul style="margin:4px 0 4px 16px;padding:0;font-size:12px;opacity:.85">${reasonsList}</ul>` : ''}
                ${j.suggested_use ? `<div style="font-size:12px;opacity:.8"><strong>Suggested Use:</strong> ${j.suggested_use}</div>` : ''}
              </div>
            `;
          }).join('');
        }

        const card = document.createElement('div');
        card.className = `card ${isHighlight ? 'paper-card-highlight' : ''}`;
        card.innerHTML = `
          <div style="display:flex;justify-content:space-between;align-items:flex-start;gap:12px">
            <div>
              <div style="font-family:var(--font-heading);font-weight:800;font-size:16px">${r.title}</div>
              <div style="font-size:12px;opacity:.6;margin-top:2px">👤 ${r.authors || 'Unknown'} · 📅 ${r.published || ''}</div>
            </div>
            <span class="score-badge ${scoreTier}">${r.avg_score}/10</span>
          </div>

          <div style="display:flex;align-items:center;gap:6px;margin:4px 0">
            ${chipsHtml}
            <span style="font-size:12px;opacity:.7">&rarr; Avg: ${r.avg_score}</span>
          </div>

          <p style="font-size:13.5px;margin:0"><strong>Verdict:</strong> ${r.verdict}</p>
          ${r.suggested_use ? `<p style="font-size:12.5px;opacity:.8;margin:0"><strong>Suggested Use:</strong> ${r.suggested_use}</p>` : ''}

          <div style="display:flex;justify-content:space-between;align-items:center;margin-top:8px">
            <a href="${r.url}" target="_blank" class="btn btn-ghost">Open Paper &rarr;</a>
          </div>

          <details style="margin-top:8px">
            <summary style="cursor:pointer;font-size:13px;font-weight:800;color:var(--color-accent)">▼ Multi-Agent Debates &amp; 5-Judge Panel Transcripts</summary>
            <div style="margin-top:10px;display:flex;flex-direction:column;gap:12px">
              ${debatesHtml ? `<div><h5 style="margin:0 0 6px">🗣️ Advocate vs. Skeptic Debates</h5>${debatesHtml}</div>` : ''}
              ${judgesHtml ? `<div><h5 style="margin:8px 0 6px">⚖️ 5-Judge Panel Individual Verdicts</h5>${judgesHtml}</div>` : ''}
            </div>
          </details>
        `;
        container.appendChild(card);
      });
    },

    renderDebateDetails(containerId, list) {
      const container = document.getElementById(containerId);
      container.innerHTML = '';
      list.forEach(r => {
        const card = document.createElement('div');
        card.className = 'card';
        card.innerHTML = `
          <div style="font-weight:800;font-size:16px">[${r.avg_score}/10] ${r.title}</div>
          <p style="font-size:13px;margin:4px 0">${r.verdict}</p>
        `;
        container.appendChild(card);
      });
    },

    // ── Schedules List ──
    async loadSchedules() {
      try {
        const res = await fetch('/api/schedules');
        const data = await res.json();
        this.currentSchedules = data.schedules || [];
        this.renderSchedules();
      } catch (e) { console.error('Schedules error:', e); }
    },

    renderSchedules() {
      const container = document.getElementById('schedules-list');
      container.innerHTML = '';

      if (!this.currentSchedules.length) {
        container.innerHTML = '<p style="opacity:.6">No recurring schedules created yet.</p>';
        return;
      }

      this.currentSchedules.forEach(s => {
        const statusDot = s.is_active ? '🟢' : '⚪';
        const card = document.createElement('div');
        card.className = 'card';
        card.innerHTML = `
          <div style="display:flex;justify-content:space-between;align-items:center">
            <div style="font-weight:800;font-size:15px">${statusDot} #${s.id} · ${s.label || `Schedule #${s.id}`} · ${s.run_time} daily</div>
            <div style="display:flex;gap:6px">
              <button class="btn btn-secondary btn-run-sch" data-id="${s.id}">Run Now</button>
              <button class="btn btn-secondary btn-toggle-sch" data-id="${s.id}" data-active="${s.is_active}">${s.is_active ? 'Pause' : 'Activate'}</button>
              <button class="btn btn-secondary btn-edit-sch" data-id="${s.id}">Edit</button>
              <button class="btn btn-secondary btn-del-sch" data-id="${s.id}" style="color:var(--color-accent)">Delete</button>
            </div>
          </div>
          <div style="font-size:12.5px;opacity:.7">Model: ${s.model_name} | Fetch: ${s.fetch_mode === 'count' ? `${s.max_papers} papers` : `${s.days_back} days`}</div>
          <div style="font-size:13px">${s.problem_text}</div>
        `;
        container.appendChild(card);
      });

      container.querySelectorAll('.btn-run-sch').forEach(b => b.addEventListener('click', e => {
        const id = e.target.getAttribute('data-id');
        fetch(`/api/schedules/${id}/run`, { method: 'POST' });
        alert(`Triggered background run for schedule #${id}!`);
      }));

      container.querySelectorAll('.btn-toggle-sch').forEach(b => b.addEventListener('click', async e => {
        const id = e.target.getAttribute('data-id');
        const active = e.target.getAttribute('data-active') === '1';
        await fetch(`/api/schedules/${id}/toggle`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ active: !active }),
        });
        this.loadSchedules();
      }));

      container.querySelectorAll('.btn-del-sch').forEach(b => b.addEventListener('click', async e => {
        const id = e.target.getAttribute('data-id');
        if (confirm('Delete schedule?')) {
          await fetch(`/api/schedules/${id}`, { method: 'DELETE' });
          this.loadSchedules();
        }
      }));

      container.querySelectorAll('.btn-edit-sch').forEach(b => b.addEventListener('click', e => {
        const id = parseInt(e.target.getAttribute('data-id'));
        const sch = this.currentSchedules.find(s => s.id === id);
        if (sch) {
          document.getElementById('edit-sch-id').value = sch.id;
          document.getElementById('edit-sch-label').value = sch.label || '';
          document.getElementById('edit-sch-problem').value = sch.problem_text;
          document.getElementById('edit-sch-model').value = sch.model_name;
          document.getElementById('edit-sch-time').value = sch.run_time;
          document.getElementById('modal-edit-schedule').classList.remove('hidden');
        }
      }));
    },

    // ── History List ──
    async loadHistory() {
      try {
        const res = await fetch('/api/evaluations');
        const data = await res.json();
        this.pastEvaluations = data.evaluations || [];
        this.renderHistory();
      } catch (e) { console.error('History error:', e); }
    },

    renderHistory() {
      const container = document.getElementById('history-list');
      container.innerHTML = '';

      const searchVal = (document.getElementById('history-search')?.value || '').toLowerCase().trim();
      const scoreFilter = document.getElementById('history-filter-score')?.value || 'all';

      let list = [...this.pastEvaluations];

      if (searchVal) {
        list = list.filter(ev =>
          (ev.problem_text || '').toLowerCase().includes(searchVal) ||
          (ev.model_name || '').toLowerCase().includes(searchVal) ||
          String(ev.id).includes(searchVal)
        );
      }

      if (scoreFilter === 'high') {
        list = list.filter(ev => (ev.overall_avg || 0) >= 7);
      } else if (scoreFilter === 'mid') {
        list = list.filter(ev => (ev.overall_avg || 0) >= 4 && (ev.overall_avg || 0) < 7);
      } else if (scoreFilter === 'low') {
        list = list.filter(ev => (ev.overall_avg || 0) < 4);
      }

      if (!list.length) {
        container.innerHTML = '<p style="opacity:.6">No past evaluations match your filter.</p>';
        return;
      }

      list.forEach(ev => {
        const card = document.createElement('div');
        card.className = 'card';
        card.innerHTML = `
          <div style="display:flex;justify-content:space-between;align-items:center">
            <div style="font-weight:800;font-size:15px">Eval #${ev.id} — Overall: ${ev.overall_avg || 0}/10</div>
            <button class="btn btn-secondary btn-del-eval" data-id="${ev.id}" style="color:var(--color-accent)">Delete</button>
          </div>
          <div style="font-size:13.5px"><strong>Problem:</strong> ${ev.problem_text}</div>
          <div style="font-size:12px;opacity:.6">Model: ${ev.model_name} | Date: ${ev.created_at} | Papers Evaluated: ${ev.paper_count}</div>

          <details style="margin-top:8px" class="past-eval-details" data-id="${ev.id}">
            <summary style="cursor:pointer;font-size:13px;font-weight:800;color:var(--color-accent)">▼ View Evaluated Papers &amp; Judge Transcripts for Eval #${ev.id}</summary>
            <div class="past-eval-papers-container" style="margin-top:10px;display:flex;flex-direction:column;gap:12px">
              <p style="font-size:12px;opacity:.6">Loading papers &amp; transcripts...</p>
            </div>
          </details>
        `;
        container.appendChild(card);
      });

      // Bind delete buttons
      container.querySelectorAll('.btn-del-eval').forEach(b => b.addEventListener('click', async e => {
        const id = e.target.getAttribute('data-id');
        if (confirm(`Delete evaluation #${id}?`)) {
          await fetch(`/api/evaluations/${id}`, { method: 'DELETE' });
          this.loadHistory();
        }
      }));

      // Bind lazy loading of past evaluation papers & judge transcripts when accordion is opened
      container.querySelectorAll('.past-eval-details').forEach(details => {
        details.addEventListener('toggle', async (e) => {
          if (details.open) {
            const evalId = details.getAttribute('data-id');
            const papersContainer = details.querySelector('.past-eval-papers-container');
            if (papersContainer && !papersContainer.hasAttribute('data-loaded')) {
              try {
                const res = await fetch(`/api/evaluations/${evalId}`);
                const data = await res.json();
                papersContainer.setAttribute('data-loaded', 'true');
                papersContainer.innerHTML = '';

                if (!data.papers || !data.papers.length) {
                  papersContainer.innerHTML = '<p style="font-size:12px;opacity:.6">No papers stored for this evaluation.</p>';
                  return;
                }

                data.papers.forEach(p => {
                  const pCard = document.createElement('div');
                  const scoreClass = p.avg_score >= 7 ? 'high' : p.avg_score >= 4 ? 'mid' : 'low';
                  pCard.className = 'card';
                  pCard.style.background = 'var(--color-bg)';

                  // Judge transcripts HTML for past paper
                  let pastJudgesHtml = '';
                  if (p.verdicts && p.verdicts.length) {
                    pastJudgesHtml = p.verdicts.map(j => {
                      const cls = j.relevance_score >= 7 ? 'high' : j.relevance_score >= 4 ? 'mid' : 'low';
                      const reasonsList = Array.isArray(j.key_reasons) ? j.key_reasons.map(r => `<li>${r}</li>`).join('') : '';
                      return `
                        <div class="debate-panel" style="background:var(--color-surface);border-left:3px solid var(--color-accent);margin-bottom:6px">
                          <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:2px">
                            <strong style="font-size:12px">⚖️ Judge #${j.judge_run} (Seed: ${j.seed || 'N/A'})</strong>
                            <span class="score-badge ${cls}" style="font-size:11px;padding:2px 5px">${j.relevance_score}/10</span>
                          </div>
                          <div style="font-size:12px"><strong>Verdict:</strong> ${j.verdict || 'N/A'}</div>
                          ${reasonsList ? `<ul style="margin:2px 0 2px 14px;padding:0;font-size:11.5px;opacity:.85">${reasonsList}</ul>` : ''}
                          ${j.suggested_use ? `<div style="font-size:11.5px;opacity:.8"><strong>Suggested Use:</strong> ${j.suggested_use}</div>` : ''}
                        </div>
                      `;
                    }).join('');
                  }

                  // Debate rounds HTML for past paper
                  let pastDebatesHtml = '';
                  if (p.debates && p.debates.length) {
                    pastDebatesHtml = p.debates.map((rnd, i) => `
                      <div class="debate-panel debate-advocate" style="margin-bottom:6px">
                        <div style="font-weight:800;font-size:11.5px;color:oklch(38% 0.15 155);margin-bottom:2px">🟢 Advocate (Round ${rnd.round_num || (i + 1)})</div>
                        <div style="font-size:12px">${rnd.advocate_arg}</div>
                      </div>
                      <div class="debate-panel debate-skeptic" style="margin-bottom:6px">
                        <div style="font-weight:800;font-size:11.5px;color:var(--color-accent);margin-bottom:2px">🔴 Skeptic (Round ${rnd.round_num || (i + 1)})</div>
                        <div style="font-size:12px">${rnd.skeptic_arg}</div>
                      </div>
                    `).join('');
                  }

                  pCard.innerHTML = `
                    <div style="display:flex;justify-content:space-between;align-items:flex-start">
                      <div>
                        <strong style="font-size:14px">${p.title}</strong>
                        <div style="font-size:11px;opacity:.6">👤 ${p.authors || 'Unknown'} · 📅 ${p.published || ''}</div>
                      </div>
                      <span class="score-badge ${scoreClass}">${p.avg_score}/10</span>
                    </div>

                    <a href="${p.url}" target="_blank" class="btn btn-ghost" style="font-size:12px;align-self:flex-start">Open Paper &rarr;</a>

                    <details style="margin-top:6px">
                      <summary style="cursor:pointer;font-size:12px;font-weight:800;color:var(--color-accent)">▼ View Multi-Agent Debates &amp; 5-Judge Panel Transcripts</summary>
                      <div style="margin-top:8px;display:flex;flex-direction:column;gap:8px">
                        ${pastDebatesHtml ? `<div><strong style="font-size:12px">🗣️ Advocate vs. Skeptic Debates</strong><div style="margin-top:4px">${pastDebatesHtml}</div></div>` : ''}
                        ${pastJudgesHtml ? `<div><strong style="font-size:12px">⚖️ 5-Judge Panel Individual Verdicts</strong><div style="margin-top:4px">${pastJudgesHtml}</div></div>` : ''}
                      </div>
                    </details>
                  `;
                  papersContainer.appendChild(pCard);
                });
              } catch (err) {
                papersContainer.innerHTML = `<p style="font-size:12px;color:var(--color-accent)">Failed to load papers: ${err.message}</p>`;
              }
            }
          }
        });
      });
    },
  };

  app.init();
});
