// client/src/components/IntakeForm.tsx
import { useState, FormEvent } from "react";
import { submitIntake, IntakeResponse } from "../api";

const DOMAINS = [
  "meta",
  "engineering",
  "research",
  "writing",
  "ops",
  "personal",
  "other",
] as const;

const URGENCIES = [
  "idle-opportunistic",
  "this-week",
  "today",
  "now",
] as const;

type SubmitState =
  | { kind: "idle" }
  | { kind: "submitting" }
  | { kind: "success"; data: IntakeResponse }
  | { kind: "error"; message: string };

export function IntakeForm() {
  const [goal, setGoal] = useState("");
  const [domain, setDomain] = useState<string>("meta");
  const [urgency, setUrgency] = useState<string>("idle-opportunistic");
  const [state, setState] = useState<SubmitState>({ kind: "idle" });

  const canSubmit =
    state.kind !== "submitting" && goal.trim().length > 0;

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    if (!canSubmit) return;

    setState({ kind: "submitting" });
    try {
      const data = await submitIntake({ goal, domain, urgency });
      setState({ kind: "success", data });
      setGoal("");
    } catch (err) {
      const message =
        err instanceof Error ? err.message : "Unknown error";
      setState({ kind: "error", message });
    }
  }

  return (
    <form className="intake-form" onSubmit={handleSubmit}>
      <h1 className="intake-title">Job-Star Intake</h1>
      <p className="intake-subtitle">
        Describe what you want done. Attachments and voice come later.
      </p>

      <label className="field">
        <span className="field-label">Goal</span>
        <textarea
          className="field-input field-textarea"
          value={goal}
          onChange={(e) => setGoal(e.target.value)}
          placeholder="What do you want Job-Star to do?"
          rows={6}
          required
          autoFocus
        />
      </label>

      <div className="field-row">
        <label className="field">
          <span className="field-label">Domain</span>
          <select
            className="field-input"
            value={domain}
            onChange={(e) => setDomain(e.target.value)}
          >
            {DOMAINS.map((d) => (
              <option key={d} value={d}>
                {d}
              </option>
            ))}
          </select>
        </label>

        <label className="field">
          <span className="field-label">Urgency</span>
          <select
            className="field-input"
            value={urgency}
            onChange={(e) => setUrgency(e.target.value)}
          >
            {URGENCIES.map((u) => (
              <option key={u} value={u}>
                {u}
              </option>
            ))}
          </select>
        </label>
      </div>

      <div className="form-actions">
        <button
          type="submit"
          className="btn-primary"
          disabled={!canSubmit}
        >
          {state.kind === "submitting" ? "Submitting…" : "Submit"}
        </button>
      </div>

      {state.kind === "success" && (
        <div className="status status-success">
          Received. ID: <code>{state.data.id}</code>
        </div>
      )}
      {state.kind === "error" && (
        <div className="status status-error">{state.message}</div>
      )}
    </form>
  );
}


// --- DUPLICATE BLOCK ---

import React, { useCallback, useMemo, useState } from 'react';
import { FileUpload, UploadedFile } from './FileUpload';

interface IntakeFormState {
  jobDescription: string;
  role: string;
  company: string;
  notes: string;
}

const INITIAL_STATE: IntakeFormState = {
  jobDescription: '',
  role: '',
  company: '',
  notes: '',
};

type Status = 'idle' | 'submitting' | 'success' | 'error';

