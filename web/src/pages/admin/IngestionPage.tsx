// Ingestion: pick a preset, adjust options, run a trial or a full ingest, watch the job.
// A full run is refused until the version is approved and passed the quality gate.
import { useEffect, useMemo, useState } from "react";
import { useParams } from "react-router-dom";
import { api } from "../../api";
import type { GuardResult, IngestJob, IngestProfile } from "../../api/types";
import { toProblem } from "../../api/errors";
import { isApiError } from "../../api";
import { Button, ButtonLink, Callout, Checkbox, ErrorState, Field, GuardList, KeyValue, PageHeader, Panel, Pill, Select, SkeletonLines, TextInput, type Tone } from "../../components";
import { useApi, useMutation } from "../../hooks/useApi";
import { formatDateTime } from "../../lib/format";
import { humanize } from "../../lib/labels";
import { QualityPill, ReviewPill } from "./shared";

type Full = Required<Omit<IngestProfile, "page_range">> & { page_range: string | null };

const IMAGE_OPTIONS = [
  { value: "ignore_all", label: "Ignore all images" },
  { value: "ignore_and_flag", label: "Ignore, and flag image-only pages" },
  { value: "ocr_image_only", label: "Read image-only pages with OCR" },
  { value: "describe_with_person_check", label: "AI description, checked by a person" },
];
const IMAGE_HINT: Record<string, string> = {
  ignore_all: "Fastest. Figures and scans are dropped without a trace.",
  ignore_and_flag: "Text is read; pages that are only an image are listed in the quality report so nothing is lost silently.",
  ocr_image_only: "Recognised text is not treated as authoritative: digits can be dropped, so flagged pages still need a person.",
  describe_with_person_check: "A model describes figures such as decision trees. A person must accept each description before it is searchable.",
};

function complete(p: IngestProfile): Full {
  return {
    preset: p.preset,
    parser: p.parser,
    page_range: p.page_range ?? null,
    include_annexes: p.include_annexes ?? true,
    images: p.images ?? "ignore_and_flag",
    ocr_languages: p.ocr_languages ?? [],
    tables: p.tables ?? "keep",
    remove_headers_footers: p.remove_headers_footers ?? true,
    remove_page_numbers: p.remove_page_numbers ?? true,
    remove_contents_pages: p.remove_contents_pages ?? true,
    split_by: p.split_by,
    words_per_chunk: p.words_per_chunk ?? 256,
    overlap_words: p.overlap_words ?? 30,
    min_chunk_words: p.min_chunk_words ?? 8,
  };
}

const JOB_TONE: Record<IngestJob["status"], { tone: Tone; label: string }> = {
  queued: { tone: "neutral", label: "Queued" },
  running: { tone: "accent", label: "Running" },
  completed: { tone: "pass", label: "Completed" },
  failed: { tone: "held", label: "Failed" },
  cancelled: { tone: "neutral", label: "Cancelled" },
};

