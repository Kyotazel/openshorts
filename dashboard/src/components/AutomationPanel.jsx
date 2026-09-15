import React, { useState, useEffect, useCallback } from 'react';
import {
  Radio, Plus, Trash2, Loader2, Send, RefreshCw, Youtube, Clock,
  AlertTriangle, Copy, Check, Play,
} from 'lucide-react';
import { apiJson } from '../lib/api';

// Autopilot: watch YouTube channels, and once a day turn whatever they
// published into clips, then POST each finished job's ZIP to an external API.
// Self-host only — the backend 404s in cloud mode, and App.jsx renders this
// only when billing is off.

const PENDING_LABEL = {
  new: 'waiting for the run hour',
  retry: 'will retry',
  queued: 'clip generator running',
  running: 'clip generator running',
  done: 'clips delivered',
  failed: 'failed',
  skip: 'skipped',
};

const DELIVERY_TONE = {
  pending: 'badge-warn',
  sending: 'badge-brass',
  sent: 'badge-ok',
  failed: 'badge-danger',
};

function pendingTone(status) {
  if (status === 'done') return 'badge-ok';
  if (status === 'failed' || status === 'skip') return 'badge-danger';
  if (status === 'queued' || status === 'running') return 'badge-brass';
  return 'badge-warn';
}

function fmtWhen(value) {
  if (!value) return '';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString(undefined, {
    month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit',
  });
}