export const IntakeForm: React.FC = () => {
  const [form, setForm] = useState<IntakeFormState>(INITIAL_STATE);
  const [files, setFiles] = useState<UploadedFile[]>([]);
  const [status, setStatus] = useState<Status>('idle');
  const [error, setError] = useState<string | null>(null);
  const [responseId, setResponseId] = useState<string | null>(null);

  const updateField = useCallback(
    (field: keyof IntakeFormState) => (
      e: React.ChangeEvent<HTMLInputElement | HTMLTextAreaElement>
    ) => {
      setForm((prev) => ({ ...prev, [field]: e.target.value }));
    },
    []
  );

  const canSubmit = useMemo(
    () =>
      status !== 'submitting' &&
      (form.jobDescription.trim().length > 0 ||
        form.role.trim().length > 0 ||
        form.notes.trim().length > 0 ||
        files.length > 0),
    [status, form, files]
  );

  const handleSubmit = useCallback(
    async (e: React.FormEvent) => {
      e.preventDefault();
      if (status === 'submitting') return;

      setStatus('submitting');
      setError(null);
      setResponseId(null);

      const data = new FormData();
      data.append('jobDescription', form.jobDescription);
      data.append('role', form.role);
      data.append('company', form.company);
      data.append('notes', form.notes);

      for (const { file } of files) {
        data.append('files', file, file.name);
      }

      try {
        const res = await fetch('/api/intake', {
          method: 'POST',
          body: data,
        });
        if (!res.ok) {
          const text = await res.text();
          throw new Error(`Request failed (${res.status}): ${text}`);
        }
        const json = (await res.json()) as { id?: string };
        setResponseId(json.id ?? null);
        setStatus('success');
        setForm(INITIAL_STATE);
        setFiles([]);
      } catch (err) {
        setStatus('error');
        setError(err instanceof Error ? err.message : String(err));
      }
    },
    [form, files, status]
  );

  return (
    <form className="intake-form" onSubmit={handleSubmit}>
      <h2 className="intake-form__title">Job Intake</h2>

      <label className="intake-form__field">
        <span className="intake-form__label">Role</span>
        <input
          type="text"
          value={form.role}
          onChange={updateField('role')}
          placeholder="e.g. Senior Backend Engineer"
        />
      </label>

      <label className="intake-form__field">
        <span className="intake-form__label">Company</span>
        <input
          type="text"
          value={form.company}
          onChange={updateField('company')}
          placeholder="e.g. Acme Corp"
        />
      </label>

      <label className="intake-form__field">
        <span className="intake-form__label">Job Description</span>
        <textarea
          value={form.jobDescription}
          onChange={updateField('jobDescription')}
          rows={8}
          placeholder="Paste the full job description here…"
        />
      </label>

      <label className="intake-form__field">
        <span className="intake-form__label">Notes</span>
        <textarea
          value={form.notes}
          onChange={updateField('notes')}
          rows={4}
          placeholder="Context, requirements, anything else worth capturing…"
        />
      </label>

      <div className="intake-form__field">
        <span className="intake-form__label">Attachments</span>
        <FileUpload files={files} onFilesChange={setFiles} />
      </div>

      <div className="intake-form__actions">
        <button type="submit" disabled={!canSubmit}>
          {status === 'submitting' ? 'Submitting…' : 'Submit Intake'}
        </button>
      </div>

      {status === 'success' && responseId && (
        <p className="intake-form__status intake-form__status--success">
          Intake received (id: <code>{responseId}</code>).
        </p>
      )}
      {status === 'error' && error && (
        <p className="intake-form__status intake-form__status--error">
          {error}
        </p>
      )}
    </form>
  );
};

export default IntakeForm;


// --- DUPLICATE BLOCK ---

import React, { useCallback, useMemo, useRef, useState } from 'react';
import { FileUpload, UploadedFile } from './FileUpload';
import { ScreenshotCapture, CapturedImage } from './ScreenshotCapture';

interface IntakeFormData {
  title: string;
  description: string;
  severity: 'low' | 'medium' | 'high';
  tags: string[];
}

const INITIAL_FORM: IntakeFormData = {
  title: '',
  description: '',
  severity: 'medium',
  tags: [],
};

