/**
 * arXiv CS.CL Paper Matcher — Single Page Application JS Logic
 * Zero-reload interactive interface for research evaluation.
 */

document.addEventListener('DOMContentLoaded', () => {
  const app = {
    currentResults: [],
    currentSchedules: [],
    pastEvaluations: [],

    init() {
      this.bindTabNavigation();
      this.bindFormEvents();
      this.bindModalEvents();
      this.fetchConfig();
      this.loadSchedules();
      this.loadHistory();
    },

    // ── Navigation ──
    bindTabNavigation() {
      const tabs = document.querySelectorAll('.nav-tab');
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

      // Filter tabs in results
      const filterBtns = document.querySelectorAll('.filter-btn');
      filterBtns.forEach(btn => {
        btn.addEventListener('click', () => {
          filterBtns.forEach(b => b.classList.remove('active'));
          btn.classList.add('active');
          const filter = btn.getAttribute('data-filter');
          this.renderResultsList(filter);
        });
      });
    },

    // ── Config & Status ──
    async fetchConfig() {
      try {
        const res = await fetch('/api/config');
        const data = await res.json();

        const keyBadge = document.getElementById('status-api-key');
        if (data.has_api_key) {
          keyBadge.className = 'status-badge badge-success';
          keyBadge.textContent = '🔑 API Key Configured';
        } else {
          keyBadge.className = 'status-badge badge-warning';
          keyBadge.textContent = '⚠️ API Key Missing';
        }

        const cloudBadge = document.getElementById('status-cloud-sync');
        if (data.gcs_bucket || data.s3_bucket) {
          cloudBadge.className = 'status-badge badge-info';
          cloudBadge.textContent = `☁️ Syncing: ${data.gcs_bucket || data.s3_bucket}`;
        } else {
          cloudBadge.className = 'status-badge';
          cloudBadge.textContent = '💾 Local Storage';
        }
      } catch (e) {
        console.error('Config fetch failed:', e);
      }
    },

    // ── Form Sliders & Controls ──
    bindFormEvents() {
      // Sliders
      const bindSlider = (id, labelId) => {
        const slider = document.getElementById(id);
        const label = document.getElementById(labelId);
        if (slider && label) {
          slider.addEventListener('input', () => { label.textContent = slider.value; });
        }
      };
      bindSlider('max-papers', 'val-max-papers');
      bindSlider('days-back', 'val-days-back');
      bindSlider('min-score', 'val-min-score');
      bindSlider('max-concurrent', 'val-max-concurrent');

      // Fetch Mode Radio
      const radios = document.querySelectorAll('input[name="fetch-mode"]');
      radios.forEach(radio => {
        radio.addEventListener('change', (e) => {
          const isCount = e.target.value === 'count';
          document.getElementById('group-paper-count').classList.toggle('hidden', !isCount);
          document.getElementById('group-days-back').classList.toggle('hidden', isCount);
        });
      });

      // Submit Form (Live Stream Evaluation)
      const form = document.getElementById('eval-form');
      form.addEventListener('submit', (e) => {
        e.preventDefault();
        this.startLiveStreamEvaluation();
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
    },

    // ── Live Stream Evaluation (SSE) ──
    async startLiveStreamEvaluation() {
      const problem = document.getElementById('problem-statement').value.trim();
      if (!problem) return alert('Please enter a research problem description.');

      const model = document.getElementById('model-name').value;
      const fetchMode = document.querySelector('input[name="fetch-mode"]:checked').value;
      const maxPapers = fetchMode === 'count' ? parseInt(document.getElementById('max-papers').value) : null;
      const daysBack = fetchMode === 'days' ? parseInt(document.getElementById('days-back').value) : null;
      const keyword = document.getElementById('keyword-filter').value.trim();
      const concurrent = parseInt(document.getElementById('max-concurrent').value);

      const statusEl = document.getElementById('stream-status');
      const outputEl = document.getElementById('stream-output');
      const progressContainer = document.getElementById('live-progress-bar');
      const progressFill = document.getElementById('progress-fill');
      const resultsSection = document.getElementById('results-section');

      statusEl.textContent = '🚀 Starting live evaluation stream...';
      outputEl.textContent = '';
      progressContainer.classList.remove('hidden');
      progressFill.style.width = '0%';
      resultsSection.classList.add('hidden');
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
          statusEl.textContent = `❌ Error: ${err.detail || 'Evaluation failed.'}`;
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
          buffer = lines.pop(); // Keep unfinished chunk in buffer

          let currentEvent = 'message';
          for (const line of lines) {
            if (line.startsWith('event:')) {
              currentEvent = line.replace('event:', '').trim();
            } else if (line.startsWith('data:')) {
              const dataStr = line.replace('data:', '').trim();
              if (dataStr) {
                try {
                  const data = JSON.parse(dataStr);
                  this.handleSSEEvent(currentEvent, data, statusEl, outputEl, progressFill);
                } catch (err) {
                  console.error('JSON parse error:', err, dataStr);
                }
              }
            }
          }
        }
      } catch (e) {
        statusEl.textContent = `❌ Stream error: ${e.message}`;
      }
    },

    handleSSEEvent(event, data, statusEl, outputEl, progressFill) {
      const logLine = (txt) => {
        const time = new Date().toLocaleTimeString();
        outputEl.textContent += `[${time}] ${txt}\n`;
        outputEl.scrollTop = outputEl.scrollHeight;
      };

      if (event === 'stage') {
        statusEl.textContent = `📡 ${data.message}`;
        logLine(data.message);
      } else if (event === 'paper_start') {
        const pct = Math.round((data.paper_index / data.total_papers) * 100);
        progressFill.style.width = `${pct}%`;
        statusEl.textContent = `🔬 Evaluating paper ${data.paper_index}/${data.total_papers}: ${data.title.substring(0, 60)}...`;
        logLine(`Evaluating [${data.paper_index}/${data.total_papers}]: ${data.title}`);
      } else if (event === 'paper_done') {
        logLine(`✅ Score: ${data.avg_score}/10 — ${data.title}`);
        this.currentResults.push(data);
      } else if (event === 'eval_complete') {
        statusEl.textContent = `🎉 ${data.message}`;
        progressFill.style.width = '100%';
        logLine(`Completed! Total evaluated: ${data.total_evaluated}`);
        this.renderResults();
      } else if (event === 'error') {
        statusEl.textContent = `❌ Error: ${data.error}`;
        logLine(`ERROR: ${data.error}`);
      }
    },

    // ── Render Results Dashboard ──
    renderResults() {
      const resultsSection = document.getElementById('results-section');
      resultsSection.classList.remove('hidden');

      const results = this.currentResults;
      document.getElementById('metric-total').textContent = results.length;

      const high = results.filter(r => r.avg_score >= 7).length;
      const mid = results.filter(r => r.avg_score >= 4 && r.avg_score < 7).length;
      const low = results.filter(r => r.avg_score < 4).length;

      document.getElementById('metric-high').textContent = high;
      document.getElementById('metric-mid').textContent = mid;
      document.getElementById('metric-low').textContent = low;

      this.renderResultsList('all');
    },

    renderResultsList(filter) {
      const container = document.getElementById('papers-list-container');
      container.innerHTML = '';

      let list = [...this.currentResults];
      if (filter === 'top') {
        const minScore = parseInt(document.getElementById('min-score').value);
        list = list.filter(r => r.avg_score >= minScore);
      }

      if (!list.length) {
        container.innerHTML = '<div class="card"><p>No papers match the selected filter.</p></div>';
        return;
      }

      list.forEach(r => {
        const card = document.createElement('div');
        const scoreClass = r.avg_score >= 7 ? 'score-high' : r.avg_score >= 4 ? 'score-mid' : 'score-low';

        let chipsHtml = '';
        if (r.judge_scores) {
          chipsHtml = r.judge_scores.map(j => {
            const cls = j.score >= 7 ? 'high' : j.score >= 4 ? 'mid' : 'low';
            return `<span class="judge-chip ${cls}">J${j.run}: ${j.score}</span>`;
          }).join(' ');
        }

        let debatesHtml = '';
        if (r.rounds) {
          debatesHtml = r.rounds.map((rnd, i) => `
            <div class="transcript-message advocate-msg">
              <strong>🟢 Advocate (Round ${i + 1}):</strong><br/>${rnd.advocate}
            </div>
            <div class="transcript-message skeptic-msg">
              <strong>🔴 Skeptic (Round ${i + 1}):</strong><br/>${rnd.skeptic}
            </div>
          `).join('');
        }

        card.className = 'paper-card';
        card.innerHTML = `
          <div class="paper-header">
            <div>
              <div class="paper-title">${r.title}</div>
              <small>👤 ${r.authors || 'Unknown'} | 📄 <a href="${r.url}" target="_blank" style="color:var(--accent-primary)">Open arXiv Paper</a></small>
            </div>
            <div class="paper-score-badge ${scoreClass}">${r.avg_score}/10</div>
          </div>

          <div class="judge-chips">${chipsHtml}</div>
          <p style="margin-top:0.5rem"><strong>Verdict:</strong> ${r.verdict}</p>
          ${r.suggested_use ? `<p><small><strong>Suggested Use:</strong> ${r.suggested_use}</small></p>` : ''}

          <details class="debate-transcript" style="margin-top:1rem">
            <summary style="cursor:pointer;font-weight:600;color:var(--accent-primary)">🗣️ View Full Advocate/Skeptic Debate Transcripts</summary>
            <div style="margin-top:0.75rem">${debatesHtml}</div>
          </details>
        `;
        container.appendChild(card);
      });
    },

    // ── Schedules Management ──
    async loadSchedules() {
      try {
        const res = await fetch('/api/schedules');
        const data = await res.json();
        this.currentSchedules = data.schedules || [];
        this.renderSchedules();
      } catch (e) {
        console.error('Failed to load schedules:', e);
      }
    },

    renderSchedules() {
      const container = document.getElementById('schedules-list');
      container.innerHTML = '';

      if (!this.currentSchedules.length) {
        container.innerHTML = '<p class="text-muted">No recurring schedules created yet.</p>';
        return;
      }

      this.currentSchedules.forEach(s => {
        const card = document.createElement('div');
        card.className = 'schedule-card';
        const activeIcon = s.is_active ? '🟢 Active' : '⏸️ Paused';

        card.innerHTML = `
          <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:0.5rem">
            <h3>${s.label || `Schedule #${s.id}`}</h3>
            <span class="status-badge ${s.is_active ? 'badge-success' : 'badge-warning'}">${activeIcon}</span>
          </div>
          <p><small><strong>Run Time:</strong> ${s.run_time} daily | <strong>Model:</strong> ${s.model_name}</small></p>
          <p><small><strong>Fetch:</strong> ${s.fetch_mode === 'count' ? `${s.max_papers} papers` : `${s.days_back} days back`}</small></p>
          <p style="margin:0.5rem 0;color:var(--text-secondary)"><small>${s.problem_text.substring(0, 100)}...</small></p>
          <p><small class="text-muted">Last run: ${s.last_run_at || 'Never'} (${s.last_status || 'N/A'})</small></p>

          <div style="display:flex;gap:0.5rem;margin-top:1rem">
            <button class="btn btn-outline btn-sm btn-run-sch" data-id="${s.id}">▶️ Run Now</button>
            <button class="btn btn-outline btn-sm btn-toggle-sch" data-id="${s.id}" data-active="${s.is_active}">${s.is_active ? '⏸️ Pause' : '▶️ Activate'}</button>
            <button class="btn btn-outline btn-sm btn-edit-sch" data-id="${s.id}">✏️ Edit</button>
            <button class="btn btn-danger btn-sm btn-del-sch" data-id="${s.id}">🗑️ Delete</button>
          </div>
        `;
        container.appendChild(card);
      });

      // Bind actions
      container.querySelectorAll('.btn-run-sch').forEach(btn => {
        btn.addEventListener('click', async (e) => {
          const id = e.target.getAttribute('data-id');
          await fetch(`/api/schedules/${id}/run`, { method: 'POST' });
          alert(`Triggered background run for schedule #${id}!`);
        });
      });

      container.querySelectorAll('.btn-toggle-sch').forEach(btn => {
        btn.addEventListener('click', async (e) => {
          const id = e.target.getAttribute('data-id');
          const active = e.target.getAttribute('data-active') === '1';
          await fetch(`/api/schedules/${id}/toggle`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ active: !active }),
          });
          this.loadSchedules();
        });
      });

      container.querySelectorAll('.btn-del-sch').forEach(btn => {
        btn.addEventListener('click', async (e) => {
          const id = e.target.getAttribute('data-id');
          if (confirm('Delete this recurring schedule?')) {
            await fetch(`/api/schedules/${id}`, { method: 'DELETE' });
            this.loadSchedules();
          }
        });
      });

      container.querySelectorAll('.btn-edit-sch').forEach(btn => {
        btn.addEventListener('click', (e) => {
          const id = parseInt(e.target.getAttribute('data-id'));
          const sch = this.currentSchedules.find(s => s.id === id);
          if (sch) this.openScheduleModal(sch);
        });
      });
    },

    // ── Modal Handling ──
    bindModalEvents() {
      const modal = document.getElementById('schedule-modal');
      document.getElementById('btn-open-create-schedule').addEventListener('click', () => {
        this.openScheduleModal(null);
      });
      document.getElementById('btn-close-modal').addEventListener('click', () => modal.classList.add('hidden'));
      document.getElementById('btn-cancel-modal').addEventListener('click', () => modal.classList.add('hidden'));

      document.getElementById('schedule-form').addEventListener('submit', async (e) => {
        e.preventDefault();
        const schId = document.getElementById('sch-id').value;
        const payload = {
          label: document.getElementById('sch-label').value.trim(),
          problem_text: document.getElementById('sch-problem').value.trim(),
          model_name: document.getElementById('sch-model').value,
          run_time: document.getElementById('sch-time').value,
          max_papers: parseInt(document.getElementById('sch-max-papers').value) || 50,
          keyword_filter: document.getElementById('sch-keyword').value.trim(),
          fetch_mode: 'count',
        };

        if (schId) {
          await fetch(`/api/schedules/${schId}`, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload),
          });
        } else {
          await fetch('/api/schedules', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload),
          });
        }

        modal.classList.add('hidden');
        this.loadSchedules();
      });
    },

    openScheduleModal(sch) {
      const modal = document.getElementById('schedule-modal');
      const title = document.getElementById('modal-title');
      document.getElementById('sch-id').value = sch ? sch.id : '';
      document.getElementById('sch-label').value = sch ? (sch.label || '') : '';
      document.getElementById('sch-problem').value = sch ? sch.problem_text : (document.getElementById('problem-statement').value || '');
      document.getElementById('sch-model').value = sch ? sch.model_name : 'gemini-3-pro-preview';
      document.getElementById('sch-time').value = sch ? sch.run_time : '08:00';
      document.getElementById('sch-max-papers').value = sch ? (sch.max_papers || 50) : 50;
      document.getElementById('sch-keyword').value = sch ? (sch.keyword_filter || '') : '';

      title.textContent = sch ? `✏️ Edit Schedule #${sch.id}` : '➕ Create Recurring Schedule';
      modal.classList.remove('hidden');
    },

    // ── History Tab ──
    async loadHistory() {
      try {
        const res = await fetch('/api/evaluations');
        const data = await res.json();
        this.pastEvaluations = data.evaluations || [];
        this.renderHistory();
      } catch (e) {
        console.error('Failed to load history:', e);
      }
    },

    renderHistory() {
      const container = document.getElementById('history-evaluations-list');
      container.innerHTML = '';

      if (!this.pastEvaluations.length) {
        container.innerHTML = '<p class="text-muted">No past evaluations stored in SQLite database.</p>';
        return;
      }

      this.pastEvaluations.forEach(ev => {
        const item = document.createElement('div');
        item.className = 'card';
        item.innerHTML = `
          <div style="display:flex;justify-content:space-between;align-items:center">
            <h3>Evaluation #${ev.id} — ${ev.overall_avg || 0}/10 Overall</h3>
            <button class="btn btn-danger btn-sm btn-del-eval" data-id="${ev.id}">🗑️ Delete</button>
          </div>
          <p><strong>Problem:</strong> ${ev.problem_text}</p>
          <p><small class="text-muted">Model: ${ev.model_name} | Date: ${ev.created_at} | Papers: ${ev.paper_count}</small></p>
        `;
        container.appendChild(item);
      });

      container.querySelectorAll('.btn-del-eval').forEach(btn => {
        btn.addEventListener('click', async (e) => {
          const id = e.target.getAttribute('data-id');
          if (confirm(`Delete evaluation #${id}?`)) {
            await fetch(`/api/evaluations/${id}`, { method: 'DELETE' });
            this.loadHistory();
          }
        });
      });
    },
  };

  app.init();
});
