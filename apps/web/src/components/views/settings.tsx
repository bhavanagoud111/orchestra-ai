"use client";

import { CheckCircle2, KeyRound } from "lucide-react";
import { useEffect, useState, type ReactNode } from "react";
import { api } from "@/lib/api/client";
import { useGitHubStatus, useMe, useSettings, useTools } from "@/lib/api/hooks";
import type { DeviceStart, EffectiveSettings, SettingsUpdate } from "@/lib/api/types";
import { humanize } from "@/lib/format";
import { Badge, Button, Card, CardHeader, ErrorState, LoadingRows, Mono, PageHeader } from "@/components/ui/primitives";

function Field({ label, hint, children, htmlFor }: { label: string; hint?: ReactNode; children: ReactNode; htmlFor: string }) {
  return (
    <div className="grid gap-1 md:grid-cols-[220px_1fr] md:items-start md:gap-4">
      <div>
        <label htmlFor={htmlFor} className="text-[13px] text-text">{label}</label>
        {hint && <p className="text-xs text-muted">{hint}</p>}
      </div>
      <div>{children}</div>
    </div>
  );
}

const input = "h-8 w-full max-w-sm rounded-md border border-border bg-bg px-2.5 text-[13px] text-text placeholder:text-faint focus:border-accent focus:outline-none disabled:opacity-60";

function useSection(save: (body: SettingsUpdate) => Promise<void>) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [saved, setSaved] = useState(false);
  const run = async (body: SettingsUpdate) => {
    setBusy(true);
    setError(null);
    setSaved(false);
    try {
      await save(body);
      setSaved(true);
    } catch (err) {
      setError(err);
    } finally {
      setBusy(false);
    }
  };
  return { busy, error, saved, run };
}

function SaveRow({ busy, saved, error, onSave, disabled }: { busy: boolean; saved: boolean; error: unknown; onSave: () => void; disabled?: boolean }) {
  return (
    <div className="space-y-2 border-t border-border px-4 py-3">
      {error !== null && <ErrorState error={error} compact />}
      <div className="flex items-center justify-end gap-3">
        {saved && <span className="flex items-center gap-1 text-xs text-ok"><CheckCircle2 className="size-3.5" /> Saved</span>}
        <Button variant="primary" onClick={onSave} loading={busy} disabled={disabled}>Save</Button>
      </div>
    </div>
  );
}

function SecretInput({ id, configured, source, value, onChange, disabled }: {
  id: string;
  configured: boolean;
  source: string | null;
  value: string;
  onChange: (v: string) => void;
  disabled?: boolean;
}) {
  return (
    <div className="space-y-1">
      <input
        id={id}
        type="password"
        autoComplete="off"
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={configured ? "•••••••• (stored; enter a new value to replace)" : "Not configured"}
        disabled={disabled}
        className={input}
      />
      <p className="text-xs text-muted">
        {configured ? <>Configured via {source === "settings" ? "Settings (encrypted at rest)" : "environment variable"}. The value is never shown again.</> : "Write-only. Stored encrypted with ORCHESTRA_SECRET_KEY."}
      </p>
    </div>
  );
}

function GitHubConnection({ canEdit }: { canEdit: boolean }) {
  const { data: gh, mutate } = useGitHubStatus();
  const [device, setDevice] = useState<DeviceStart | null>(null);
  const [status, setStatus] = useState<string | null>(null);
  const [error, setError] = useState<unknown>(null);

  useEffect(() => {
    if (!device) return;
    const timer = setInterval(async () => {
      try {
        const res = await api.github.devicePoll(device.device_code);
        setStatus(res.status);
        if (res.status === "connected" || res.status === "expired_token" || res.status === "access_denied") {
          clearInterval(timer);
          setDevice(null);
          await mutate();
        }
      } catch (err) {
        setError(err);
        clearInterval(timer);
      }
    }, Math.max(device.interval, 5) * 1000);
    return () => clearInterval(timer);
  }, [device, mutate]);

  const start = async () => {
    setError(null);
    try {
      setDevice(await api.github.deviceStart());
      setStatus("pending");
    } catch (err) {
      setError(err);
    }
  };

  return (
    <div className="space-y-2 text-[13px]">
      {gh?.connected ? (
        <p className="flex items-center gap-2 text-text"><Badge tone="ok">Connected</Badge> as <Mono>{gh.user?.login}</Mono></p>
      ) : (
        <p className="flex items-center gap-2 text-muted"><Badge>Not connected</Badge>{gh?.error && <span className="text-xs text-err">{gh.error}</span>}</p>
      )}
      {gh?.device_flow_available && !gh.connected && canEdit && (
        device ? (
          <div className="rounded-md border border-border bg-panel-2 p-3">
            <p>Open <a className="text-accent hover:underline" href={device.verification_uri} target="_blank" rel="noreferrer">{device.verification_uri}</a> and enter:</p>
            <p className="my-2 font-mono text-lg tracking-widest text-text">{device.user_code}</p>
            <p className="text-xs text-muted">Waiting for authorization… ({status})</p>
          </div>
        ) : (
          <Button onClick={start}><KeyRound className="size-3.5" /> Sign in with GitHub</Button>
        )
      )}
      {error !== null && <ErrorState error={error} compact />}
      <p className="text-xs text-muted">
        Tokens are held by the API server only. Use a fine-grained token limited to the repositories you connect, with
        contents, issues and pull requests read/write. Orchestra never merges, force-pushes or deletes.
      </p>
    </div>
  );
}