export const IntakeForm: React.FC = () => {
  const [form, setForm] = useState<IntakeFormData>(INITIAL_FORM);
  const [files, setFiles] = useState<UploadedFile[]>([]);
  const [images, setImages] = useState<CapturedImage[]>([]);
  const [tagInput, setTagInput] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);

  const updateField = <K extends keyof IntakeFormData>(
    key: K,
    value: IntakeFormData[K]
  ) => {
    setForm((prev) => ({ ...prev, [key]: value }));
  };

  const addTag = () => {
    const trimmed = tagInput.trim();
    if (trimmed && !form.tags.includes(trimmed)) {
      updateField('tags', [...form.tags, trimmed]);
    }
    setTagInput('');
  };

  const removeTag = (tag: string) => {
    updateField(
      'tags',
      form.tags.filter((t) => t !== tag)
    );
  };

  const handleAddImages = useCallback((next: CapturedImage[]) => {
    setImages((prev) => [...prev, ...next]);
  }, []);

  const handleRemoveImage = useCallback((id: string) => {
    setImages((prev) => {
      const target = prev.find((img) => img.id === id);
      if (target) URL.revokeObjectURL(target.previewUrl);
      return prev.filter((img) => img.id !== id);
    });
  }, []);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);
    setSuccess(null);

    if (!form.title.trim()) {
      setError('Title is required.');
      return;
    }

    setSubmitting(true);
    try {
      const fd = new FormData();
      fd.append('title', form.title);
      fd.append('description', form.description);
      fd.append('severity', form.severity);
      fd.append('tags', JSON.stringify(form.tags));

      // Attach generic uploaded files.
      files.forEach((f) => fd.append('files', f.file, f.file.name));

      // Attach screenshot/pasted images under a distinct field name so the
      // backend can categorize them as visual context.
      images.forEach((img) => fd.append('screenshots', img.file, img.file.name));

      const res = await fetch('/api/intake', {
        method: 'POST',
        body: fd,
      });

      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body.error || `Request failed (${res.status})`);
      }

      const data = await res.json();
      setSuccess(`Intake created: ${data.id ?? '(unknown id)'}`);

      // Reset state.
      setForm(INITIAL_FORM);
      setFiles([]);
      images.forEach((img) => URL.revokeObjectURL(img.previewUrl));
      setImages([]);
      setTagInput('');
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Submission failed.');
    } finally {
      setSubmitting(false);
    }
  };

  const canSubmit = useMemo(
    () => form.title.trim().length > 0 && !submitting,
    [form.title, submitting]
  );

  return (
    <form className="intake-form" onSubmit={handleSubmit}>
      <h2 className="intake-form__heading">New Intake</h2>

      {error && <div className="intake-form__error">{error}</div>}
      {success && <div className="intake-form__success">{success}</div>}

      <label className="intake-form__field">
        <span className="intake-form__label">Title</span>
        <input
          type="text"
          className="intake-form__input"
          value={form.title}
          onChange={(e) => updateField('title', e.target.value)}
          placeholder="Short summary of the issue"
          required
        />
      </label>

      <label className="intake-form__field">
        <span className="intake-form__label">Description</span>
        <textarea
          className="intake-form__textarea"
          value={form.description}
          onChange={(e) => updateField('description', e.target.value)}
          rows={5}
          placeholder="What happened? What did you expect?"
        />
      </label>

      <label className="intake-form__field">
        <span className="intake-form__label">Severity</span>
        <select
          className="intake-form__select"
          value={form.severity}
          onChange={(e) =>
            updateField('severity', e.target.value as IntakeFormData['severity'])
          }
        >
          <option value="low">Low</option>
          <option value="medium">Medium</option>
          <option value="high">High</option>
        </select>
      </label>

      <div className="intake-form__field">
        <span className="intake-form__label">Tags</span>
        <div className="intake-form__tags">
          {form.tags.map((tag) => (
            <span key={tag} className="intake-form__tag">
              {tag}
              <button
                type="button"
                className="intake-form__tag-remove"
                onClick={() => removeTag(tag)}
                aria-label={`Remove tag ${tag}`}
              >
                ×
              </button>
            </span>
          ))}
          <input
            type="text"
            className="intake-form__tag-input"
            value={tagInput}
            onChange={(e) => setTagInput(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter') {
                e.preventDefault();
                addTag();
              }
            }}
            placeholder="Add tag, press Enter"
          />
        </div>
      </div>

      <div className="intake-form__section">
        <ScreenshotCapture
          images={images}
          onAdd={handleAddImages}
          onRemove={handleRemoveImage}
        />
      </div>

      <div className="intake-form__section">
        <FileUpload files={files} onAdd={setFiles} />
      </div>

      <div className="intake-form__actions">
        <button
          type="submit"
          className="intake-form__submit"
          disabled={!canSubmit}
        >
          {submitting ? 'Submitting…' : 'Submit Intake'}
        </button>
      </div>
    </form>
  );
};