export default function IngestionPage() {
  const { versionId = "" } = useParams();
  const version = useApi(() => api.getDocumentVersion(versionId), [versionId]);
  const doc = useApi(() => api.getDocument(version.data!.document_id), [version.data?.document_id], { enabled: Boolean(version.data) });
  const presets = useApi(() => api.listIngestPresets(), []);
  const [presetKey, setPresetKey] = useState<string>("");
  const [p, setP] = useState<Full | null>(null);
  const [jobId, setJobId] = useState<string | null>(null);
  const [refused, setRefused] = useState<unknown>(null);
  const start = useMutation((mode: "trial" | "full", profile: IngestProfile) => api.startIngestJob(versionId, { mode, profile }));
  const cancel = useMutation((id: string) => api.cancelIngestJob(id));
  const job = useApi(() => api.getIngestJob(jobId!), [jobId], { enabled: Boolean(jobId) });

  // Start from the preset that suits the document type once presets load.
  useEffect(() => {
    if (p || !presets.data || !doc.data) return;
    const t = doc.data.doc_type;
    const key = t === "law" || t === "executive_regulation" ? "law" : t === "service_card" ? "web_page" : t === "database" ? "data_table" : "guideline";
    const preset = presets.data.items.find((x) => x.key === key) ?? presets.data.items[0];
    setPresetKey(preset.key);
    setP(complete(preset.profile));
  }, [presets.data, doc.data, p]);

  const active = job.data && (job.data.status === "running" || job.data.status === "queued");
  useEffect(() => {
    if (!active) return;
    const t = setInterval(() => job.reload(), 1000);
    return () => clearInterval(t);
  }, [active, job]);

  const base = presets.data?.items.find((x) => x.key === presetKey);
  const set = <K extends keyof Full>(k: K, v: Full[K]) => setP((cur) => (cur ? { ...cur, [k]: v } : cur));
  const dirty = useMemo(() => (base && p ? JSON.stringify(complete(base.profile)) !== JSON.stringify(p) : false), [base, p]);

  if (version.error) return <ErrorState error={version.error} onRetry={version.reload} />;
  if (!version.data || !p || !presets.data) return <SkeletonLines lines={8} />;
  const v = version.data;
  const d = doc.data;

  const approved = v.review_status === "approved" || v.review_status === "approved_with_notes";
  const qualityOk = v.quality_status === "pass" || v.quality_status === "attention";
  const guards: GuardResult[] = [
    { guard: "full_needs_approved_version", check_id: null, passed: approved, detail: approved ? "A Process Owner approved this version." : "A Process Owner has not approved this version yet. Only a trial can run." },
    { guard: "quality_gate_passed", check_id: null, passed: qualityOk, detail: qualityOk ? "The last trial passed the quality gate." : v.quality_status === "fail" ? "The last trial failed the quality gate. Change the options and run a trial again." : "No quality result yet. Run a trial first." },
  ];
  const overlapBad = p.overlap_words >= p.words_per_chunk;
  const rangeBad = p.page_range ? !/^\d+(-\d+)?(,\s*\d+(-\d+)?)*$/.test(p.page_range) : false;
  const invalid = overlapBad || rangeBad || p.words_per_chunk < 20 || p.min_chunk_words < 1;
  const needsOcr = p.images === "ocr_image_only" || p.images === "describe_with_person_check";

  const run = async (mode: "trial" | "full") => {
    setRefused(null);
    try {
      const profile: IngestProfile = { ...p, preset: dirty ? "custom" : (p.preset as IngestProfile["preset"]), page_range: p.page_range || null, ocr_languages: needsOcr ? p.ocr_languages : [] };
      const j = await start.run(mode, profile);
      setJobId(j.id);
    } catch (e) {
      setRefused(e);
    }
  };

  const j = job.data;
  const refusedGuards = isApiError(refused) ? refused.failed : [];

  return (
    <div className="stack">
      <PageHeader
        breadcrumbs={[{ label: "Document library", to: "/admin/library" }, { label: v.document_id, to: `/admin/library/${v.document_id}` }, { label: "Ingestion" }]}
        eyebrow={<code>{v.id}</code>}
        title="Ingestion"
        subtitle={d ? `${d.title}${v.version_label ? ` · ${v.version_label}` : ""}` : v.filename}
        status={
          <>
            <ReviewPill status={v.review_status} />
            <QualityPill status={v.quality_status} />
          </>
        }
        actions={<ButtonLink to={`/admin/library/versions/${v.id}/quality`} icon="list-checks">Quality report</ButtonLink>}
      />

      <Callout tone="neutral" icon="info" title="The options used are saved with this version">
        A run can be repeated and audited. A new version never deletes older chunks, so a past decision can still show the text that applied.
      </Callout>

      <Panel title="1. Choose a preset">
        <div className="adm-presets" role="group" aria-label="Presets">
          {presets.data.items.map((x) => (
            <button
              key={x.key}
              type="button"
              className="adm-preset"
              aria-pressed={presetKey === x.key && !dirty}
              onClick={() => {
                setPresetKey(x.key);
                setP(complete(x.profile));
              }}
            >
              <div className="adm-preset-title">{x.label}</div>
              <div className="xsmall muted">{x.good_for}</div>
            </button>
          ))}
        </div>
        {dirty ? <p className="small" style={{ marginTop: 10 }}><Pill tone="look" size="sm" icon="edit">Custom options</Pill> <span className="muted">Changed from the {base?.label.toLowerCase()} preset.</span></p> : null}
      </Panel>

      <Panel title="2. Adjust options">
        <div className="adm-opts">
          <Field label="Parser" hint="Best layout is slower but keeps columns and tables intact.">
            {(id, dd) => (
              <Select id={id} aria-describedby={dd} value={p.parser} onChange={(e) => set("parser", e.target.value as Full["parser"])} options={[{ value: "best_layout", label: "Best layout" }, { value: "fast_text", label: "Fast text" }, { value: "web_page", label: "Web page" }, { value: "table", label: "Table" }]} />
            )}
          </Field>
          <Field label="Pages" hint="Blank for all pages, or a range such as 3-40, 45." error={rangeBad ? "Use numbers, ranges and commas." : undefined}>
            {(id, dd) => <TextInput id={id} aria-describedby={dd} value={p.page_range ?? ""} onChange={(e) => set("page_range", e.target.value || null)} placeholder="All pages" />}
          </Field>
          <div className="span-2">
            <Field label="Images" hint={IMAGE_HINT[p.images]}>
              {(id, dd) => <Select id={id} aria-describedby={dd} value={p.images} onChange={(e) => set("images", e.target.value as Full["images"])} options={IMAGE_OPTIONS} />}
            </Field>
          </div>
          {needsOcr ? (
            <div className="span-2">
              <fieldset style={{ border: 0, padding: 0, margin: 0 }}>
                <legend className="field-label">Recognition languages</legend>
                <div className="row" style={{ gap: 16 }}>
                  {[["en", "English"], ["ar", "Arabic"]].map(([code, name]) => (
                    <Checkbox key={code} label={name} checked={p.ocr_languages.includes(code)} onChange={(e) => set("ocr_languages", e.target.checked ? [...p.ocr_languages, code] : p.ocr_languages.filter((x) => x !== code))} />
                  ))}
                </div>
              </fieldset>
            </div>
          ) : null}
          <Field label="Tables">
            {(id) => <Select id={id} value={p.tables} onChange={(e) => set("tables", e.target.value as Full["tables"])} options={[{ value: "keep", label: "Keep as tables" }, { value: "flatten", label: "Flatten to text" }, { value: "ignore", label: "Ignore tables" }]} />}
          </Field>
          <fieldset style={{ border: 0, padding: 0, margin: 0 }}>
            <legend className="field-label">Cleaning</legend>
            <div className="stack-sm">
              <Checkbox label="Remove headers and footers" checked={p.remove_headers_footers} onChange={(e) => set("remove_headers_footers", e.target.checked)} />
              <Checkbox label="Remove page numbers" checked={p.remove_page_numbers} onChange={(e) => set("remove_page_numbers", e.target.checked)} />
              <Checkbox label="Remove contents pages" checked={p.remove_contents_pages} onChange={(e) => set("remove_contents_pages", e.target.checked)} />
              <Checkbox label="Include annexes" checked={p.include_annexes} onChange={(e) => set("include_annexes", e.target.checked)} />
            </div>
          </fieldset>
          <Field label="Split by" hint="Article keeps a citation to one provision; heading suits guidelines.">
            {(id, dd) => <Select id={id} aria-describedby={dd} value={p.split_by} onChange={(e) => set("split_by", e.target.value as Full["split_by"])} options={[{ value: "article", label: "Article and clause" }, { value: "heading", label: "Heading" }, { value: "fixed", label: "Fixed size" }]} />}
          </Field>
          <div />
          <Field label="Words per chunk">
            {(id) => <TextInput id={id} type="number" min={20} value={p.words_per_chunk} onChange={(e) => set("words_per_chunk", Number(e.target.value))} />}
          </Field>
          <Field label="Overlap (words)" error={overlapBad ? "Overlap must be smaller than the chunk size." : undefined}>
            {(id) => <TextInput id={id} type="number" min={0} value={p.overlap_words} onChange={(e) => set("overlap_words", Number(e.target.value))} />}
          </Field>
          <Field label="Smallest chunk (words)">
            {(id) => <TextInput id={id} type="number" min={1} value={p.min_chunk_words} onChange={(e) => set("min_chunk_words", Number(e.target.value))} />}
          </Field>
        </div>
      </Panel>

      <Panel title="3. Run">
        <div className="grid-2">
          <div className="stack-sm">
            <h3>Trial run</h3>
            <p className="small muted">Reads and splits the file and produces the quality report. Nothing is embedded or searchable. Always allowed.</p>
            <div><Button variant="primary" icon="play" loading={start.pending} disabled={invalid || Boolean(active)} onClick={() => void run("trial")}>Run trial</Button></div>
          </div>
          <div className="stack-sm">
            <h3>Full run</h3>
            <p className="small muted">Embeds and indexes the chunks. Refused until the version is approved and the last trial passed the quality gate.</p>
            <GuardList guards={guards} title={guards.every((g) => g.passed) ? "A full run is allowed" : "A full run is refused for now"} />
            <div><Button variant="secondary" icon="database" loading={start.pending} disabled={invalid || Boolean(active)} onClick={() => void run("full")}>Run full ingest</Button></div>
          </div>
        </div>
        {refused ? (
          <div style={{ marginTop: 12 }}>
            {refusedGuards.length ? <GuardList guards={refusedGuards} title={toProblem(refused).title} onlyFailed /> : <Callout tone="held" title={toProblem(refused).title}>{toProblem(refused).detail}</Callout>}
          </div>
        ) : null}
      </Panel>

      {jobId ? (
        <Panel
          title="Job"
          actions={
            active && j ? (
              <Button size="sm" variant="danger" icon="x" loading={cancel.pending} onClick={() => void cancel.run(j.id).then(job.reload)}>Cancel job</Button>
            ) : undefined
          }
        >
          {job.error ? (
            <ErrorState error={job.error} onRetry={job.reload} />
          ) : !j ? (
            <SkeletonLines lines={3} />
          ) : (
            <div className="stack">
              <div className="row">
                <Pill tone={JOB_TONE[j.status].tone} icon={j.status === "completed" ? "check" : j.status === "failed" ? "x" : j.status === "cancelled" ? "ban" : "loader"} spin={j.status === "running"}>{JOB_TONE[j.status].label}</Pill>
                <Pill tone="neutral" size="sm">{j.mode === "trial" ? "Trial run" : "Full run"}</Pill>
                <code className="small">{j.id}</code>
              </div>
              <div>
                <div className="row-between small"><span>Progress</span><span className="num">{j.progress_pct}%</span></div>
                <div className="adm-bar" role="progressbar" aria-valuemin={0} aria-valuemax={100} aria-valuenow={j.progress_pct} aria-label="Ingestion progress"><span style={{ width: `${j.progress_pct}%` }} /></div>
              </div>
              <KeyValue columns={3} items={[
                { label: "Pages read", value: j.pages_done },
                { label: "Chunks so far", value: j.chunks },
                { label: "Started", value: j.started_at ? formatDateTime(j.started_at) : "Waiting" },
                { label: "Split by", value: humanize(j.profile.split_by) },
                { label: "Images", value: IMAGE_OPTIONS.find((o) => o.value === j.profile.images)?.label },
                { label: "Finished", value: j.finished_at ? formatDateTime(j.finished_at) : null },
              ]} />
              {j.warnings.length ? (
                <Callout tone="look" title={`${j.warnings.length} warning${j.warnings.length === 1 ? "" : "s"}`}>
                  <ul style={{ margin: "4px 0 0", paddingLeft: 18 }}>{j.warnings.map((w) => <li key={w}>{w}</li>)}</ul>
                </Callout>
              ) : null}
              {j.error ? <Callout tone="held" title="The job failed">{j.error}</Callout> : null}
              {j.status === "completed" || j.status === "failed" ? (
                <div className="row"><ButtonLink to={`/admin/library/versions/${v.id}/quality`} variant="primary" iconRight="arrow-right">Open quality report</ButtonLink></div>
              ) : null}
            </div>
          )}
        </Panel>
      ) : null}
    </div>
  );
}
