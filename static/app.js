/**
 * arXiv CS.CL Paper Matcher — SPA JS Engine
 * Replicates the Streamlit UI layout with zero page reloads.
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
      const tabs = document.querySelectorAll('.st-tab');
      tabs.forEach(tab => {
        tab.addEventListener('click', () => {
          tabs.forEach(t => t.classList.remove('active'));
          document.querySelectorAll('.tab-pane').forEach(p => p.classList.remove('active'));

          tab.classList.add('active');
          const paneId = tab.getAttribute('data-tab');
          const pane = document.getElementById(paneId);
          if (pane) pane.classList.add('active');
        });
      });

      // Results Sub-tabs
      const subtabs = document.querySelectorAll('.subtab');
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
          keyBadge.className = 'alert-box alert-success';
          keyBadge.textContent = '🔑 API key loaded from Secret Manager';
        } else {
          keyBadge.className = 'alert-box alert-warning';
          keyBadge.textContent = '⚠️ Set GEMINI_API_KEY in Secret Manager';
        }

        const cloudBadge = document.getElementById('status-cloud-sync');
        if (data.gcs_bucket || data.s3_bucket) {
          cloudBadge.className = 'alert-box alert-info';
          cloudBadge.textContent = `💾 Persistent DB: ${data.gcs_bucket || data.s3_bucket}`;
        }
      } catch (e) {
        console.error('Config error:', e);
      }
    },

    // ── Controls & Range Sliders ──
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

      // Fetch Mode Radio
      document.querySelectorAll('input[name="fetch-mode"]').forEach(radio => {
        radio.addEventListener('change', (e) => {
          const isCount = e.target.value === 'count';
          document.getElementById('group-paper-count').classList.toggle('hidden', !isCount);
          document.getElementById('group-days-back').classList.toggle('hidden', isCount);
        });
      });

      // Actions
      document.getElementById('btn-run-all').addEventListener('click', () => this.startLiveStreamEvaluation());
      document.getElementById('btn-clear-results').addEventListener('click', () => {
        this.currentResults = [];
        document.getElementById('results-dashboard').classList.add('hidden');
        document.getElementById('evaluation-stream-container').innerHTML = '';
      });

      document.getElementById('btn-add-recurring').addEventListener('click', () => {
        const problem = document.getElementById('problem-statement').value.trim();
        if (!problem) return alert('Please enter your research problem first.');
        this.openEditModal(null);
      });

      // Modal submit
      document.getElementById('form-create-schedule').addEventListener('submit', async (e) => {
        e.preventDefault();
        const payload = {
          label: document.getElementById('sch-label').value.trim(),
          problem_text: document.getElementById('sch-problem').value.trim(),
          model_name: document.getElementById('sch-model').value,
          run_time: document.getElementById('sch-time').value,
          max_papers: parseInt(document.getElementById('sch-papers').value) || 50,
          keyword_filter: document.getElementById('sch-keyword').value.trim(),
          fetch_mode: 'count',
        };
        await fetch('/api/schedules', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(payload),
        });
        alert('Created recurring schedule!');
        this.loadSchedules();
      });

      // Modal Edit submit
      document.getElementById('form-edit-schedule').addEventListener('submit', async (e) => {
        e.preventDefault();
        const id = document.getElementById('edit-sch-id').value;
        const payload = {
          label: document.getElementById('edit-sch-label').value.trim(),
          problem_text: document.getElementById('edit-sch-problem').value.trim(),
          model_name: document.getElementById('edit-sch-model').value,
          run_time: document.getElementById('edit-sch-time').value,
          max_papers: parseInt(document.getElementById('edit-sch-papers').value) || 50,
          keyword_filter: document.getElementById('edit-sch-keyword').value.trim(),
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

      document.getElementById('btn-close-modal').addEventListener('click', () => {
        document.getElementById('modal-edit-schedule').classList.add('hidden');
      });
      document.getElementById('btn-cancel-edit').addEventListener('click', () => {
        document.getElementById('modal-edit-schedule').classList.add('hidden');
      });
    },

    // ── Live Stream Evaluation ──
    async startLiveStreamEvaluation() {
      const problem = document.getElementById('problem-statement').value.trim();
      if (!problem) return alert('Please describe your research problem.');

      const model = document.getElementById('model-name').value;
      const fetchMode = document.querySelector('input[name="fetch-mode"]:checked').value;
      const maxPapers = fetchMode === 'count' ? parseInt(document.getElementById('max-papers').value) : null;
      const daysBack = fetchMode === 'days' ? parseInt(document.getElementById('days-back').value) : null;
      const keyword = document.getElementById('keyword-filter').value.trim();
      const concurrent = parseInt(document.getElementById('max-concurrent').value);

      const statusBox = document.getElementById('status-container');
      const statusText = document.getElementById('status-text');
      const progressFill = document.getElementById('progress-fill');
      const streamContainer = document.getElementById('evaluation-stream-container');

      statusBox.classList.remove('hidden');
      statusText.textContent = '📡 Fetching papers from arXiv CS.CL...';
      progressFill.style.width = '0%';
      streamContainer.innerHTML = '';
      document.getElementById('results-dashboard').classList.add('hidden');
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
          statusText.textContent = `❌ Error: ${err.detail || 'Evaluation failed.'}`;
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
                  this.handleStreamEvent(currentEvent, data, statusText, progressFill, streamContainer);
                } catch (err) {
                  console.error('SSE Error:', err);
                }
              }
            }
          }
        }
      } catch (e) {
        statusText.textContent = `❌ Connection error: ${e.message}`;
      }
    },

    handleStreamEvent(event, data, statusText, progressFill, streamContainer) {
      if (event === 'stage') {
        statusText.textContent = `📡 ${data.message}`;
      } else if (event === 'paper_start') {
        const pct = Math.round((data.paper_index / data.total_papers) * 100);
        progressFill.style.width = `${pct}%`;
        statusText.textContent = `📄 [${data.paper_index}/${data.total_papers}] ${data.title.substring(0, 70)}...`;

        const card = document.createElement('div');
        card.id = `stream-paper-${data.paper_index}`;
        card.className = 'paper-card';
        card.innerHTML = `
          <div><strong>[${data.paper_index}/${data.total_papers}] ${data.title}</strong></div>
          <div class="caption-muted">👤 ${data.authors} &nbsp;|&nbsp; 📅 ${data.published}</div>
          <div class="paper-status-msg" style="margin-top:0.5rem;color:var(--st-primary)">⏳ Evaluating multi-agent debate...</div>
        `;
        streamContainer.appendChild(card);
      } else if (event === 'paper_done') {
        this.currentResults.push(data);
        const card = document.getElementById(`stream-paper-${data.paper_index}`);
        if (card) {
          const scoreClass = data.avg_score >= 7 ? 'score-high' : data.avg_score >= 4 ? 'score-mid' : 'score-low';
          card.querySelector('.paper-status-msg').innerHTML = `<span class="${scoreClass}">Score: ${data.avg_score}/10</span> &nbsp;|&nbsp; ${data.verdict}`;
        }
      } else if (event === 'eval_complete') {
        statusText.textContent = `✅ ${data.message}`;
        progressFill.style.width = '100%';
        this.renderResultsDashboard();
      }
    },

    // ── Render Streamlit Dashboard ──
    renderResultsDashboard() {
      const results = this.currentResults;
      document.getElementById('results-dashboard').classList.remove('hidden');

      document.getElementById('metric-total').textContent = results.length;
      document.getElementById('metric-high').textContent = results.filter(r => r.avg_score >= 7).length;
      document.getElementById('metric-mid').textContent = results.filter(r => r.avg_score >= 4 && r.avg_score < 7).length;
      document.getElementById('metric-low').textContent = results.filter(r => r.avg_score < 4).length;

      this.renderPaperCards('papers-list-all', results);
      const minScore = parseInt(document.getElementById('min-score').value);
      this.renderPaperCards('papers-list-top', results.filter(r => r.avg_score >= minScore), true);
      this.renderDebateDetails('papers-list-debate', results);
    },

    renderPaperCards(containerId, list, showTopBadge = false) {
      const container = document.getElementById(containerId);
      container.innerHTML = '';

      if (!list.length) {
        container.innerHTML = '<p class="caption-muted">No papers match this threshold.</p>';
        return;
      }

      list.forEach(r => {
        const scoreClass = r.avg_score >= 7 ? 'score-high' : r.avg_score >= 4 ? 'score-mid' : 'score-low';
        const badge = showTopBadge ? '⭐ ' : '';
        const chipsHtml = (r.judge_scores || []).map(j => {
          const cls = j.score >= 7 ? 'chip-high' : j.score >= 4 ? 'chip-mid' : 'chip-low';
          return `<span class="judge-chip ${cls}">J${j.run}: ${j.score}</span>`;
        }).join(' ');

        let roundsHtml = '';
        if (r.rounds) {
          roundsHtml = r.rounds.map((rnd, i) => `
            <div class="chat-msg chat-advocate">
              <strong>🟢 Advocate (Round ${i + 1})</strong><br/>${rnd.advocate}
            </div>
            <div class="chat-msg chat-skeptic">
              <strong>🔴 Skeptic (Round ${i + 1})</strong><br/>${rnd.skeptic}
            </div>
          `).join('');
        }

        const card = document.createElement('div');
        card.className = 'paper-card';
        card.innerHTML = `
          <div style="display:flex;justify-content:space-between;align-items:flex-start">
            <div>
              <span class="${scoreClass}">${badge}${r.avg_score}/10</span>
              &nbsp;&nbsp;<strong>${r.title}</strong><br/>
              <span class="caption-muted">👤 ${r.authors || 'Unknown'} &nbsp;|&nbsp; 📅 ${r.published || ''}</span>
            </div>
          </div>

          <div style="margin:0.75rem 0">${chipsHtml} &rarr; <strong>Avg: ${r.avg_score}</strong></div>
          <p><strong>Verdict:</strong> ${r.verdict}</p>
          ${r.suggested_use ? `<p><small><strong>Suggested Use:</strong> ${r.suggested_use}</small></p>` : ''}
          <div style="margin-top:0.75rem"><a href="${r.url}" target="_blank" class="st-btn st-btn-secondary" style="text-decoration:none">📄 Open Paper</a></div>

          <details style="margin-top:1rem">
            <summary style="cursor:pointer;font-weight:600;color:var(--st-primary)">🗣️ Debate Transcripts & Judge Details</summary>
            <div style="margin-top:0.75rem">${roundsHtml}</div>
          </details>
        `;
        container.appendChild(card);
      });
    },

    renderDebateDetails(containerId, list) {
      const container = document.getElementById(containerId);
      container.innerHTML = '';
      list.forEach(r => {
        const icon = r.avg_score >= 7 ? '🟢' : r.avg_score >= 4 ? '🟡' : '🔴';
        const card = document.createElement('div');
        card.className = 'paper-card';
        card.innerHTML = `
          <h3>${icon} [${r.avg_score}/10] ${r.title}</h3>
          <p><strong>Verdict:</strong> ${r.verdict}</p>
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
      } catch (e) { console.error('Load schedules error:', e); }
    },

    renderSchedules() {
      const container = document.getElementById('schedules-accordion-container');
      container.innerHTML = '';

      if (!this.currentSchedules.length) {
        container.innerHTML = '<p class="caption-muted">No recurring schedules yet.</p>';
        return;
      }

      this.currentSchedules.forEach(s => {
        const icon = s.is_active ? '🟢' : '⏸️';
        const card = document.createElement('div');
        card.className = 'paper-card';
        card.innerHTML = `
          <div style="display:flex;justify-content:space-between;align-items:center">
            <h3>${icon} #${s.id} • ${s.label || `Schedule #${s.id}`} • ${s.run_time} daily</h3>
            <div style="display:flex;gap:0.5rem">
              <button class="st-btn st-btn-secondary btn-run-sch" data-id="${s.id}">▶️ Run Now</button>
              <button class="st-btn st-btn-secondary btn-toggle-sch" data-id="${s.id}" data-active="${s.is_active}">${s.is_active ? '⏸️ Pause' : '▶️ Activate'}</button>
              <button class="st-btn st-btn-secondary btn-edit-sch" data-id="${s.id}">✏️ Edit</button>
              <button class="st-btn st-btn-secondary btn-del-sch" data-id="${s.id}" style="color:var(--st-red)">🗑️ Delete</button>
            </div>
          </div>
          <p><small>Model: ${s.model_name} | Fetch: ${s.fetch_mode === 'count' ? `${s.max_papers} papers` : `${s.days_back} days`}</small></p>
          <p><small>Problem: ${s.problem_text}</small></p>
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
        if (sch) this.openEditModal(sch);
      }));
    },

    openEditModal(sch) {
      document.getElementById('edit-sch-id').value = sch ? sch.id : '';
      document.getElementById('edit-sch-label').value = sch ? (sch.label || '') : '';
      document.getElementById('edit-sch-problem').value = sch ? sch.problem_text : (document.getElementById('problem-statement').value || '');
      document.getElementById('edit-sch-model').value = sch ? sch.model_name : 'gemini-3-pro-preview';
      document.getElementById('edit-sch-time').value = sch ? sch.run_time : '08:00';
      document.getElementById('edit-sch-papers').value = sch ? (sch.max_papers || 50) : 50;
      document.getElementById('edit-sch-keyword').value = sch ? (sch.keyword_filter || '') : '';

      document.getElementById('modal-edit-title').textContent = sch ? `✏️ Edit Schedule #${sch.id}` : '➕ Create Schedule';
      document.getElementById('modal-edit-schedule').classList.remove('hidden');
    },

    // ── History List ──
    async loadHistory() {
      try {
        const res = await fetch('/api/evaluations');
        const data = await res.json();
        this.pastEvaluations = data.evaluations || [];
        this.renderHistory();
      } catch (e) { console.error('Load history error:', e); }
    },

    renderHistory() {
      const container = document.getElementById('history-list-container');
      container.innerHTML = '';

      if (!this.pastEvaluations.length) {
        container.innerHTML = '<p class="caption-muted">No past evaluations stored in SQLite database.</p>';
        return;
      }

      this.pastEvaluations.forEach(ev => {
        const item = document.createElement('div');
        item.className = 'paper-card';
        item.innerHTML = `
          <div style="display:flex;justify-content:space-between;align-items:center">
            <h3>Evaluation #${ev.id} — ${ev.overall_avg || 0}/10 Overall</h3>
            <button class="st-btn st-btn-secondary btn-del-eval" data-id="${ev.id}" style="color:var(--st-red)">🗑️ Delete</button>
          </div>
          <p><strong>Research Problem:</strong> ${ev.problem_text}</p>
          <p><small class="caption-muted">Model: ${ev.model_name} | Date: ${ev.created_at} | Papers: ${ev.paper_count}</small></p>
        `;
        container.appendChild(item);
      });

      container.querySelectorAll('.btn-del-eval').forEach(b => b.addEventListener('click', async e => {
        const id = e.target.getAttribute('data-id');
        if (confirm(`Delete evaluation #${id}?`)) {
          await fetch(`/api/evaluations/${id}`, { method: 'DELETE' });
          this.loadHistory();
        }
      }));
    },
  };

  app.init();
});