export default IntakeForm;


// --- DUPLICATE BLOCK ---

import { useCallback, useRef, useState } from 'react';
import { FileUpload, UploadedFile } from './FileUpload';
import { ScreenshotCapture, PastedScreenshot } from './ScreenshotCapture';
import { VoiceRecorder, VoiceRecording } from './VoiceRecorder';
import { ScreenCapture, CapturedScreen } from './ScreenCapture';

interface IntakeFormProps {
  onSubmitted?: (id: string) => void;
}

interface FormState {
  title: string;
  description: string;
  context: string;
  priority: 'low' | 'normal' | 'high';
}

const INITIAL_FORM: FormState = {
  title: '',
  description: '',
  context: '',
  priority: 'normal',
};

export function IntakeForm({ onSubmitted }: IntakeFormProps) {
  const [form, setForm] = useState<FormState>(INITIAL_FORM);
  const [files, setFiles] = useState<UploadedFile[]>([]);
  const [screenshots, setScreenshots] = useState<PastedScreenshot[]>([]);
  const [recordings, setRecordings] = useState<VoiceRecording[]>([]);
  const [screenCaptures, setScreenCaptures] = useState<CapturedScreen[]>([]);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [successId, setSuccessId] = useState<string | null>(null);

  const handleChange = useCallback(
    (field: keyof FormState) =>
      (
        e: React.ChangeEvent<
          HTMLInputElement | HTMLTextAreaElement | HTMLSelectElement
        >,
      ) => {
        setForm((prev) => ({ ...prev, [field]: e.target.value }));
      },
    [],
  );

  const handleFilesAdded = useCallback((added: UploadedFile[]) => {
    setFiles((prev) => [...prev, ...added]);
  }, []);

  const handleFileRemoved = useCallback((id: string) => {
    setFiles((prev) => prev.filter((f) => f.id !== id));
  }, []);

  const handleScreenshot = useCallback((shot: PastedScreenshot) => {
    setScreenshots((prev) => [...prev, shot]);
  }, []);

  const handleScreenshotRemove = useCallback((id: string) => {
    setScreenshots((prev) => prev.filter((s) => s.id !== id));
  }, []);

  const handleRecording = useCallback((rec: VoiceRecording) => {
    setRecordings((prev) => [...prev, rec]);
  }, []);

  const handleRecordingRemove = useCallback((id: string) => {
    setRecordings((prev) => prev.filter((r) => r.id !== id));
  }, []);

  const handleScreenCapture = useCallback((cap: CapturedScreen) => {
    setScreenCaptures((prev) => [...prev, cap]);
  }, []);

  const handleScreenCaptureRemove = useCallback((id: string) => {
    setScreenCaptures((prev) => {
      const target = prev.find((c) => c.id === id);
      if (target) URL.revokeObjectURL(target.previewUrl);
      return prev.filter((c) => c.id !== id);
    });
  }, []);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (submitting) return;

    setError(null);
    setSubmitting(true);

    try {
      const fd = new FormData();
      fd.append('title', form.title);
      fd.append('description', form.description);
      fd.append('context', form.context);
      fd.append('priority', form.priority);

      files.forEach((f) => fd.append('files', f.file, f.file.name));
      screenshots.forEach((s) => fd.append('files', s.file, s.file.name));
      recordings.forEach((r) => fd.append('files', r.file, r.file.name));
      screenCaptures.forEach((c) => fd.append('files', c.file, c.file.name));

      const res = await fetch('/api/intake', { method: 'POST', body: fd });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body.error || `Submission failed (${res.status})`);
      }
      const data = (await res.json()) as { id: string };

      // Cleanup object URLs.
      [...screenshots, ...screenCaptures].forEach((x) =>
        URL.revokeObjectURL(x.previewUrl),
      );

      setSuccessId(data.id);
      setForm(INITIAL_FORM);
      setFiles([]);
      setScreenshots([]);
      setRecordings([]);
      setScreenCaptures([]);
      onSubmitted?.(data.id);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Unknown error.');
    } finally {
      setSubmitting(false);
    }
  };

  const totalAttachments =
    files.length + screenshots.length + recordings.length + screenCaptures.length;

  return (
    <form className="intake-form" onSubmit={handleSubmit}>
      <header className="intake-form__header">
        <h2>New Job Intake</h2>
        <p className="intake-form__subtitle">
          Capture a task with text, files, screenshots, voice, and screen
          captures.
        </p>
      </header>

      {error && <div className="alert alert--error">{error}</div>}
      {successId && (
        <div className="alert alert--success">
          Submitted (id: <code>{successId}</code>)
        </div>
      )}

      <fieldset className="intake-form__section">
        <legend>Details</legend>

        <label className="field">
          <span className="field__label">Title</span>
          <input
            type="text"
            value={form.title}
            onChange={handleChange('title')}
            placeholder="Short summary of the job"
            required
          />
        </label>

        <label className="field">
          <span className="field__label">Description</span>
          <textarea
            value={form.description}
            onChange={handleChange('description')}
            rows={4}
            placeholder="What needs to happen?"
            required
          />
        </label>

        <label className="field">
          <span className="field__label">Context</span>
          <textarea
            value={form.context}
            onChange={handleChange('context')}
            rows={3}
            placeholder="Links, related tickets, constraints, etc."
          />
        </label>

        <label className="field field--inline">
          <span className="field__label">Priority</span>
          <select value={form.priority} onChange={handleChange('priority')}>
            <option value="low">Low</option>
            <option value="normal">Normal</option>
            <option value="high">High</option>
          </select>
        </label>
      </fieldset>

      <fieldset className="intake-form__section">
        <legend>Attachments ({totalAttachments})</legend>

        <FileUpload onAdd={handleFilesAdded} onRemove={handleFileRemoved} />

        <ScreenshotCapture
          onCapture={handleScreenshot}
          onRemove={handleScreenshotRemove}
          screenshots={screenshots}
        />

        <ScreenCapture
          onCapture={handleScreenCapture}
          onError={(m) => setError(m)}
        />

        {screenCaptures.length > 0 && (
          <ul className="capture-list">
            {screenCaptures.map((c) => (
              <li key={c.id} className="capture-list__item">
                <img src={c.previewUrl} alt={c.label} />
                <span className="capture-list__name">{c.label}</span>
                <button
                  type="button"
                  className="btn btn--ghost"
                  onClick={() => handleScreenCaptureRemove(c.id)}
                >
                  Remove
                </button>
              </li>
            ))}
          </ul>
        )}

        <VoiceRecorder
          onRecording={handleRecording}
          onRemove={handleRecordingRemove}
          recordings={recordings}
        />
      </fieldset>

      <div className="intake-form__actions">
        <button
          type="submit"
          className="btn btn--primary"
          disabled={submitting}
        >
          {submitting ? 'Submitting…' : 'Submit intake'}
        </button>
      </div>
    </form>
  );
}

export default IntakeForm;