export default function AutomationPanel() {
  const [data, setData] = useState(null);
  const [form, setForm] = useState(null);
  const [secret, setSecret] = useState('');
  const [clearSecret, setClearSecret] = useState(false);
  const [channelQuery, setChannelQuery] = useState('');
  const [busy, setBusy] = useState('');
  const [message, setMessage] = useState('');
  const [error, setError] = useState('');
  const [copied, setCopied] = useState(false);

  const load = useCallback(async () => {
    try {
      const d = await apiJson('/api/automation');
      setData(d);
      const s = d.settings || {};
      const delivery = s.delivery || {};
      setForm({
        enabled: !!s.enabled,
        run_hour: s.run_hour != null ? s.run_hour : 8,
        timezone: s.timezone || 'Asia/Jakarta',
        url: delivery.url || '',
        file_field: delivery.file_field || 'file',
        headers: (delivery.headers || []).map((h) => ({ name: h.name, value: h.value })),
      });
    } catch (e) {
      setError(e?.detail || 'Could not load the autopilot settings.');
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  const act = useCallback(async (key, fn) => {
    setBusy(key);
    setError('');
    setMessage('');
    try {
      await fn();
    } catch (e) {
      setError(e?.detail || 'Something went wrong.');
    }
    setBusy('');
  }, []);

  const saveSettings = useCallback(() => act('save', async () => {
    const delivery = {
      url: form.url,
      file_field: form.file_field,
      headers: form.headers.filter((h) => (h.name || '').trim()),
    };
    if (clearSecret) delivery.secret = '';
    else if (secret) delivery.secret = secret;
    await apiJson('/api/automation/settings', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        enabled: form.enabled,
        run_hour: Number(form.run_hour),
        timezone: form.timezone,
        delivery,
      }),
    });
    setSecret('');
    setClearSecret(false);
    setMessage('Settings saved.');
    await load();
  }), [act, form, secret, clearSecret, load]);

  const addChannel = useCallback(() => act('channel', async () => {
    const query = channelQuery.trim();
    if (!query) return;
    const res = await apiJson('/api/automation/channels', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ query }),
    });
    setChannelQuery('');
    setMessage(res.websub
      ? 'Channel added and registered for instant notifications.'
      : 'Channel added. Instant notifications are off, so it is checked on a timer.');
    await load();
  }), [act, channelQuery, load]);

  const removeChannel = useCallback((sub) => act('remove-' + sub.id, async () => {
    if (!window.confirm('Stop watching "' + (sub.title || sub.channel_id) + '"?')) return;
    await apiJson('/api/automation/channels/' + sub.id, { method: 'DELETE' });
    await load();
  }), [act, load]);

  const retryPending = useCallback((videoId) => act('pending-' + videoId, async () => {
    await apiJson('/api/automation/pending/' + videoId + '/retry', { method: 'POST' });
    setMessage('Queued again.');
    await load();
  }), [act, load]);

  const resendDelivery = useCallback((jobId) => act('delivery-' + jobId, async () => {
    await apiJson('/api/automation/deliveries/' + jobId + '/retry', { method: 'POST' });
    setMessage('Sending again.');
    await load();
  }), [act, load]);

  const runNow = useCallback(() => act('run-now', async () => {
    const res = await apiJson('/api/automation/run-now', { method: 'POST' });
    setMessage('Run started for ' + (res.pending || 0) + ' waiting video(s).');
    await load();
  }), [act, load]);

  const copyCallback = useCallback(() => {
    if (!data?.callback_url) return;
    navigator.clipboard?.writeText(data.callback_url)
      .then(() => { setCopied(true); setTimeout(() => setCopied(false), 1500); })
      .catch(() => {});
  }, [data]);

  if (!form || !data) {
    return (
      <div className="card p-6 flex justify-center">
        <Loader2 className="animate-spin text-brass" size={18} />
      </div>
    );
  }

  const pending = data.pending || [];
  const outbox = data.outbox || [];
  const subs = data.subscriptions || [];

  const setHeader = (index, key, value) => setForm((f) => ({
    ...f,
    headers: f.headers.map((h, i) => (i === index ? { ...h, [key]: value } : h)),
  }));

  return (
    <div className="space-y-6">
      <div className="card p-6">
        <div className="flex items-start justify-between gap-4 mb-2">
          <h3 className="font-display lowercase text-lg text-ink flex items-center gap-2">
            <Radio size={16} className="text-brass" /> Autopilot
          </h3>
          <label className="flex items-center gap-2 text-xs text-muted shrink-0 cursor-pointer">
            <input
              type="checkbox"
              checked={form.enabled}
              onChange={(e) => setForm((f) => ({ ...f, enabled: e.target.checked }))}
            />
            {form.enabled ? 'on' : 'off'}
          </label>
        </div>
        <p className="text-muted text-sm mb-4">
          Watch these YouTube channels. New uploads wait here, and once a day at the
          hour below they are turned into clips like any manual job. When a job
          finishes, its clips are zipped (clips + captions) and POSTed to your API.
        </p>

        {!data.callback_url && (
          <p className="text-warn text-xs mb-4 flex items-start gap-2">
            <AlertTriangle size={14} className="shrink-0 mt-0.5" />
            Set PUBLIC_API_URL to this server's public address to get instant
            YouTube notifications. Without it, channels are still checked every
            30 minutes.
          </p>
        )}

        <div className="grid sm:grid-cols-2 gap-4 mb-4">
          <div>
            <label className="eyebrow block mb-1.5">daily run hour (24h)</label>
            <select
              className="input-field w-full"
              value={form.run_hour}
              onChange={(e) => setForm((f) => ({ ...f, run_hour: Number(e.target.value) }))}
            >
              {Array.from({ length: 24 }, (_, h) => (
                <option key={h} value={h}>{String(h).padStart(2, '0')}:00</option>
              ))}
            </select>
          </div>
          <div>
            <label className="eyebrow block mb-1.5">timezone</label>
            <input
              className="input-field w-full"
              value={form.timezone}
              onChange={(e) => setForm((f) => ({ ...f, timezone: e.target.value }))}
              placeholder="Asia/Jakarta"
            />
          </div>
        </div>
        <p className="readout mb-4 flex items-center gap-1.5">
          <Clock size={12} /> last run: {data.schedule?.last_run_date || 'never'}
          {data.schedule?.last_run_date === data.local_today ? ' (today)' : ''}
        </p>

        <label className="eyebrow block mb-1.5">delivery URL</label>
        <input
          className="input-field w-full mb-4"
          value={form.url}
          onChange={(e) => setForm((f) => ({ ...f, url: e.target.value }))}
          placeholder="https://your-api.example.com/openshorts"
        />

        <div className="grid sm:grid-cols-2 gap-4 mb-4">
          <div>
            <label className="eyebrow block mb-1.5">form field name</label>
            <input
              className="input-field w-full"
              value={form.file_field}
              onChange={(e) => setForm((f) => ({ ...f, file_field: e.target.value }))}
              placeholder="file"
            />
          </div>
          <div>
            <label className="eyebrow block mb-1.5">signing secret (optional)</label>
            <div className="flex gap-2">
              <input
                type="password"
                className="input-field flex-1"
                value={secret}
                onChange={(e) => { setSecret(e.target.value); setClearSecret(false); }}
                placeholder={data.secret_set ? 'saved — type to replace' : 'none'}
              />
              {data.secret_set && !clearSecret && (
                <button
                  type="button"
                  className="btn-ghost px-3 shrink-0 text-xs"
                  onClick={() => { setClearSecret(true); setSecret(''); }}
                  title="Remove the stored secret"
                >
                  clear
                </button>
              )}
            </div>
          </div>
        </div>

        <label className="eyebrow block mb-1.5">extra headers</label>
        <div className="space-y-2 mb-2">
          {form.headers.map((h, i) => (
            <div key={i} className="flex gap-2">
              <input
                className="input-field flex-1"
                value={h.name}
                onChange={(e) => setHeader(i, 'name', e.target.value)}
                placeholder="Authorization"
              />
              <input
                className="input-field flex-[2]"
                value={h.value}
                onChange={(e) => setHeader(i, 'value', e.target.value)}
                placeholder="Bearer …"
              />
              <button
                type="button"
                className="text-muted hover:text-warn transition-colors shrink-0"
                onClick={() => setForm((f) => ({
                  ...f, headers: f.headers.filter((_, j) => j !== i),
                }))}
                title="Remove header"
              >
                <Trash2 size={15} />
              </button>
            </div>
          ))}
        </div>
        <button
          type="button"
          className="btn-ghost px-3 py-1.5 text-xs mb-5"
          onClick={() => setForm((f) => ({
            ...f, headers: [...f.headers, { name: '', value: '' }],
          }))}
        >
          <Plus size={13} /> add header
        </button>

        <div className="flex flex-wrap items-center gap-3">
          <button onClick={saveSettings} disabled={busy === 'save'} className="btn-primary py-2 px-4 text-sm">
            {busy === 'save' ? <Loader2 size={15} className="animate-spin" /> : <Check size={15} />} save settings
          </button>
          <button onClick={runNow} disabled={busy === 'run-now'} className="btn-ghost py-2 px-4 text-sm">
            {busy === 'run-now' ? <Loader2 size={15} className="animate-spin" /> : <Play size={15} />} run now
          </button>
        </div>
        {message && <p className="text-ok text-xs mt-3">{message}</p>}
        {error && <p className="text-danger text-xs mt-3">{error}</p>}
      </div>

      <div className="card p-6">
        <h3 className="font-display lowercase text-lg text-ink mb-1 flex items-center gap-2">
          <Youtube size={16} className="text-brass" /> Channels
        </h3>
        <p className="text-muted text-sm mb-4">
          Paste a channel URL or @handle. Only videos published after you add the
          channel are processed — nothing is backfilled.
        </p>

        {data.callback_url && (
          <div className="mb-4">
            <label className="eyebrow block mb-1.5">notification callback</label>
            <div className="flex items-center gap-2">
              <code className="readout flex-1 break-all select-all">{data.callback_url}</code>
              <button onClick={copyCallback} className="btn-ghost px-3 py-1.5 shrink-0">
                {copied ? <Check size={14} /> : <Copy size={14} />} {copied ? 'copied' : 'copy'}
              </button>
            </div>
          </div>
        )}

        <div className="flex gap-2 mb-4">
          <input
            className="input-field flex-1 text-sm"
            value={channelQuery}
            onChange={(e) => setChannelQuery(e.target.value)}
            onKeyDown={(e) => { if (e.key === 'Enter') addChannel(); }}
            placeholder="https://youtube.com/@channel or @handle"
          />
          <button onClick={addChannel} disabled={busy === 'channel'} className="btn-ghost px-4 py-2 shrink-0">
            {busy === 'channel' ? <Loader2 size={16} className="animate-spin" /> : <Plus size={16} />} add
          </button>
        </div>

        {subs.length === 0 ? (
          <p className="text-muted text-sm lowercase">no channels yet.</p>
        ) : (
          <div className="space-y-2">
            {subs.map((sub) => (
              <div key={sub.id} className="flex items-center justify-between gap-3 border border-rule rounded-card px-3 py-2">
                <div className="min-w-0">
                  <p className="text-sm text-ink truncate">{sub.title || sub.channel_id}</p>
                  <p className="readout truncate">
                    {sub.status === 'active' ? 'instant + timer'
                      : sub.status === 'unverified' ? 'instant (unverified)'
                      : 'timer only'}
                    {sub.handle ? ' · ' + sub.handle : ''}
                  </p>
                </div>
                <button
                  onClick={() => removeChannel(sub)}
                  className="text-muted hover:text-warn transition-colors shrink-0"
                  title="Stop watching"
                >
                  <Trash2 size={15} />
                </button>
              </div>
            ))}
          </div>
        )}
      </div>

      <div className="card p-6">
        <h3 className="font-display lowercase text-lg text-ink mb-1">Waiting to run</h3>
        <p className="text-muted text-sm mb-4">
          Videos found on your channels that have not been processed yet.
        </p>
        {pending.length === 0 ? (
          <p className="text-muted text-sm lowercase">nothing waiting.</p>
        ) : (
          <div className="space-y-2">
            {pending.map((item) => (
              <div key={item.video_id} className="flex items-center justify-between gap-3 border border-rule rounded-card px-3 py-2">
                <div className="min-w-0">
                  <p className="text-sm text-ink truncate" title={item.title}>{item.title || item.video_id}</p>
                  <p className="readout truncate">
                    {item.channel_title || 'channel'} · {fmtWhen(item.published)}
                    {item.last_error ? ' · ' + item.last_error : ''}
                  </p>
                </div>
                <div className="flex items-center gap-3 shrink-0">
                  <span className={pendingTone(item.status)}>{PENDING_LABEL[item.status] || item.status}</span>
                  {(item.status === 'failed' || item.status === 'skip') && (
                    <button
                      onClick={() => retryPending(item.video_id)}
                      className="text-muted hover:text-ink transition-colors"
                      title="Try again"
                    >
                      <RefreshCw size={14} />
                    </button>
                  )}
                </div>
              </div>
            ))}
          </div>
        )}
      </div>

      <div className="card p-6">
        <h3 className="font-display lowercase text-lg text-ink mb-1">Deliveries</h3>
        <p className="text-muted text-sm mb-4">
          One ZIP per finished video. Three automatic attempts, then re-send by hand.
        </p>
        {outbox.length === 0 ? (
          <p className="text-muted text-sm lowercase">no deliveries yet.</p>
        ) : (
          <div className="space-y-2">
            {outbox.map((state) => (
              <div key={state.job_id} className="flex items-center justify-between gap-3 border border-rule rounded-card px-3 py-2">
                <div className="min-w-0">
                  <p className="text-sm text-ink truncate">
                    {state.meta?.channel_title ? state.meta.channel_title + ' · ' : ''}
                    {state.job_id.slice(0, 8)}
                  </p>
                  <p className="readout truncate">
                    {fmtWhen(state.created_at)} · {state.meta?.clip_count || 0} clip(s)
                    {state.zip_size ? ' · ' + Math.round(state.zip_size / 1048576) + ' MB' : ''}
                    {state.last_error ? ' · ' + state.last_error : ''}
                  </p>
                </div>
                <div className="flex items-center gap-3 shrink-0">
                  <span className={DELIVERY_TONE[state.status] || 'badge-warn'}>
                    {state.status + (state.attempts ? ' (' + state.attempts + ')' : '')}
                  </span>
                  {state.status !== 'sent' && (
                    <button
                      onClick={() => resendDelivery(state.job_id)}
                      className="text-muted hover:text-ink transition-colors"
                      title="Send again"
                    >
                      <Send size={14} />
                    </button>
                  )}
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
