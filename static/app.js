/**
 * arXiv CS.CL Paper Matcher — SPA JS Engine
 * Handled via FastAPI REST APIs & Live Server-Sent Events (SSE) streaming.
 */

document.addEventListener('DOMContentLoaded', () => {
  const app = {
    currentResults: [],
    currentSchedules: [],
    pastEvaluations: [],
    allPapers: [],
    selectedPaperIds: new Set(),
    selectedEvalIds: new Set(),
    abortController: null,

    init() {
      this.bindTabNavigation();
      this.bindFormControls();
      this.bindPaperModal();
      this.fetchConfig();
      this.loadSchedules();
      this.loadHistory();
    },

    async fetchJsonWithTimeout(url, timeoutMs = 15000) {
      const controller = new AbortController();
      const timer = setTimeout(() => controller.abort(), timeoutMs);
      try {
        const res = await fetch(url, { signal: controller.signal });
        if (!res.ok) throw new Error(`Request failed (${res.status})`);
        return await res.json();
      } catch (err) {
        if (err && err.name === 'AbortError') {
          throw new Error(`Request timed out after ${Math.round(timeoutMs / 1000)}s`);
        }
        throw err;
      } finally {
        clearTimeout(timer);
      }
    },

    async fetchPaperDetails(ids) {
      const settled = await Promise.allSettled(
        ids.map(id => this.fetchJsonWithTimeout(`/api/papers/${id}`, 15000))
      );

      const papers = [];
      const errors = [];
      settled.forEach((result, idx) => {
        const id = ids[idx];
        if (result.status === 'fulfilled') {
          papers.push(result.value);
        } else {
          const msg = result.reason && result.reason.message ? result.reason.message : String(result.reason);
          errors.push(`Paper #${id}: ${msg}`);
        }
      });

      if (!papers.length) {
        throw new Error(errors[0] || 'No paper details could be loaded.');
      }

      return { papers, errors };
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
          this.navigateToTab(paneId);
        });
      });

      document.getElementById('btn-go-to-history')?.addEventListener('click', () => {
        this.navigateToTab('tab-history');
      });

      // Subtabs (Works for both Results dashboard and History sub-tabs!)
      document.addEventListener('click', (e) => {
        if (e.target.classList.contains('subtab-btn')) {
          const btn = e.target;
          const parentHeader = btn.closest('.subtabs-header');
          if (parentHeader) {
            parentHeader.querySelectorAll('.subtab-btn').forEach(b => b.classList.remove('active'));
            btn.classList.add('active');

            const parentContainer = parentHeader.parentElement;
            parentContainer.querySelectorAll('.subtab-pane').forEach(p => p.classList.remove('active'));

            const paneId = btn.getAttribute('data-subtab');
            const pane = document.getElementById(paneId);
            if (pane) pane.classList.add('active');

            // Reset paper detail pane view when switching History subtabs
            const historySelectedPane = document.getElementById('history-selected-pane');
            if (historySelectedPane) historySelectedPane.classList.add('hidden');
            document.getElementById('history-table-container')?.classList.remove('hidden');
            document.getElementById('history-bulk-toolbar')?.classList.remove('hidden');
          }
        }
      });
    },

    navigateToTab(tabId) {
      document.querySelectorAll('.tab-btn').forEach(t => {
        t.classList.toggle('active', t.getAttribute('data-tab') === tabId);
      });
      document.querySelectorAll('.tab-pane').forEach(p => {
        p.classList.toggle('active', p.id === tabId);
      });
      if (tabId === 'tab-history') {
        const runsSubtabBtn = document.querySelector('.subtab-btn[data-subtab="history-subtab-runs"]');
        if (runsSubtabBtn) runsSubtabBtn.click();
        this.loadHistory();
      } else if (tabId === 'tab-recurring') {
        this.autoFillScheduleForm(false);
        this.loadSchedules();
      }
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
      bindVal('history-filter-min-score', 'val-history-filter-min-score');
      bindVal('history-filter-max-score', 'val-history-filter-max-score');

      // Paper Source Change Control (arXiv vs ACL 2026 for New Evaluation Sidebar)
      const paperSourceSelect = document.getElementById('paper-source');
      if (paperSourceSelect) {
        const handleSidebarSourceChange = () => {
          const isAcl = paperSourceSelect.value === 'acl';
          const groupAclTrack = document.getElementById('group-acl-track');
          const groupFetchMode = document.getElementById('group-fetchmode');
          const groupPaperCount = document.getElementById('group-paper-count');
          const groupDaysBack = document.getElementById('group-days-back');

          if (groupAclTrack) groupAclTrack.classList.toggle('hidden', !isAcl);
          if (groupFetchMode) groupFetchMode.classList.toggle('hidden', isAcl);

          if (isAcl) {
            if (groupDaysBack) groupDaysBack.classList.add('hidden');
            if (groupPaperCount) groupPaperCount.classList.add('hidden');
            const radioCount = document.querySelector('input[name="fetchmode"][value="count"]');
            if (radioCount) radioCount.checked = true;
          } else {
            const selectedMode = document.querySelector('input[name="fetchmode"]:checked')?.value || 'count';
            if (groupPaperCount) groupPaperCount.classList.toggle('hidden', selectedMode !== 'count');
            if (groupDaysBack) groupDaysBack.classList.toggle('hidden', selectedMode !== 'days');
          }
        };
        paperSourceSelect.addEventListener('change', handleSidebarSourceChange);
        handleSidebarSourceChange();
      }

      // Fetch Mode Segmented Control
      document.querySelectorAll('input[name="fetchmode"]').forEach(radio => {
        radio.addEventListener('change', (e) => {
          const isCount = e.target.value === 'count';
          document.getElementById('group-paper-count').classList.toggle('hidden', !isCount);
          document.getElementById('group-days-back').classList.toggle('hidden', isCount);
        });
      });

      // Evaluation Action Buttons
      document.getElementById('btn-run-all')?.addEventListener('click', () => {
        const execMode = document.querySelector('input[name="execmode"]:checked')?.value || 'live';
        if (execMode === 'background') {
          this.startBackgroundEvaluation();
        } else {
          this.startLiveStreamEvaluation();
        }
      });

      document.getElementById('btn-stop-eval')?.addEventListener('click', () => {
        if (this.abortController) {
          this.abortController.abort();
          this.abortController = null;
        }
        const progressStage = document.getElementById('progress-stage');
        const btnRunAll = document.getElementById('btn-run-all');
        const btnStop = document.getElementById('btn-stop-eval');

        if (progressStage) progressStage.textContent = '🛑 Evaluation stopped by user.';
        if (btnRunAll) btnRunAll.classList.remove('hidden');
        if (btnStop) btnStop.classList.add('hidden');

        if (this.currentResults && this.currentResults.length) {
          this.renderResultsDashboard();
        }
        this.loadHistory();
      });

      document.getElementById('btn-clear-results')?.addEventListener('click', () => {
        this.currentResults = [];
        document.getElementById('dashboard-container')?.classList.add('hidden');
        document.getElementById('progress-card')?.classList.add('hidden');
      });

      document.getElementById('btn-fetch-only')?.addEventListener('click', () => {
        this.fetchPapersOnly();
      });

      document.getElementById('btn-post-webhook')?.addEventListener('click', () => {
        if (!this.currentResults.length) {
          alert('No in-memory results available. Run an evaluation first, then use this button.');
          return;
        }
        alert('Webhook posting is handled automatically on completed evaluations when auto-post is enabled.');
      });

      // Range Value Badge Syncing for Create Schedule
      const syncSchRange = (id, targetId) => {
        const input = document.getElementById(id);
        const target = document.getElementById(targetId);
        if (input && target) {
          input.addEventListener('input', () => { target.textContent = input.value; });
        }
      };
      syncSchRange('sch-papers', 'sch-val-papers');
      syncSchRange('sch-days-back', 'sch-val-days');
      syncSchRange('sch-min-score', 'sch-val-minscore');
      syncSchRange('sch-max-concurrent', 'sch-val-concurrent');

      syncSchRange('edit-sch-papers', 'edit-sch-val-papers');
      syncSchRange('edit-sch-days-back', 'edit-sch-val-days');
      syncSchRange('edit-sch-min-score', 'edit-sch-val-minscore');
      syncSchRange('edit-sch-max-concurrent', 'edit-sch-val-concurrent');

      // Schedule Source & Track toggles (Hides Fetch mode when ACL selected)
      const bindSourceToggle = (srcId, trackGroupId, fetchModeGroupId, countGrpId, daysGrpId, radioName) => {
        const src = document.getElementById(srcId);
        const trackGrp = document.getElementById(trackGroupId);
        const fetchGrp = document.getElementById(fetchModeGroupId);
        const countGrp = document.getElementById(countGrpId);
        const daysGrp = document.getElementById(daysGrpId);

        if (src) {
          const handleSourceChange = () => {
            const isAcl = src.value === 'acl';
            if (trackGrp) trackGrp.classList.toggle('hidden', !isAcl);
            if (fetchGrp) fetchGrp.classList.toggle('hidden', isAcl);

            if (isAcl) {
              if (daysGrp) daysGrp.classList.add('hidden');
              if (countGrp) countGrp.classList.add('hidden');
              const radioCount = document.querySelector(`input[name="${radioName}"][value="count"]`);
              if (radioCount) radioCount.checked = true;
            } else {
              const selectedMode = document.querySelector(`input[name="${radioName}"]:checked`)?.value || 'count';
              if (countGrp) countGrp.classList.toggle('hidden', selectedMode !== 'count');
              if (daysGrp) daysGrp.classList.toggle('hidden', selectedMode !== 'days');
            }
          };
          src.addEventListener('change', handleSourceChange);
          handleSourceChange();
        }
      };
      bindSourceToggle('sch-source', 'sch-group-acl-track', 'sch-field-fetchmode', 'sch-group-count', 'sch-group-days', 'sch-fetchmode');
      bindSourceToggle('edit-sch-source', 'edit-sch-group-acl-track', 'edit-sch-field-fetchmode', 'edit-sch-group-count', 'edit-sch-group-days', 'edit-sch-fetchmode');

      // Schedule Fetch Mode toggles
      const bindFetchToggle = (radioName, countGrpId, daysGrpId) => {
        document.querySelectorAll(`input[name="${radioName}"]`).forEach(r => {
          r.addEventListener('change', (e) => {
            const countGrp = document.getElementById(countGrpId);
            const daysGrp = document.getElementById(daysGrpId);
            if (e.target.value === 'days') {
              countGrp.classList.add('hidden');
              daysGrp.classList.remove('hidden');
            } else {
              countGrp.classList.remove('hidden');
              daysGrp.classList.add('hidden');
            }
          });
        });
      };
      bindFetchToggle('sch-fetchmode', 'sch-group-count', 'sch-group-days');
      bindFetchToggle('edit-sch-fetchmode', 'edit-sch-group-count', 'edit-sch-group-days');

      // Add to Recurring Button (copies ALL sidebar options to Create Schedule form)
      document.getElementById('btn-add-recurring')?.addEventListener('click', () => {
        const problem = document.getElementById('problem-statement').value.trim();
        if (!problem) return alert('Please describe your research problem first.');

        // Copy sidebar options
        const model = document.getElementById('model-name').value;
        const paperSource = document.getElementById('paper-source')?.value || 'arxiv';
        const aclTrack = document.getElementById('acl-track')?.value || 'all';
        const fetchMode = document.querySelector('input[name="fetchmode"]:checked')?.value || 'count';
        const maxPapers = document.getElementById('max-papers').value;
        const daysBack = document.getElementById('days-back').value;
        const keyword = document.getElementById('keyword-filter').value.trim();
        const minScore = document.getElementById('min-score').value;
        const concurrent = document.getElementById('max-concurrent').value;

        // Switch to recurring tab
        document.querySelector('.tab-btn[data-tab="tab-recurring"]').click();

        document.getElementById('sch-problem').value = problem;
        document.getElementById('sch-model').value = model;
        document.getElementById('sch-source').value = paperSource;
        document.getElementById('sch-acl-track').value = aclTrack;
        document.getElementById('sch-source').dispatchEvent(new Event('change'));

        const fmRadio = document.querySelector(`input[name="sch-fetchmode"][value="${fetchMode}"]`);
        if (fmRadio) {
          fmRadio.checked = true;
          fmRadio.dispatchEvent(new Event('change'));
        }

        document.getElementById('sch-papers').value = maxPapers;
        document.getElementById('sch-val-papers').textContent = maxPapers;
        document.getElementById('sch-days-back').value = daysBack;
        document.getElementById('sch-val-days').textContent = daysBack;
        document.getElementById('sch-keyword').value = keyword;
        document.getElementById('sch-min-score').value = minScore;
        document.getElementById('sch-val-minscore').textContent = minScore;
        document.getElementById('sch-max-concurrent').value = concurrent;
        document.getElementById('sch-val-concurrent').textContent = concurrent;

        document.getElementById('sch-label').focus();
      });

      document.getElementById('btn-sync-sch-from-sidebar')?.addEventListener('click', () => {
        this.autoFillScheduleForm(true);
      });

      document.getElementById('form-schedule')?.addEventListener('submit', async (e) => {
        e.preventDefault();
        const paperSource = document.getElementById('sch-source').value;
        const isAcl = paperSource === 'acl';
        const fetchMode = document.querySelector('input[name="sch-fetchmode"]:checked')?.value || 'count';

        const payload = {
          label: document.getElementById('sch-label').value.trim(),
          problem_text: document.getElementById('sch-problem').value.trim(),
          model_name: document.getElementById('sch-model').value,
          paper_source: paperSource,
          acl_track: document.getElementById('sch-acl-track').value,
          fetch_mode: fetchMode,
          max_papers: isAcl ? null : (fetchMode === 'count' ? parseInt(document.getElementById('sch-papers').value) : null),
          days_back: isAcl ? null : (fetchMode === 'days' ? parseInt(document.getElementById('sch-days-back').value) : null),
          keyword_filter: document.getElementById('sch-keyword').value.trim(),
          min_score: parseInt(document.getElementById('sch-min-score').value),
          max_concurrent: parseInt(document.getElementById('sch-max-concurrent').value),
          run_time: document.getElementById('sch-time').value,
        };

        if (!payload.problem_text) {
          alert('Please provide a research problem description for this schedule.');
          return;
        }

        try {
          const res = await fetch('/api/schedules', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload),
          });
          const data = await res.json().catch(() => ({}));
          if (!res.ok || !data.success) {
            throw new Error(data.detail || data.error || 'Failed to create schedule');
          }
          await this.loadSchedules();
          alert(`Schedule created (#${data.schedule_id}).`);
        } catch (err) {
          alert(`Failed to create schedule: ${err.message}`);
        }
      });

      // Export JSON
      document.getElementById('btn-export-json')?.addEventListener('click', () => {
        if (!this.currentResults.length) return;
        const blob = new Blob([JSON.stringify(this.currentResults, null, 2)], { type: 'application/json' });
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = 'arxiv_paper_matches.json';
        a.click();
      });



      // History Filters & Bulk Action Event Handlers
      const historySearch = document.getElementById('history-search');
      const historyMinScore = document.getElementById('history-filter-min-score');
      const historyMaxScore = document.getElementById('history-filter-max-score');
      const historyFilterSource = document.getElementById('history-filter-source');
      const historySort = document.getElementById('history-sort');

      const triggerTableReRender = () => {
        if (typeof this.renderHistoryPapersTable === 'function') {
          this.renderHistoryPapersTable();
        }
        if (typeof this.renderHistory === 'function') {
          this.renderHistory();
        }
      };

      const updateDualSliderHighlight = () => {
        const minInput = document.getElementById('history-filter-min-score');
        const maxInput = document.getElementById('history-filter-max-score');
        const highlight = document.getElementById('dual-range-highlight');
        if (minInput && maxInput && highlight) {
          const minVal = parseInt(minInput.value);
          const maxVal = parseInt(maxInput.value);
          const leftPercent = ((minVal - 1) / 9) * 100;
          const rightPercent = 100 - (((maxVal - 1) / 9) * 100);
          highlight.style.left = `${leftPercent}%`;
          highlight.style.right = `${rightPercent}%`;
        }
      };

      const handleScoreSliderChange = (e) => {
        const minInput = document.getElementById('history-filter-min-score');
        const maxInput = document.getElementById('history-filter-max-score');
        if (minInput && maxInput) {
          let minVal = parseInt(minInput.value);
          let maxVal = parseInt(maxInput.value);
          if (minVal > maxVal) {
            if (e.target?.id === 'history-filter-min-score') {
              maxInput.value = minVal;
              const valMax = document.getElementById('val-history-filter-max-score');
              if (valMax) valMax.textContent = minVal;
            } else {
              minInput.value = maxVal;
              const valMin = document.getElementById('val-history-filter-min-score');
              if (valMin) valMin.textContent = maxVal;
            }
          }
        }
        updateDualSliderHighlight();
        triggerTableReRender();
      };

      updateDualSliderHighlight();

      if (historySearch) historySearch.addEventListener('input', triggerTableReRender);
      if (historyMinScore) {
        historyMinScore.addEventListener('input', handleScoreSliderChange);
        historyMinScore.addEventListener('change', handleScoreSliderChange);
      }
      if (historyMaxScore) {
        historyMaxScore.addEventListener('input', handleScoreSliderChange);
        historyMaxScore.addEventListener('change', handleScoreSliderChange);
      }
      if (historyFilterSource) historyFilterSource.addEventListener('change', triggerTableReRender);
      if (historySort) historySort.addEventListener('change', triggerTableReRender);

      // Select All Checkbox (Papers Table)
      const chkSelectAll = document.getElementById('chk-select-all-papers');
      if (chkSelectAll) {
        chkSelectAll.addEventListener('change', (e) => {
          const isChecked = e.target.checked;
          const checkboxes = document.querySelectorAll('.chk-paper-item');
          checkboxes.forEach(cb => {
            cb.checked = isChecked;
            const pid = parseInt(cb.getAttribute('data-id'));
            if (isChecked) this.selectedPaperIds.add(pid);
            else this.selectedPaperIds.delete(pid);
          });
          this.updateBulkActionButtons();
        });
      }

      // Bulk Delete Papers Button
      const btnDeleteSelected = document.getElementById('btn-delete-selected-papers');
      if (btnDeleteSelected) {
        btnDeleteSelected.addEventListener('click', () => this.deleteSelectedPapers());
      }

      // Select All Runs Checkbox (Evaluation Runs Subtab)
      const chkSelectAllRuns = document.getElementById('chk-select-all-runs');
      if (chkSelectAllRuns) {
        chkSelectAllRuns.addEventListener('change', (e) => {
          const isChecked = e.target.checked;
          const checkboxes = document.querySelectorAll('.chk-eval-run-item');
          checkboxes.forEach(cb => {
            cb.checked = isChecked;
            const eid = parseInt(cb.getAttribute('data-id'));
            if (isChecked) this.selectedEvalIds.add(eid);
            else this.selectedEvalIds.delete(eid);
          });
          this.updateBulkActionButtonsForRuns();
        });
      }

      // Bulk Delete Runs Button (Evaluation Runs Subtab)
      const btnDeleteSelectedRuns = document.getElementById('btn-delete-selected-runs');
      if (btnDeleteSelectedRuns) {
        btnDeleteSelectedRuns.addEventListener('click', () => this.deleteSelectedEvaluationRuns());
      }

      // View Selected Details Button
      const btnViewSelected = document.getElementById('btn-view-selected-papers');
      if (btnViewSelected) {
        btnViewSelected.addEventListener('click', () => {
          const ids = Array.from(this.selectedPaperIds);
          if (ids.length === 0) return alert('Select at least one paper to view details.');
          this.renderHistorySelectedPane(ids);
        });
      }

      // Pane Close Button
      const btnClosePane = document.getElementById('btn-close-history-pane');
      if (btnClosePane) {
        btnClosePane.addEventListener('click', () => {
          document.getElementById('history-selected-pane')?.classList.add('hidden');
          document.getElementById('history-table-container')?.classList.remove('hidden');
          document.getElementById('history-filter-toolbar')?.classList.remove('hidden');
          document.getElementById('history-bulk-toolbar')?.classList.remove('hidden');
        });
      }

      // Pane Navigation Buttons
      const btnPanePrev = document.getElementById('btn-pane-prev-paper');
      if (btnPanePrev) {
        btnPanePrev.addEventListener('click', () => {
          if (!this.currentPanePaperIds || !this.currentPanePaperIds.length) return;
          const currentId = this.currentPanePaperIds[0];
          const idx = this.allPapers.findIndex(p => p.id === currentId);
          if (idx > 0) {
            this.renderHistorySelectedPane([this.allPapers[idx - 1].id]);
          }
        });
      }

      const btnPaneNext = document.getElementById('btn-pane-next-paper');
      if (btnPaneNext) {
        btnPaneNext.addEventListener('click', () => {
          if (!this.currentPanePaperIds || !this.currentPanePaperIds.length) return;
          const currentId = this.currentPanePaperIds[0];
          const idx = this.allPapers.findIndex(p => p.id === currentId);
          if (idx >= 0 && idx < this.allPapers.length - 1) {
            this.renderHistorySelectedPane([this.allPapers[idx + 1].id]);
          }
        });
      }

      // Pane Delete Button
      const btnDeletePane = document.getElementById('btn-delete-pane-papers');
      if (btnDeletePane) {
        btnDeletePane.addEventListener('click', async () => {
          if (this.currentPanePaperIds && this.currentPanePaperIds.length === 1) {
            if (confirm('Delete this paper from the database?')) {
              const targetId = this.currentPanePaperIds[0];
              await fetch('/api/papers', {
                method: 'DELETE',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ paper_ids: [targetId] }),
              });
              const idx = this.allPapers.findIndex(p => p.id === targetId);
              await this.loadHistory();
              if (this.allPapers.length > 0 && idx !== -1) {
                const nextIdx = Math.min(idx, this.allPapers.length - 1);
                this.renderHistorySelectedPane([this.allPapers[nextIdx].id]);
              } else {
                document.getElementById('history-selected-pane')?.classList.add('hidden');
                document.getElementById('history-table-container')?.classList.remove('hidden');
                document.getElementById('history-filter-toolbar')?.classList.remove('hidden');
                document.getElementById('history-bulk-toolbar')?.classList.remove('hidden');
              }
            }
          } else {
            await this.deleteSelectedPapers();
            document.getElementById('history-selected-pane')?.classList.add('hidden');
            document.getElementById('history-table-container')?.classList.remove('hidden');
            document.getElementById('history-filter-toolbar')?.classList.remove('hidden');
            document.getElementById('history-bulk-toolbar')?.classList.remove('hidden');
          }
        });
      }

      // Modal Paper Navigation & Action Buttons
      const btnPrevModal = document.getElementById('btn-prev-paper-modal');
      if (btnPrevModal) {
        btnPrevModal.addEventListener('click', () => {
          const idx = this.allPapers.findIndex(p => p.id === this.currentPaperModalId);
          if (idx > 0) {
            this.openPaperDetailModal(this.allPapers[idx - 1].id);
          }
        });
      }

      const btnNextModal = document.getElementById('btn-next-paper-modal');
      if (btnNextModal) {
        btnNextModal.addEventListener('click', () => {
          const idx = this.allPapers.findIndex(p => p.id === this.currentPaperModalId);
          if (idx >= 0 && idx < this.allPapers.length - 1) {
            this.openPaperDetailModal(this.allPapers[idx + 1].id);
          }
        });
      }

      const btnDeleteModal = document.getElementById('btn-delete-paper-modal');
      if (btnDeleteModal) {
        btnDeleteModal.addEventListener('click', async () => {
          if (!this.currentPaperModalId) return;
          if (confirm('Delete this paper from the database?')) {
            const targetId = this.currentPaperModalId;
            await fetch('/api/papers', {
              method: 'DELETE',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({ paper_ids: [targetId] }),
            });
            const idx = this.allPapers.findIndex(p => p.id === targetId);
            await this.loadHistory();
            if (this.allPapers.length > 0 && idx !== -1) {
              const nextIdx = Math.min(idx, this.allPapers.length - 1);
              this.openPaperDetailModal(this.allPapers[nextIdx].id);
            } else {
              document.getElementById('modal-paper-detail')?.classList.add('hidden');
            }
          }
        });
      }

      // Edit Schedule Modal Form (1:1 with New Evaluation)
      document.getElementById('form-edit-schedule')?.addEventListener('submit', async (e) => {
        e.preventDefault();
        try {
          const id = document.getElementById('edit-sch-id').value;
          const paperSource = document.getElementById('edit-sch-source').value;
          const isAcl = paperSource === 'acl';
          const fetchMode = document.querySelector('input[name="edit-sch-fetchmode"]:checked').value;
          const payload = {
            label: document.getElementById('edit-sch-label').value.trim(),
            problem_text: document.getElementById('edit-sch-problem').value.trim(),
            model_name: document.getElementById('edit-sch-model').value,
            paper_source: paperSource,
            acl_track: document.getElementById('edit-sch-acl-track').value,
            fetch_mode: fetchMode,
            max_papers: isAcl ? null : (fetchMode === 'count' ? parseInt(document.getElementById('edit-sch-papers').value) : null),
            days_back: isAcl ? null : (fetchMode === 'days' ? parseInt(document.getElementById('edit-sch-days-back').value) : null),
            keyword_filter: document.getElementById('edit-sch-keyword').value.trim(),
            min_score: parseInt(document.getElementById('edit-sch-min-score').value),
            max_concurrent: parseInt(document.getElementById('edit-sch-max-concurrent').value),
            run_time: document.getElementById('edit-sch-time').value,
          };
          const res = await fetch(`/api/schedules/${id}`, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload),
          });
          const data = await res.json().catch(() => ({}));
          if (!res.ok || !data.success) {
            throw new Error(data.detail || data.error || 'Failed to update schedule');
          }
          document.getElementById('modal-edit-schedule').classList.add('hidden');
          this.loadSchedules();
        } catch (err) {
          alert(`Failed to update schedule: ${err.message}`);
        }
      });

      document.getElementById('btn-cancel-edit')?.addEventListener('click', () => {
        document.getElementById('modal-edit-schedule').classList.add('hidden');
      });
    },

    autoFillScheduleForm(force = false) {
      const schProblem = document.getElementById('sch-problem');
      const schLabel = document.getElementById('sch-label');
      const sideProblem = document.getElementById('problem-statement')?.value.trim() || '';
      const sideModel = document.getElementById('model-name')?.value;
      const sideSource = document.getElementById('paper-source')?.value;
      const sideAclTrack = document.getElementById('acl-track')?.value;
      const sideKeyword = document.getElementById('keyword-filter')?.value;

      if (schProblem && (force || !schProblem.value.trim())) {
        schProblem.value = sideProblem || "Evaluate latest CS.CL & ACL 2026 papers matching high relevance criteria.";
      }

      if (schLabel && (force || !schLabel.value.trim())) {
        const titleExcerpt = sideProblem ? sideProblem.substring(0, 30) : "Daily Paper Matcher";
        schLabel.value = `Daily Evaluation: ${titleExcerpt}`;
      }

      if (sideModel && document.getElementById('sch-model')) {
        document.getElementById('sch-model').value = sideModel;
      }

      if (sideSource && document.getElementById('sch-source')) {
        document.getElementById('sch-source').value = sideSource;
        document.getElementById('sch-source').dispatchEvent(new Event('change'));
      }

      if (sideAclTrack && document.getElementById('sch-acl-track')) {
        document.getElementById('sch-acl-track').value = sideAclTrack;
      }

      if (sideKeyword && document.getElementById('sch-keyword')) {
        document.getElementById('sch-keyword').value = sideKeyword;
      }
    },

    bindPaperModal() {
      const modal = document.getElementById('modal-paper-detail');
      const btnClose = document.getElementById('btn-close-paper-modal');
      if (btnClose) btnClose.addEventListener('click', () => modal.classList.add('hidden'));
      if (modal) {
        modal.addEventListener('click', (e) => {
          if (e.target === modal) modal.classList.add('hidden');
        });
      }
    },

    // ── Live Stream Evaluation (SSE) ──
    async startLiveStreamEvaluation() {
      const problem = document.getElementById('problem-statement').value.trim();
      if (!problem) return alert('Please describe your research problem.');

      const model = document.getElementById('model-name').value;
      const paperSource = document.getElementById('paper-source')?.value || 'arxiv';
      const aclTrack = document.getElementById('acl-track')?.value || 'all';

      const fetchMode = document.querySelector('input[name="fetchmode"]:checked')?.value || 'count';
      const maxPapers = paperSource === 'acl' ? null : (fetchMode === 'count' ? parseInt(document.getElementById('max-papers').value) : null);
      const daysBack = fetchMode === 'days' ? parseInt(document.getElementById('days-back').value) : null;
      const keyword = document.getElementById('keyword-filter').value.trim();
      const concurrent = parseInt(document.getElementById('max-concurrent').value);

      const btnRunAll = document.getElementById('btn-run-all');
      const btnStop = document.getElementById('btn-stop-eval');
      const progressCard = document.getElementById('progress-card');
      const progressStage = document.getElementById('progress-stage');
      const progressFill = document.getElementById('progress-fill');
      const dashboard = document.getElementById('dashboard-container');

      btnRunAll.classList.add('hidden');
      btnStop.classList.remove('hidden');

      progressCard.classList.remove('hidden');
      progressStage.textContent = paperSource === 'acl'
        ? `Fetching papers from ACL 2026 Anthology (${aclTrack.toUpperCase()} Track)...`
        : 'Fetching papers from arXiv CS.CL...';
      progressFill.style.width = '0%';
      dashboard.classList.add('hidden');
      this.currentResults = [];

      this.abortController = new AbortController();

      try {
        const response = await fetch('/api/evaluate/stream', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          signal: this.abortController.signal,
          body: JSON.stringify({
            problem_statement: problem,
            model_name: model,
            paper_source: paperSource,
            acl_track: aclTrack,
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
        if (e.name === 'AbortError') {
          progressStage.textContent = '🛑 Evaluation stopped by user.';
        } else {
          progressStage.textContent = `❌ Stream error: ${e.message}`;
        }
      } finally {
        btnRunAll.classList.remove('hidden');
        btnStop.classList.add('hidden');
        this.abortController = null;
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

    // ── Background Evaluation ──
    async startBackgroundEvaluation() {
      const problem = document.getElementById('problem-statement').value.trim();
      if (!problem) return alert('Please describe your research problem.');

      const model = document.getElementById('model-name').value;
      const paperSource = document.getElementById('paper-source')?.value || 'arxiv';
      const aclTrack = document.getElementById('acl-track')?.value || 'all';

      const fetchMode = document.querySelector('input[name="fetchmode"]:checked')?.value || 'count';
      const maxPapers = paperSource === 'acl' ? null : (fetchMode === 'count' ? parseInt(document.getElementById('max-papers').value) : null);
      const daysBack = fetchMode === 'days' ? parseInt(document.getElementById('days-back').value) : null;
      const keyword = document.getElementById('keyword-filter').value.trim();
      const concurrent = parseInt(document.getElementById('max-concurrent').value);

      try {
        const response = await fetch('/api/evaluate/background', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            problem_statement: problem,
            model_name: model,
            paper_source: paperSource,
            acl_track: aclTrack,
            max_papers: maxPapers,
            days_back: daysBack,
            keyword_filter: keyword,
            max_concurrent: concurrent,
          }),
        });

        const data = await response.json();
        if (!response.ok) {
          alert('Error starting background evaluation: ' + (data.detail || data.error || 'Unknown error'));
          return;
        }

        const totalText = Number.isFinite(data.total_papers) ? `${data.total_papers} papers` : 'paper fetch in progress';
        alert(`🚀 Background Evaluation #${data.eval_id} launched (${totalText})!\n\nYou can track live progress in the History tab at any time.`);
        const historyTab = document.querySelector('.tab-btn[data-tab="tab-history"]');
        if (historyTab) historyTab.click();
        this.loadHistory();
      } catch (err) {
        alert('Failed to launch background evaluation: ' + err.message);
      }
    },

    async fetchPapersOnly() {
      const paperSource = document.getElementById('paper-source')?.value || 'arxiv';
      const aclTrack = document.getElementById('acl-track')?.value || 'all';
      const fetchMode = document.querySelector('input[name="fetchmode"]:checked')?.value || 'count';
      const maxPapers = paperSource === 'acl' ? null : (fetchMode === 'count' ? parseInt(document.getElementById('max-papers').value) : null);
      const daysBack = fetchMode === 'days' ? parseInt(document.getElementById('days-back').value) : null;
      const keyword = document.getElementById('keyword-filter').value.trim();

      try {
        const response = await fetch('/api/fetch-papers', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            paper_source: paperSource,
            acl_track: aclTrack,
            max_papers: maxPapers,
            days_back: daysBack,
            keyword_filter: keyword,
          }),
        });
        const data = await response.json().catch(() => ({}));
        if (!response.ok || !data.success) {
          throw new Error(data.detail || data.error || 'Failed to fetch papers');
        }

        alert(`Fetched ${data.count || 0} papers from ${paperSource.toUpperCase()}.`);
      } catch (err) {
        alert(`Fetch failed: ${err.message}`);
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
        this.cloudScheduler = data.cloud_scheduler || null;
        this.renderSchedules();
      } catch (e) { console.error('Schedules error:', e); }
    },

    renderSchedules() {
      // 1. Render GCP Cloud Scheduler Banner
      const banner = document.getElementById('cloud-scheduler-banner');
      if (banner) {
        banner.innerHTML = '';
        if (this.cloudScheduler) {
          const cs = this.cloudScheduler;
          const isPaused = cs.state === 'PAUSED';
          const isEnabled = cs.state === 'ENABLED';

          const card = document.createElement('div');
          card.className = 'card';
          if (isPaused) {
            card.style.cssText = 'background:var(--color-surface);border:2px solid var(--color-accent);border-left:6px solid var(--color-accent);padding:14px 18px;display:flex;justify-content:space-between;align-items:center;gap:12px;margin-bottom:12px';
            card.innerHTML = `
              <div>
                <div style="font-weight:800;font-size:14px;color:var(--color-accent-700)">
                  ⚠️ GCP Cloud Scheduler is PAUSED (${cs.job_name || 'hourly-paper-matcher-eval'})
                </div>
                <div style="font-size:12px;opacity:.75;margin-top:2px">
                  Daily recurring paper matching triggers are suspended on Cloud Run.
                </div>
              </div>
              <button id="btn-toggle-cloud-scheduler" class="btn btn-primary" style="white-space:nowrap">
                ▶️ Resume Cloud Scheduler
              </button>
            `;
          } else if (isEnabled) {
            card.style.cssText = 'background:var(--color-surface);border:1px solid var(--color-divider);border-left:6px solid oklch(42% 0.15 155);padding:12px 18px;display:flex;justify-content:space-between;align-items:center;gap:12px;margin-bottom:12px';
            card.innerHTML = `
              <div>
                <div style="font-weight:800;font-size:14px;color:oklch(42% 0.15 155)">
                  🟢 GCP Cloud Scheduler is ACTIVE (Hourly trigger)
                </div>
                <div style="font-size:12px;opacity:.75;margin-top:2px">
                  Job: ${cs.job_name} | Location: ${cs.location}
                </div>
              </div>
              <button id="btn-toggle-cloud-scheduler" class="btn btn-secondary" style="white-space:nowrap">
                ⏸️ Pause Cloud Scheduler
              </button>
            `;
          }
          if (isPaused || isEnabled) {
            banner.appendChild(card);
            const toggleBtn = card.querySelector('#btn-toggle-cloud-scheduler');
            if (toggleBtn) {
              toggleBtn.addEventListener('click', async () => {
                const targetState = isPaused; // resume if paused, pause if enabled
                try {
                  toggleBtn.disabled = true;
                  toggleBtn.textContent = 'Updating...';
                  const res = await fetch('/api/schedules/cloud-scheduler/toggle', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ active: targetState }),
                  });
                  const data = await res.json().catch(() => ({}));
                  if (!res.ok || !data.success) {
                    throw new Error(data.detail || data.error || 'Failed to update Cloud Scheduler');
                  }
                  await this.loadSchedules();
                } catch (err) {
                  alert(`Cloud Scheduler error: ${err.message}`);
                  await this.loadSchedules();
                }
              });
            }
          }
        }
      }

      // 2. Render Existing Schedules List
      const container = document.getElementById('schedules-list');
      container.innerHTML = '';

      if (!this.currentSchedules.length) {
        container.innerHTML = '<p style="opacity:.6">No recurring schedules created yet.</p>';
        return;
      }

      const csPaused = this.cloudScheduler && this.cloudScheduler.state === 'PAUSED';

      this.currentSchedules.forEach(s => {
        const statusDot = s.is_active ? (csPaused ? '🟡' : '🟢') : '⚪';
        const pausedBadge = (s.is_active && csPaused)
          ? `<span style="font-size:11px;font-weight:700;padding:2px 6px;background:var(--color-accent);color:#fff;border-radius:4px;margin-left:6px">⚠️ Trigger Suspended (Scheduler Paused)</span>`
          : '';
        const sourceLabel = (s.paper_source === 'acl')
          ? `ACL 2026 (${(s.acl_track || 'all').toUpperCase()} Track)`
          : 'arXiv CS.CL';
        const fetchLabel = s.fetch_mode === 'count'
          ? `${s.max_papers || 10} papers`
          : `last ${s.days_back || 7} days`;
        const kwLabel = s.keyword_filter ? ` | Keyword: "${s.keyword_filter}"` : '';

        const card = document.createElement('div');
        card.className = 'card';
        card.innerHTML = `
          <div style="display:flex;justify-content:space-between;align-items:flex-start;gap:12px">
            <div>
              <div style="font-weight:800;font-size:15px">${statusDot} #${s.id} · ${s.label || `Schedule #${s.id}`} · ⏰ ${s.run_time} daily ${pausedBadge}</div>
              <div style="font-size:12px;opacity:.65;margin-top:2px">
                📚 <strong>Source:</strong> ${sourceLabel} | 📦 <strong>Fetch:</strong> ${fetchLabel}${kwLabel} | ⚙️ <strong>Model:</strong> ${s.model_name}
              </div>
              <div style="font-size:12px;opacity:.65;margin-top:2px">
                🎯 <strong>Highlight Score:</strong> ≥${s.min_score || 6}/10 | ⚡ <strong>Workers:</strong> ${s.max_concurrent || 3}
              </div>
            </div>
            <div style="display:flex;gap:6px;flex-wrap:wrap;justify-content:flex-end">
              <button class="btn btn-secondary btn-run-sch" data-id="${s.id}">Run Now</button>
              <button class="btn btn-secondary btn-toggle-sch" data-id="${s.id}" data-active="${s.is_active}">${s.is_active ? 'Pause' : 'Activate'}</button>
              <button class="btn btn-secondary btn-edit-sch" data-id="${s.id}">Edit</button>
              <button class="btn btn-secondary btn-del-sch" data-id="${s.id}" style="color:var(--color-accent)">Delete</button>
            </div>
          </div>

          <div style="font-size:13px;margin-top:8px"><strong>Problem:</strong> ${s.problem_text}</div>
          ${s.last_run_at ? `<div style="font-size:11.5px;opacity:.55;margin-top:4px">Last run: ${s.last_run_at} — Status: ${s.last_status || 'N/A'}</div>` : ''}
        `;
        container.appendChild(card);
      });

      container.querySelectorAll('.btn-run-sch').forEach(b => b.addEventListener('click', async e => {
        const id = e.target.getAttribute('data-id');
        const btn = e.target;
        try {
          btn.disabled = true;
          btn.textContent = 'Launching...';
          const res = await fetch(`/api/schedules/${id}/run`, { method: 'POST' });
          const data = await res.json().catch(() => ({}));
          if (!res.ok || !data.success) {
            throw new Error(data.detail || data.error || 'Failed to trigger schedule run');
          }
          alert(`🚀 Evaluation run for schedule #${id} launched!\n\nSwitching to the History tab to track live progress.`);
          await this.loadSchedules();
          const historyTab = document.querySelector('.tab-btn[data-tab="tab-history"]');
          if (historyTab) historyTab.click();
          await this.loadHistory();
        } catch (err) {
          alert(`Failed to trigger schedule #${id}: ${err.message}`);
        } finally {
          btn.disabled = false;
          btn.textContent = 'Run Now';
        }
      }));

      container.querySelectorAll('.btn-toggle-sch').forEach(b => b.addEventListener('click', async e => {
        const id = e.target.getAttribute('data-id');
        const activeRaw = String(e.target.getAttribute('data-active') || '').toLowerCase();
        const active = activeRaw === '1' || activeRaw === 'true';
        const nextActive = !active;

        try {
          const res = await fetch(`/api/schedules/${id}/toggle`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ active: nextActive }),
          });
          const data = await res.json().catch(() => ({}));
          if (!res.ok || !data.success) {
            throw new Error(data.detail || data.error || 'Failed to update schedule');
          }
          await this.loadSchedules();
        } catch (err) {
          alert(`Failed to update schedule #${id}: ${err.message}`);
        }
      }));

      container.querySelectorAll('.btn-del-sch').forEach(b => b.addEventListener('click', async e => {
        const id = e.target.getAttribute('data-id');
        if (confirm('Delete schedule?')) {
          try {
            const res = await fetch(`/api/schedules/${id}`, { method: 'DELETE' });
            const data = await res.json().catch(() => ({}));
            if (!res.ok || !data.success) {
              throw new Error(data.detail || data.error || 'Failed to delete schedule');
            }
            await this.loadSchedules();
          } catch (err) {
            alert(`Failed to delete schedule #${id}: ${err.message}`);
          }
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
          document.getElementById('edit-sch-source').value = sch.paper_source || 'arxiv';
          document.getElementById('edit-sch-acl-track').value = sch.acl_track || 'all';
          document.getElementById('edit-sch-source').dispatchEvent(new Event('change'));

          const fetchMode = sch.fetch_mode || 'count';
          const radio = document.querySelector(`input[name="edit-sch-fetchmode"][value="${fetchMode}"]`);
          if (radio) {
            radio.checked = true;
            radio.dispatchEvent(new Event('change'));
          }

          document.getElementById('edit-sch-papers').value = sch.max_papers || 10;
          document.getElementById('edit-sch-val-papers').textContent = sch.max_papers || 10;
          document.getElementById('edit-sch-days-back').value = sch.days_back || 7;
          document.getElementById('edit-sch-val-days').textContent = sch.days_back || 7;
          document.getElementById('edit-sch-keyword').value = sch.keyword_filter || '';
          document.getElementById('edit-sch-min-score').value = sch.min_score || 6;
          document.getElementById('edit-sch-val-minscore').textContent = sch.min_score || 6;
          document.getElementById('edit-sch-max-concurrent').value = sch.max_concurrent || 3;
          document.getElementById('edit-sch-val-concurrent').textContent = sch.max_concurrent || 3;
          document.getElementById('edit-sch-time').value = sch.run_time || '08:00';

          document.getElementById('modal-edit-schedule').classList.remove('hidden');
        }
      }));
    },

    // ── History List & Paper-Centric Table ──
    async loadHistory() {
      try {
        const [resEval, resPapers] = await Promise.all([
          fetch('/api/evaluations'),
          fetch('/api/all-papers'),
        ]);
        const dataEval = await resEval.json();
        const dataPapers = await resPapers.json();

        this.pastEvaluations = dataEval.evaluations || [];
        this.allPapers = dataPapers.papers || [];

        this.selectedPaperIds.clear();
        this.updateBulkActionButtons();

        this.renderHistoryPapersTable();
        this.renderHistory();

        // Auto-poll History if any background evaluation is currently RUNNING
        const hasRunning = this.pastEvaluations.some(ev => ev.status === 'RUNNING');
        if (hasRunning) {
          if (this.historyPollTimer) clearTimeout(this.historyPollTimer);
          this.historyPollTimer = setTimeout(() => this.loadHistory(), 3500);
        }
      } catch (e) { console.error('History error:', e); }
    },

    escapeHtml(value) {
      return String(value ?? '')
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#039;');
    },

    paperGenerationMarkup(p, fontSize = '12px') {
      const hasJudgeOutput = (p.verdicts && p.verdicts.length) || (p.judge_scores && p.judge_scores.length);
      const legacyMissingScore = !p.generation_status && Number(p.avg_score || 0) === 0 && !hasJudgeOutput;
      const status = legacyMissingScore ? 'NO_SCORE' : String(p.generation_status || 'COMPLETED').toUpperCase();
      if (status === 'COMPLETED') {
        const score = Number(p.avg_score || 0);
        const scoreClass = score >= 7 ? 'high' : score >= 4 ? 'mid' : 'low';
        return `<span class="score-badge ${scoreClass}" style="font-size:${fontSize};padding:2px 8px">${score}/10</span>`;
      }

      const label = status === 'TIMED_OUT' ? 'Timed out' : status === 'NO_SCORE' ? 'No score generated' : 'Generation failed';
      const stage = this.escapeHtml(p.generation_stage || (status === 'NO_SCORE' ? 'Legacy run; stage unavailable' : 'Unknown stage'));
      const message = this.escapeHtml(p.generation_message || label);
      return `
        <div title="${message}" style="display:flex;flex-direction:column;align-items:flex-start;gap:3px">
          <span class="score-badge low" style="font-size:${fontSize};padding:2px 8px">${label}</span>
          <span style="font-size:11px;opacity:.72;max-width:180px;white-space:normal">During: ${stage}</span>
        </div>
      `;
    },

    createPaperRow(p) {
      const row = document.createElement('tr');
      row.style.borderBottom = '1px solid var(--color-divider)';

      const isChecked = this.selectedPaperIds.has(p.id);

      const title = p.title || 'Untitled';
      const authors = p.authors || 'Unknown';
      const problem = p.problem_text || '';
      const dateText = p.eval_date ? String(p.eval_date).split('T')[0] : '';

      row.innerHTML = `
        <td style="padding:10px">
          <input type="checkbox" class="chk-paper-item" data-id="${p.id}" ${isChecked ? 'checked' : ''} />
        </td>
        <td style="padding:10px">
          <div style="font-weight:700">${title}</div>
          <div style="font-size:12px;opacity:.65">${authors}</div>
        </td>
        <td style="padding:10px">
          ${this.paperGenerationMarkup(p)}
        </td>
        <td style="padding:10px;max-width:420px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis" title="${problem.replace(/"/g, '&quot;')}">${problem}</td>
        <td style="padding:10px">${dateText}</td>
        <td style="padding:10px;display:flex;gap:6px;flex-wrap:wrap">
          <button class="btn btn-secondary btn-view-paper" data-id="${p.id}" style="font-size:12px;padding:4px 8px">View</button>
          <button class="btn btn-secondary btn-del-paper" data-id="${p.id}" style="font-size:12px;padding:4px 8px;color:var(--color-accent)">Delete</button>
        </td>
      `;

      return row;
    },

    renderHistoryPapersTable() {
      const tbody = document.getElementById('table-history-papers-body');
      if (!tbody) return;
      tbody.innerHTML = '';

      const searchVal = (document.getElementById('history-search')?.value || '').toLowerCase().trim();
      const sourceFilter = document.getElementById('history-filter-source')?.value || 'all';
      const minScoreFilter = parseInt(document.getElementById('history-filter-min-score')?.value || '1');
      const maxScoreFilter = parseInt(document.getElementById('history-filter-max-score')?.value || '10');
      const sortVal = document.getElementById('history-sort')?.value || 'score-desc';

      let list = [...this.allPapers];

      if (searchVal) {
        list = list.filter(p =>
          (p.title || '').toLowerCase().includes(searchVal) ||
          (p.authors || '').toLowerCase().includes(searchVal) ||
          (p.abstract || '').toLowerCase().includes(searchVal) ||
          (p.problem_text || '').toLowerCase().includes(searchVal)
        );
      }

      if (sourceFilter === 'acl') {
        list = list.filter(p => (p.categories || '').includes('ACL'));
      } else if (sourceFilter === 'arxiv') {
        list = list.filter(p => !(p.categories || '').includes('ACL'));
      }

      if (minScoreFilter > 1 || maxScoreFilter < 10) {
        list = list.filter(p => (p.avg_score || 0) >= minScoreFilter && (p.avg_score || 0) <= maxScoreFilter);
      }

      // Sorting
      if (sortVal === 'score-desc') {
        list.sort((a, b) => (b.avg_score || 0) - (a.avg_score || 0));
      } else if (sortVal === 'score-asc') {
        list.sort((a, b) => (a.avg_score || 0) - (b.avg_score || 0));
      } else if (sortVal === 'problem-asc') {
        list.sort((a, b) => (a.problem_text || '').localeCompare(b.problem_text || ''));
      } else if (sortVal === 'source-asc') {
        list.sort((a, b) => (a.categories || '').localeCompare(b.categories || ''));
      } else if (sortVal === 'date-desc') {
        list.sort((a, b) => new Date(b.eval_date || 0) - new Date(a.eval_date || 0));
      } else if (sortVal === 'title-asc') {
        list.sort((a, b) => (a.title || '').localeCompare(b.title || ''));
      }

      if (!list.length) {
        tbody.innerHTML = '<tr><td colspan="6" style="padding:16px;text-align:center;opacity:.6">No papers match your filter criteria.</td></tr>';
        return;
      }

      list.forEach(p => tbody.appendChild(this.createPaperRow(p)));

      // Bind row checkboxes
      tbody.querySelectorAll('.chk-paper-item').forEach(cb => {
        cb.addEventListener('change', (e) => {
          const pid = parseInt(e.target.getAttribute('data-id'));
          if (e.target.checked) this.selectedPaperIds.add(pid);
          else this.selectedPaperIds.delete(pid);
          this.updateBulkActionButtons();
        });
      });

      // Bind View Details buttons
      tbody.querySelectorAll('.btn-view-paper').forEach(b => {
        b.addEventListener('click', (e) => {
          const pid = parseInt(e.target.getAttribute('data-id'));
          this.renderHistorySelectedPane([pid]);
        });
      });

      // Bind Single Delete buttons
      tbody.querySelectorAll('.btn-del-paper').forEach(b => {
        b.addEventListener('click', async (e) => {
          const pid = parseInt(e.target.getAttribute('data-id'));
          if (confirm('Delete this paper record from database?')) {
            await fetch('/api/papers', {
              method: 'DELETE',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({ paper_ids: [pid] }),
            });
            this.loadHistory();
          }
        });
      });
    },

    updateBulkActionButtons() {
      const count = this.selectedPaperIds.size;
      const countEl = document.getElementById('selected-papers-count');
      const btnDel = document.getElementById('btn-delete-selected-papers');
      const btnView = document.getElementById('btn-view-selected-papers');
      const chkSelectAll = document.getElementById('chk-select-all-papers');
      const totalCheckboxes = document.querySelectorAll('.chk-paper-item').length;

      if (countEl) countEl.textContent = `Selected (${count})`;
      if (btnDel) btnDel.disabled = (count === 0);
      if (btnView) btnView.disabled = (count === 0);
      if (chkSelectAll) chkSelectAll.checked = (totalCheckboxes > 0 && count === totalCheckboxes);
    },

    updateBulkActionButtonsForRuns() {
      const count = this.selectedEvalIds.size;
      const countEl = document.getElementById('selected-runs-count');
      const btnDel = document.getElementById('btn-delete-selected-runs');
      const chkSelectAll = document.getElementById('chk-select-all-runs');
      const totalCheckboxes = document.querySelectorAll('.chk-eval-run-item').length;

      if (countEl) countEl.textContent = `Selected (${count})`;
      if (btnDel) btnDel.disabled = (count === 0);
      if (chkSelectAll) chkSelectAll.checked = (totalCheckboxes > 0 && count === totalCheckboxes);
    },

    async deleteSelectedEvaluationRuns() {
      const ids = Array.from(this.selectedEvalIds);
      if (!ids.length) return;
      if (confirm(`Delete ${ids.length} selected evaluation run(s) and all associated papers?`)) {
        await fetch('/api/evaluations/delete-bulk', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ eval_ids: ids }),
        });
        this.selectedEvalIds.clear();
        this.loadHistory();
      }
    },

    async deleteSelectedPapers() {
      const ids = Array.from(this.selectedPaperIds);
      if (!ids.length) return;
      if (confirm(`Delete ${ids.length} selected paper(s) from database?`)) {
        await fetch('/api/papers', {
          method: 'DELETE',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ paper_ids: ids }),
        });
        this.selectedPaperIds.clear();
        this.loadHistory();
      }
    },

    async renderHistorySelectedPane(paperIds) {
      const pane = document.getElementById('history-selected-pane');
      const tableContainer = document.getElementById('history-table-container');
      const toolbarFilter = document.getElementById('history-filter-toolbar');
      const toolbarBulk = document.getElementById('history-bulk-toolbar');
      const titleEl = document.getElementById('history-pane-title');
      const contentEl = document.getElementById('history-pane-content');

      const ids = Array.isArray(paperIds) ? paperIds : [paperIds];
      if (!ids.length) return;
      this.currentPanePaperIds = ids;

      const primaryId = ids[0];
      const currentIndex = this.allPapers.findIndex(p => p.id === primaryId);
      const btnPanePrev = document.getElementById('btn-pane-prev-paper');
      const btnPaneNext = document.getElementById('btn-pane-next-paper');
      if (btnPanePrev) btnPanePrev.disabled = (currentIndex <= 0 || currentIndex === -1);
      if (btnPaneNext) btnPaneNext.disabled = (currentIndex >= this.allPapers.length - 1 || currentIndex === -1);

      if (tableContainer) tableContainer.classList.add('hidden');
      if (toolbarFilter) toolbarFilter.classList.add('hidden');
      if (toolbarBulk) toolbarBulk.classList.add('hidden');
      if (pane) pane.classList.remove('hidden');

      titleEl.textContent = ids.length === 1 ? 'Viewing Paper Details' : `Viewing Details for ${ids.length} Selected Papers`;
      contentEl.innerHTML = '<p style="opacity:.6">Loading paper details, 5-judge panel verdicts and debate transcripts...</p>';

      try {
        const { papers, errors } = await this.fetchPaperDetails(ids);

        let html = '';
        if (errors.length) {
          html += `
            <div class="card" style="border-left:4px solid var(--color-amber-500);margin-bottom:12px">
              <div style="font-size:12.5px"><strong>Some papers could not be loaded.</strong></div>
              <div style="font-size:12px;opacity:.8;margin-top:4px">${errors.join(' | ')}</div>
            </div>
          `;
        }
        papers.forEach(p => {
          const chipsHtml = (p.verdicts || []).map(j => {
            const cls = j.relevance_score >= 7 ? 'high' : j.relevance_score >= 4 ? 'mid' : 'low';
            return `<span class="judge-chip ${cls}">J${j.judge_run}: ${j.relevance_score}</span>`;
          }).join(' ');

          let judgesHtml = '';
          if (p.verdicts && p.verdicts.length) {
            judgesHtml = p.verdicts.map(j => {
              const cls = j.relevance_score >= 7 ? 'high' : j.relevance_score >= 4 ? 'mid' : 'low';
              const reasonsList = Array.isArray(j.key_reasons) ? j.key_reasons.map(r => `<li>${r}</li>`).join('') : '';
              return `
                <div class="debate-panel" style="background:var(--color-surface);border-left:3px solid var(--color-accent);margin-bottom:8px">
                  <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:4px">
                    <strong style="font-size:13px">⚖️ Judge #${j.judge_run} (Seed: ${j.seed || 'N/A'})</strong>
                    <span class="score-badge ${cls}" style="font-size:12px;padding:2px 6px">${j.relevance_score}/10</span>
                  </div>
                  <div style="font-size:12.5px;margin-bottom:4px"><strong>Verdict:</strong> ${j.verdict || 'N/A'}</div>
                  ${reasonsList ? `<ul style="margin:4px 0 4px 16px;padding:0;font-size:12px;opacity:.85">${reasonsList}</ul>` : ''}
                  ${j.suggested_use ? `<div style="font-size:12px;opacity:.8"><strong>Suggested Use:</strong> ${j.suggested_use}</div>` : ''}
                </div>
              `;
            }).join('');
          }

          let debatesHtml = '';
          if (p.debates && p.debates.length) {
            debatesHtml = p.debates.map((rnd, i) => `
              <div class="debate-panel debate-advocate" style="margin-bottom:8px">
                <div style="font-weight:800;font-size:12px;color:oklch(38% 0.15 155);margin-bottom:4px">🟢 Advocate (Round ${rnd.round_num || (i + 1)})</div>
                <div style="font-size:13px">${rnd.advocate_arg}</div>
              </div>
              <div class="debate-panel debate-skeptic" style="margin-bottom:8px">
                <div style="font-weight:800;font-size:12px;color:var(--color-accent);margin-bottom:4px">🔴 Skeptic (Round ${rnd.round_num || (i + 1)})</div>
                <div style="font-size:13px">${rnd.skeptic_arg}</div>
              </div>
            `).join('');
          }

          const isAclPaper = (p.categories || '').includes('ACL');
          const sourceBadge = isAclPaper
            ? '<span class="score-badge mid" style="font-size:11px;padding:2px 6px">ACL 2026</span>'
            : '<span class="score-badge high" style="font-size:11px;padding:2px 6px">arXiv CS.CL</span>';

          html += `
            <div class="card elev-md" style="display:flex;flex-direction:column;gap:14px;padding:20px;border-left:4px solid var(--color-accent)">
              <div style="display:flex;justify-content:space-between;align-items:flex-start;gap:12px">
                <div>
                  <div style="display:flex;align-items:center;gap:8px;margin-bottom:6px">
                    ${sourceBadge}
                    <span style="font-size:12px;opacity:.6">Evaluated: ${(p.eval_date || '').split('T')[0] || p.eval_date} using ${p.model_name || 'Gemini'}</span>
                  </div>
                  <h3 style="margin:0 0 6px">${p.title}</h3>
                  <div style="font-size:13px;opacity:.75">👤 <strong>Authors:</strong> ${p.authors}</div>
                  <a href="${p.url}" target="_blank" style="font-size:12.5px;color:var(--color-accent);text-decoration:none;margin-top:4px;display:inline-block">🔗 View Original Paper &rarr;</a>
                </div>
                <div style="display:flex;flex-direction:column;align-items:flex-end;gap:6px">
                  ${this.paperGenerationMarkup(p, '16px')}
                  <div style="display:flex;gap:4px;flex-wrap:wrap;justify-content:flex-end">${chipsHtml}</div>
                </div>
              </div>

              ${p.problem_text ? `
                <div style="padding:10px 14px;background:var(--color-surface);border:1px solid var(--color-divider)">
                  <strong style="font-size:12.5px">🎯 Evaluated Research Problem:</strong>
                  <div style="font-size:13px;margin-top:2px">${p.problem_text}</div>
                </div>
              ` : ''}

              <details style="border:1px solid var(--color-divider);padding:10px 14px" open>
                <summary style="font-weight:800;font-size:13px;cursor:pointer">📖 Abstract &amp; Technical Overview</summary>
                <p style="font-size:13px;line-height:1.5;margin:8px 0 0;opacity:.9">${p.abstract}</p>
                ${p.full_text ? `
                  <div style="margin-top:10px;padding-top:10px;border-top:1px dashed var(--color-divider);max-height:220px;overflow-y:auto;font-size:12px;opacity:.85;font-family:monospace;white-space:pre-wrap">
                    <strong>Full Extracted Paper Content:</strong>\n${p.full_text.substring(0, 3500)}...
                  </div>
                ` : ''}
              </details>

              ${judgesHtml ? `
                <div>
                  <h4 style="margin:0 0 8px;font-size:14px">⚖️ 5-Judge Panel Individual Verdicts</h4>
                  ${judgesHtml}
                </div>
              ` : ''}

              ${debatesHtml ? `
                <div>
                  <h4 style="margin:0 0 8px;font-size:14px">🗣️ Advocate vs. Skeptic Multi-Agent Debate Transcripts</h4>
                  ${debatesHtml}
                </div>
              ` : ''}
            </div>
          `;
        });

        contentEl.innerHTML = html;
      } catch (err) {
        contentEl.innerHTML = `<p style="color:var(--color-accent)">Failed to load paper details: ${err.message}</p>`;
      }
    },

    async openPaperDetailModal(paperIds) {
      const modal = document.getElementById('modal-paper-detail');
      const titleEl = document.getElementById('modal-paper-title');
      const contentEl = document.getElementById('modal-paper-content');

      const ids = Array.isArray(paperIds) ? paperIds : [paperIds];
      if (!ids.length) return;

      const primaryId = ids[0];
      this.currentPaperModalId = primaryId;

      modal.classList.remove('hidden');
      titleEl.textContent = ids.length === 1 ? 'Paper Details' : `Selected Papers Details (${ids.length} Papers)`;
      contentEl.innerHTML = '<p style="opacity:.6">Loading paper details, 5-judge panel verdicts and debate transcripts...</p>';

      const currentIndex = this.allPapers.findIndex(p => p.id === primaryId);
      const btnPrev = document.getElementById('btn-prev-paper-modal');
      const btnNext = document.getElementById('btn-next-paper-modal');
      const btnDel = document.getElementById('btn-delete-paper-modal');

      if (btnPrev) btnPrev.disabled = (currentIndex <= 0 || currentIndex === -1);
      if (btnNext) btnNext.disabled = (currentIndex >= this.allPapers.length - 1 || currentIndex === -1);
      if (btnDel) btnDel.disabled = (!this.currentPaperModalId);

      try {
        const { papers, errors } = await this.fetchPaperDetails(ids);

        if (ids.length === 1 && papers[0].title) {
          titleEl.textContent = papers[0].title;
        }

        let fullContentHtml = '';
        if (errors.length) {
          fullContentHtml += `
            <div class="card" style="border-left:4px solid var(--color-amber-500);margin-bottom:12px">
              <div style="font-size:12.5px"><strong>Some papers could not be loaded.</strong></div>
              <div style="font-size:12px;opacity:.8;margin-top:4px">${errors.join(' | ')}</div>
            </div>
          `;
        }

        papers.forEach((p, idx) => {
          const chipsHtml = (p.verdicts || []).map(j => {
            const cls = j.relevance_score >= 7 ? 'high' : j.relevance_score >= 4 ? 'mid' : 'low';
            return `<span class="judge-chip ${cls}">J${j.judge_run}: ${j.relevance_score}</span>`;
          }).join(' ');

          // Judge Transcripts HTML
          let judgesHtml = '';
          if (p.verdicts && p.verdicts.length) {
            judgesHtml = p.verdicts.map(j => {
              const cls = j.relevance_score >= 7 ? 'high' : j.relevance_score >= 4 ? 'mid' : 'low';
              const reasonsList = Array.isArray(j.key_reasons) ? j.key_reasons.map(r => `<li>${r}</li>`).join('') : '';
              return `
                <div class="debate-panel" style="background:var(--color-surface);border-left:3px solid var(--color-accent);margin-bottom:8px">
                  <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:4px">
                    <strong style="font-size:13px">⚖️ Judge #${j.judge_run} (Seed: ${j.seed || 'N/A'})</strong>
                    <span class="score-badge ${cls}" style="font-size:12px;padding:2px 6px">${j.relevance_score}/10</span>
                  </div>
                  <div style="font-size:12.5px;margin-bottom:4px"><strong>Verdict:</strong> ${j.verdict || 'N/A'}</div>
                  ${reasonsList ? `<ul style="margin:4px 0 4px 16px;padding:0;font-size:12px;opacity:.85">${reasonsList}</ul>` : ''}
                  ${j.suggested_use ? `<div style="font-size:12px;opacity:.8"><strong>Suggested Use:</strong> ${j.suggested_use}</div>` : ''}
                </div>
              `;
            }).join('');
          }

          // Debate Rounds HTML
          let debatesHtml = '';
          if (p.debates && p.debates.length) {
            debatesHtml = p.debates.map((rnd, i) => `
              <div class="debate-panel debate-advocate" style="margin-bottom:8px">
                <div style="font-weight:800;font-size:12px;color:oklch(38% 0.15 155);margin-bottom:4px">🟢 Advocate (Round ${rnd.round_num || (i + 1)})</div>
                <div style="font-size:13px">${rnd.advocate_arg}</div>
              </div>
              <div class="debate-panel debate-skeptic" style="margin-bottom:8px">
                <div style="font-weight:800;font-size:12px;color:var(--color-accent);margin-bottom:4px">🔴 Skeptic (Round ${rnd.round_num || (i + 1)})</div>
                <div style="font-size:13px">${rnd.skeptic_arg}</div>
              </div>
            `).join('');
          }

          fullContentHtml += `
            <div class="card paper-card-highlight" style="background:var(--color-bg);margin-bottom:20px;border-left:4px solid var(--color-accent)">
              <div style="display:flex;justify-content:space-between;align-items:flex-start;gap:12px">
                <div>
                  <div style="font-family:var(--font-heading);font-weight:800;font-size:16px">${ids.length > 1 ? `${idx + 1}. ` : ''}${p.title}</div>
                  <div style="font-size:12px;opacity:.6;margin-top:2px">👤 ${p.authors || 'Unknown'} · 📅 ${p.published || ''}</div>
                  <div style="font-size:12.5px;margin-top:4px">🎯 <strong>Research Problem:</strong> ${p.problem_text}</div>
                </div>
                ${this.paperGenerationMarkup(p, '15px')}
              </div>

              <div style="display:flex;align-items:center;gap:6px;margin:6px 0">
                ${chipsHtml}
                <span style="font-size:12px;opacity:.7">&rarr; Avg: ${p.avg_score}</span>
              </div>

              <div style="margin:6px 0">
                <h5 style="margin:0 0 2px;font-size:12.5px">Abstract</h5>
                <p style="font-size:12.5px;line-height:1.4;opacity:.85;margin:0">${p.abstract || 'No abstract available.'}</p>
              </div>

              <div style="display:flex;justify-content:space-between;align-items:center;margin-top:8px">
                <a href="${p.url}" target="_blank" class="btn btn-ghost" style="font-size:12.5px">Open Paper on arXiv &rarr;</a>
              </div>

              <details style="margin-top:10px" open>
                <summary style="cursor:pointer;font-size:13px;font-weight:800;color:var(--color-accent)">▼ Multi-Agent Debates &amp; 5-Judge Panel Transcripts</summary>
                <div style="margin-top:10px;display:flex;flex-direction:column;gap:12px">
                  ${debatesHtml ? `<div><h5 style="margin:0 0 6px">🗣️ Advocate vs. Skeptic Debates</h5>${debatesHtml}</div>` : ''}
                  ${judgesHtml ? `<div><h5 style="margin:8px 0 6px">⚖️ 5-Judge Panel Individual Verdicts</h5>${judgesHtml}</div>` : ''}
                </div>
              </details>
            </div>
          `;
        });

        contentEl.innerHTML = fullContentHtml;
      } catch (err) {
        contentEl.innerHTML = `<p style="color:var(--color-accent)">Failed to load paper details: ${err.message}</p>`;
      }
    },

    renderHistory() {
      const container = document.getElementById('history-list');
      if (!container) return;
      container.innerHTML = '';

      const searchVal = (document.getElementById('history-search')?.value || '').toLowerCase().trim();
      const minScoreFilter = parseInt(document.getElementById('history-filter-min-score')?.value || '1');
      const maxScoreFilter = parseInt(document.getElementById('history-filter-max-score')?.value || '10');

      let list = [...this.pastEvaluations];

      if (searchVal) {
        list = list.filter(ev =>
          (ev.problem_text || '').toLowerCase().includes(searchVal) ||
          (ev.model_name || '').toLowerCase().includes(searchVal) ||
          String(ev.id).includes(searchVal)
        );
      }

      if (minScoreFilter > 1 || maxScoreFilter < 10) {
        list = list.filter(ev => {
          // Keep in-progress/failed runs visible even when score filtering is active.
          const status = String(ev.status || 'COMPLETED').toUpperCase();
          if (status !== 'COMPLETED') return true;
          const avg = Number(ev.overall_avg || 0);
          return avg >= minScoreFilter && avg <= maxScoreFilter;
        });
      }

      if (!list.length) {
        container.innerHTML = '<p style="opacity:.6">No past evaluation runs match your filter.</p>';
        return;
      }

      list.forEach(ev => {
        const isRunning = ev.status === 'RUNNING';
        const isFailed = ev.status === 'FAILED';
        const isPartial = ev.status === 'PARTIAL';
        const isChecked = this.selectedEvalIds.has(ev.id);
        const statusBadge = isRunning
          ? `<span class="badge" style="background:var(--color-amber-500);color:#000;font-weight:700;padding:3px 8px;border-radius:12px">RUNNING (${ev.completed_papers || 0}/${ev.total_papers || '?'})</span>`
          : isFailed
          ? `<span class="badge" style="background:var(--color-accent);color:#fff;font-weight:700;padding:3px 8px;border-radius:12px">❌ FAILED</span>`
          : isPartial
          ? `<span class="badge" style="background:var(--color-amber-500);color:#000;font-weight:700;padding:3px 8px;border-radius:12px">PARTIAL (${ev.completed_papers || 0}/${ev.total_papers || '?'})</span>`
          : `<span class="badge" style="background:var(--color-emerald-500);color:#fff;font-weight:700;padding:3px 8px;border-radius:12px">✅ COMPLETED</span>`;

        const resumeBtnHtml = `<button class="btn btn-secondary btn-resume-eval" data-id="${ev.id}" style="margin-right:6px">▶️ Resume / Re-run</button>`;

        const card = document.createElement('div');
        card.className = 'card';
        if (isRunning) card.style.borderLeft = '4px solid var(--color-amber-500)';
        card.innerHTML = `
          <div style="display:flex;justify-content:space-between;align-items:center">
            <div style="display:flex;align-items:center;gap:10px">
              <input type="checkbox" class="chk-eval-run-item" data-id="${ev.id}" ${isChecked ? 'checked' : ''} style="cursor:pointer;transform:scale(1.15)" />
              <div style="font-weight:800;font-size:15px">Eval #${ev.id} — Overall: ${ev.overall_avg || 0}/10</div>
              ${statusBadge}
            </div>
            <div>
              ${resumeBtnHtml}
              <button class="btn btn-secondary btn-del-eval" data-id="${ev.id}" style="color:var(--color-accent)">Delete Run</button>
            </div>
          </div>
          <div style="font-size:13.5px;margin-top:4px"><strong>Problem:</strong> ${ev.problem_text}</div>
          <div style="font-size:12px;opacity:.6;margin-top:2px">Model: ${ev.model_name} | Date: ${ev.created_at} | Progress: ${ev.completed_papers || ev.paper_count}/${ev.total_papers || ev.paper_count} papers</div>
          ${isRunning && ev.generation_stage ? `<div style="font-size:12px;margin-top:4px"><strong>${this.escapeHtml(ev.generation_stage)}</strong> · ${this.escapeHtml(ev.generation_message || '')}</div>` : ''}

          <details style="margin-top:8px" class="past-eval-details" data-id="${ev.id}">
            <summary style="cursor:pointer;font-size:13px;font-weight:800;color:var(--color-accent)">▼ View Evaluated Papers &amp; Judge Transcripts for Eval #${ev.id}</summary>
            <div class="past-eval-papers-container" style="margin-top:10px;display:flex;flex-direction:column;gap:12px">
              <p style="font-size:12px;opacity:.6">Loading papers &amp; transcripts...</p>
            </div>
          </details>
        `;
        container.appendChild(card);
      });

      // Bind evaluation run checkboxes
      container.querySelectorAll('.chk-eval-run-item').forEach(cb => {
        cb.addEventListener('change', (e) => {
          const eid = parseInt(e.target.getAttribute('data-id'));
          if (e.target.checked) this.selectedEvalIds.add(eid);
          else this.selectedEvalIds.delete(eid);
          this.updateBulkActionButtonsForRuns();
        });
      });
      // Bind Resume / Re-run buttons
      container.querySelectorAll('.btn-resume-eval').forEach(b => {
        b.addEventListener('click', async (e) => {
          const id = parseInt(e.target.getAttribute('data-id'));
          const btn = e.target;
          try {
            btn.disabled = true;
            btn.textContent = 'Resuming...';
            const res = await fetch(`/api/evaluations/${id}/resume`, { method: 'POST' });
            const data = await res.json().catch(() => ({}));
            if (!res.ok || !data.status) {
              throw new Error(data.detail || data.error || 'Failed to resume evaluation');
            }
            alert(`🚀 Evaluation #${id} resumed!\n\n${data.message || 'Resumed evaluation run.'}`);
            this.loadHistory();
          } catch (err) {
            alert(`Failed to resume evaluation #${id}: ${err.message}`);
          } finally {
            btn.disabled = false;
            btn.textContent = '▶️ Resume / Re-run';
          }
        });
      });

      // Bind delete buttons
      container.querySelectorAll('.btn-del-eval').forEach(b => b.addEventListener('click', async e => {
        const id = parseInt(e.target.getAttribute('data-id'));
        if (confirm(`Delete evaluation run #${id}?`)) {
          await fetch(`/api/evaluations/${id}`, { method: 'DELETE' });
          this.selectedEvalIds.delete(id);
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
                      ${this.paperGenerationMarkup(p)}
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
