const $ = id => document.getElementById(id);

async function api(url, opt = {}) {
  const r = await fetch(url, {
    credentials: 'same-origin',
    ...opt
  });

  const d = await r.json().catch(() => ({}));

  if (!r.ok) {
    const e = new Error(
      d.error ||
      d.message ||
      `Request failed (${r.status})`
    );
    e.status = r.status;
    throw e;
  }

  return d;
}

function val(id) {
  return $(id)?.value || '';
}

function setButtonState(id, busy, label) {
  const b = $(id);
  if (!b) return;

  if (busy) {
    if (!b.dataset.originalText) {
      b.dataset.originalText = b.textContent;
    }

    b.disabled = true;
    b.classList.add('isBusy');
    b.textContent = label || 'Saving…';
  } else {
    b.disabled = false;
    b.classList.remove('isBusy');
    b.textContent = b.dataset.originalText || b.textContent;
  }
}

let noticeTimer;

function notice(message, type = 'info') {
  const box = $('adminNotice');
  if (!box) return;

  box.textContent = message;
  box.className = `adminNotice ${type} show`;

  clearTimeout(noticeTimer);

  noticeTimer = setTimeout(() => {
    box.classList.remove('show');
  }, 3500);
}

async function saveSettings(buttonId, payload, successMessage) {
  setButtonState(buttonId, true, 'Saving…');

  try {
    await api('/admin/api/settings', {
      method: 'PUT',
      headers: {
        'Content-Type': 'application/json'
      },
      body: JSON.stringify(payload)
    });

    notice(
      successMessage || 'Settings saved successfully.',
      'success'
    );

    await load();

  } catch (e) {
    notice(
      `Save failed: ${e.message}`,
      'error'
    );
  } finally {
    setButtonState(buttonId, false);
  }
}

async function load() {
  try {
    const s = await api('/admin/api/settings');

    const v = s.settings?.verification || {};
    const sh = v.shorteners || {};
    const se = s.settings?.search || {};

    $('verifyEnabled').checked = !!v.enabled;

    $('shortlinkEnabled').checked =
      v.shortlink_mode !== 'disabled';

    $('s1on').checked = !!sh['1']?.enabled;
    $('s2on').checked = !!sh['2']?.enabled;
    $('s3on').checked = !!sh['3']?.enabled;

    $('s1name').value = sh['1']?.name || '';
    $('s1api').value = sh['1']?.api || '';

    $('s2name').value = sh['2']?.name || '';
    $('s2api').value = sh['2']?.api || '';

    $('s3name').value = sh['3']?.name || '';
    $('s3api').value = sh['3']?.api || '';

    $('t1').value = v.tutorial_1 || '';
    $('t2').value = v.tutorial_2 || '';
    $('t3').value = v.tutorial_3 || '';

    $('v2').value = v.verification_time_2 || 0;
    $('v3').value = v.verification_time_3 || 0;

    $('validity').value =
      v.validity_hours || 24;

    $('maintenance').checked =
      !!s.settings?.site?.maintenance;

    $('maxResults').value =
      se.max_results || 20;

    $('resultsPerPage').value =
      se.results_per_page || 20;

    $('candidateLimit').value =
      se.candidate_limit || 120;

    $('spellCheck').checked =
      se.spell_check !== false;

    $('fuzzy').checked =
      se.fuzzy_fallback !== false;

    $('imdb').checked =
      !!se.imdb_poster;

    const f = s.settings?.files || {};
    const m = s.settings?.metadata || {};
    const pay = s.settings?.payments || {};

    $('fileSecure').checked =
      !!f.file_secure;

    $('autoDelete').checked =
      !!f.auto_delete;

    $('autoDeleteSeconds').value =
      f.auto_delete_seconds || 60;

    $('tmdbEnabled').checked =
      !!m.tmdb_enabled;

    $('posterFallback').checked =
      m.poster_fallback !== false;

    $('activationMode').value =
      pay.activation_mode || 'environment';

    $('premiumBypass').checked =
      pay.premium_bypass_verification !== false;

    $('state').textContent =
      JSON.stringify(s.settings, null, 2);

    $('panel').classList.remove('hidden');

    await loadRequests();

  } catch (e) {
    if (e.status === 401) {
      location.replace('/admin');
      return;
    }

    notice(
      `Could not load settings: ${e.message}`,
      'error'
    );

    $('state').textContent = e.message;
  }
}

$('logout').onclick = async () => {
  setButtonState(
    'logout',
    true,
    'Logging out…'
  );

  try {
    await api('/admin/logout', {
      method: 'POST'
    });

    location.reload();

  } catch (e) {
    setButtonState(
      'logout',
      false
    );

    notice(
      e.message,
      'error'
    );
  }
};