export function SettingsView() {
  const { data, error, mutate } = useSettings();
  const { data: tools } = useTools();
  const { data: me } = useMe();
  const canEdit = me?.role === "admin";
  // Unsaved edits; falls back to the server's values until the user changes something.
  const [draft, setForm] = useState<EffectiveSettings | null>(null);
  const form = draft ?? data?.effective ?? null;
  const [secrets, setSecrets] = useState({ anthropic_api_key: "", github_token: "" });

  const save = async (body: SettingsUpdate) => {
    const updated = await api.settings.update(body);
    await mutate(updated, { revalidate: false });
    setForm(null);
  };
  const model = useSection(save);
  const secretSection = useSection(async (b) => {
    await save(b);
    setSecrets({ anthropic_api_key: "", github_token: "" });
  });
  const toolSection = useSection(save);
  const limits = useSection(save);

  if (error) return <ErrorState error={error} onRetry={() => mutate()} />;
  if (!data || !form) return <LoadingRows rows={8} label="Loading settings" />;
  const set = <K extends keyof EffectiveSettings>(key: K, value: EffectiveSettings[K]) => setForm({ ...form, [key]: value });

  return (
    <>
      <PageHeader
        title="Settings"
        description={
          <>
            Runtime configuration. Run mode is <Badge tone={form.mode === "demo" ? "sim" : "ok"}>{form.mode}</Badge> and is set with <Mono>ORCHESTRA_MODE</Mono> on the API server.
            {!canEdit && <> Changing settings requires the admin role.</>}
          </>
        }
      />
      <div className="space-y-4">
        <Card>
          <CardHeader title="Model provider" description="Used for agent reasoning in live mode. Demo mode never calls a model." />
          <div className="space-y-3 p-4">
            <Field label="Provider" htmlFor="provider"><select id="provider" className={input} value="anthropic" disabled><option value="anthropic">Anthropic</option></select></Field>
            <Field label="Model" htmlFor="model" hint="Anthropic model ID">
              <input id="model" className={`${input} font-mono`} value={form.model.model} disabled={!canEdit} onChange={(e) => set("model", { ...form.model, model: e.target.value })} />
            </Field>
            <Field label="Effort" htmlFor="effort" hint="Higher effort reasons more thoroughly and costs more">
              <select id="effort" className={input} value={form.model.effort} disabled={!canEdit} onChange={(e) => set("model", { ...form.model, effort: e.target.value as EffectiveSettings["model"]["effort"] })}>
                {["low", "medium", "high", "xhigh", "max"].map((x) => <option key={x}>{x}</option>)}
              </select>
            </Field>
          </div>
          {canEdit && <SaveRow {...model} onSave={() => model.run({ model: form.model })} />}
        </Card>

        <Card>
          <CardHeader title="Credentials" description="Secret values are write-only and never returned by the API." />
          <div className="space-y-4 p-4">
            {!data.secret_storage_available && (
              <p className="rounded-md border border-warn/30 bg-warn-soft px-3 py-2 text-xs text-warn">
                Set <Mono>ORCHESTRA_SECRET_KEY</Mono> on the API server to store secrets from this page. Environment variables still work.
              </p>
            )}
            <Field label="Anthropic API key" htmlFor="anthropic-key">
              <SecretInput id="anthropic-key" {...(data.secrets.anthropic_api_key as { configured: boolean; source: string | null })} value={secrets.anthropic_api_key} onChange={(v) => setSecrets({ ...secrets, anthropic_api_key: v })} disabled={!canEdit || !data.secret_storage_available} />
            </Field>
            <Field label="GitHub token" htmlFor="github-token">
              <SecretInput id="github-token" {...(data.secrets.github_token as { configured: boolean; source: string | null })} value={secrets.github_token} onChange={(v) => setSecrets({ ...secrets, github_token: v })} disabled={!canEdit || !data.secret_storage_available} />
            </Field>
            <Field label="GitHub connection" htmlFor="gh"><GitHubConnection canEdit={canEdit} /></Field>
          </div>
          {canEdit && (
            <SaveRow
              {...secretSection}
              disabled={!secrets.anthropic_api_key && !secrets.github_token}
              onSave={() => secretSection.run({ secrets: Object.fromEntries(Object.entries(secrets).filter(([, v]) => v)) as SettingsUpdate["secrets"] })}
            />
          )}
        </Card>

        <Card>
          <CardHeader title="Tool permissions" description="Disable a tool for every agent. Per-agent allowlists are fixed in code; write tools always need an approval." />
          {!tools ? <LoadingRows /> : (
            <div className="overflow-x-auto">
              <table className="w-full text-[13px]">
                <thead>
                  <tr className="border-b border-border text-left text-xs text-muted">
                    <th className="px-4 py-2 font-medium">Enabled</th>
                    <th className="px-3 py-2 font-medium">Tool</th>
                    <th className="px-3 py-2 font-medium">Permission</th>
                    <th className="px-3 py-2 font-medium">Source</th>
                    <th className="px-4 py-2 font-medium">Agents</th>
                  </tr>
                </thead>
                <tbody>
                  {tools.tools.map((t) => {
                    const disabled = form.tools.disabled ?? [];
                    const enabled = !disabled.includes(t.name);
                    return (
                      <tr key={t.name} className="border-b border-border/50 last:border-0">
                        <td className="px-4 py-1.5">
                          <input
                            type="checkbox"
                            aria-label={`Enable ${t.name}`}
                            checked={enabled}
                            disabled={!canEdit}
                            onChange={() => set("tools", { disabled: enabled ? [...disabled, t.name] : disabled.filter((x) => x !== t.name) })}
                          />
                        </td>
                        <td className="px-3 py-1.5"><Mono>{t.name}</Mono><p className="text-xs text-muted">{t.description}</p></td>
                        <td className="px-3 py-1.5"><Badge tone={t.permission === "write" ? "warn" : t.permission === "execute" ? "info" : "neutral"}>{t.permission}</Badge></td>
                        <td className="px-3 py-1.5 text-xs text-muted">{t.source}</td>
                        <td className="px-4 py-1.5 text-xs text-muted">{t.allowed_agents.map(humanize).join(", ") || "none"}</td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
              {Object.keys(tools.unavailable).length > 0 && (
                <div className="border-t border-border px-4 py-2 text-xs text-err">
                  Unavailable: {Object.entries(tools.unavailable).map(([k, v]) => `${k} (${v})`).join("; ")}
                </div>
              )}
            </div>
          )}
          {canEdit && <SaveRow {...toolSection} onSave={() => toolSection.run({ tools: form.tools })} />}
        </Card>

        <Card>
          <CardHeader title="Workflow limits and execution" />
          <div className="space-y-3 p-4">
            {(
              [
                ["max_graph_steps", "Max graph steps", "Upper bound on node executions per run"],
                ["workflow_timeout_s", "Workflow timeout (s)", "Wall-clock limit per running segment"],
                ["node_timeout_s", "Stage timeout (s)", "Limit for a single agent stage"],
                ["max_debug_revisions", "Fix revisions", "How many times a failed fix may be revised"],
                ["max_concurrent_workflows", "Concurrent workflows", "Applies after an API restart"],
              ] as const
            ).map(([key, label, hint]) => (
              <Field key={key} label={label} hint={hint} htmlFor={key}>
                <input id={key} type="number" className={`${input} w-32`} value={form.limits[key]} disabled={!canEdit}
                  onChange={(e) => set("limits", { ...form.limits, [key]: Number(e.target.value) })} />
              </Field>
            ))}
            <Field label="Run tests in sandbox" htmlFor="sandbox" hint="Requires Docker on the API host and the sandbox image">
              <label className="flex items-center gap-2 text-[13px] text-text">
                <input id="sandbox" type="checkbox" checked={form.preferences.sandbox_enabled} disabled={!canEdit}
                  onChange={(e) => set("preferences", { ...form.preferences, sandbox_enabled: e.target.checked })} />
                Execute the repository&apos;s tests in an isolated container (no network, read-only, CPU/memory limits)
              </label>
            </Field>
            <Field label="Require tests for pull requests" htmlFor="require-tests" hint="Block draft PRs unless sandbox tests ran and passed">
              <label className="flex items-center gap-2 text-[13px] text-text">
                <input id="require-tests" type="checkbox" checked={Boolean(form.preferences.require_tests_for_pr)} disabled={!canEdit}
                  onChange={(e) => set("preferences", { ...form.preferences, require_tests_for_pr: e.target.checked })} />
                Only propose a pull request when the fix passed tests in the sandbox
              </label>
            </Field>
          </div>
          {canEdit && <SaveRow {...limits} onSave={() => limits.run({ limits: form.limits, preferences: form.preferences })} />}
        </Card>
      </div>
    </>
  );
}