$('refresh').onclick = async () => {
  setButtonState(
    'refresh',
    true,
    'Refreshing…'
  );

  try {
    await load();

    notice(
      'Admin data refreshed.',
      'success'
    );

  } finally {
    setButtonState(
      'refresh',
      false
    );
  }
};

$('saveMaintenance').onclick = () =>
  saveSettings(
    'saveMaintenance',
    {
      site: {
        maintenance:
          $('maintenance').checked
      }
    },
    'Website settings saved.'
  );

$('saveSearch').onclick = () =>
  saveSettings(
    'saveSearch',
    {
      search: {
        max_results:
          Number(val('maxResults') || 20),

        results_per_page:
          Number(val('resultsPerPage') || 20),

        candidate_limit:
          Number(val('candidateLimit') || 120),

        spell_check:
          $('spellCheck').checked,

        fuzzy_fallback:
          $('fuzzy').checked,

        imdb_poster:
          $('imdb').checked
      }
    },
    'Search settings saved.'
  );

$('saveFiles').onclick = () =>
  saveSettings(
    'saveFiles',
    {
      files: {
        file_secure:
          $('fileSecure').checked,

        auto_delete:
          $('autoDelete').checked,

        auto_delete_seconds:
          Number(
            val('autoDeleteSeconds') || 60
          )
      },

      metadata: {
        tmdb_enabled:
          $('tmdbEnabled').checked,

        poster_fallback:
          $('posterFallback').checked
      }
    },
    'File and metadata settings saved.'
  );

$('saveVerify').onclick = () =>
  saveSettings(
    'saveVerify',
    {
      verification: {
        enabled:
          $('verifyEnabled').checked,

        shortlink_mode:
          $('shortlinkEnabled').checked
            ? 'enabled'
            : 'disabled',

        shorteners: {
          '1': {
            enabled:
              $('s1on').checked,
            name:
              val('s1name'),
            api:
              val('s1api')
          },

          '2': {
            enabled:
              $('s2on').checked,
            name:
              val('s2name'),
            api:
              val('s2api')
          },

          '3': {
            enabled:
              $('s3on').checked,
            name:
              val('s3name'),
            api:
              val('s3api')
          }
        },

        tutorial_1: val('t1'),
        tutorial_2: val('t2'),
        tutorial_3: val('t3'),

        verification_time_2:
          Number(val('v2') || 0),

        verification_time_3:
          Number(val('v3') || 0),

        validity_hours:
          Number(val('validity') || 24)
      }
    },
    'Verification settings saved.'
  );

$('savePayments').onclick = () =>
  saveSettings(
    'savePayments',
    {
      payments: {
        activation_mode:
          val('activationMode'),

        premium_bypass_verification:
          $('premiumBypass').checked,

        premium_bypass_shortener:
          $('premiumBypass').checked
      }
    },
    'Premium and payment settings saved.'
  );

async function loadRequests() {
  try {
    const d =
      await api('/admin/api/premium/manual');

    const box =
      $('manualRequests');

    box.innerHTML = '';

    if (!d.requests?.length) {
      box.innerHTML =
        '<p class="muted">No pending manual requests.</p>';

      return;
    }

    d.requests.forEach(r => {
      const el =
        document.createElement('div');

      el.className = 'request';

      el.innerHTML = `
        <b>${r.plan_name}</b>
        — ₹${r.amount}
        <br>
        <small>
          User: ${r.user_id}
          <br>
          Request: ${r.id}
        </small>

        <div>
          <a
            target="_blank"
            rel="noopener"
            href="/admin/api/premium/manual/${encodeURIComponent(r.id)}/proof"
          >
            View proof
          </a>

          <button
            type="button"
            data-a="approve"
          >
            Approve
          </button>

          <button
            type="button"
            data-a="reject"
          >
            Reject
          </button>
        </div>
      `;

      el
        .querySelectorAll('button')
        .forEach(b => {
          b.onclick = async () => {
            b.disabled = true;

            b.textContent =
              b.dataset.a === 'approve'
                ? 'Approving…'
                : 'Rejecting…';

            try {
              await api(
                '/admin/api/premium/manual/decide',
                {
                  method: 'POST',
                  headers: {
                    'Content-Type':
                      'application/json'
                  },
                  body: JSON.stringify({
                    request_id: r.id,
                    action: b.dataset.a
                  })
                }
              );

              notice(
                `Request ${b.dataset.a}d successfully.`,
                'success'
              );

              await loadRequests();

            } catch (e) {
              b.disabled = false;

              b.textContent =
                b.dataset.a === 'approve'
                  ? 'Approve'
                  : 'Reject';

              notice(
                `Action failed: ${e.message}`,
                'error'
              );
            }
          };
        });

      box.append(el);
    });

  } catch (e) {
    $('manualRequests').innerHTML =
      `<p class="muted">${e.message}</p>`;
  }
}

load();
