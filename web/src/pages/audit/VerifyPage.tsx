// Log verification: recompute the hash chain and report how many rows were checked
// and the first altered row, if any. Reading only; verification changes nothing.
import { useState } from "react";
import { api, type AuditVerification } from "../../api";
import { Button, ButtonLink, Callout, ErrorState, KeyValue, PageHeader, Panel } from "../../components";
import { useMutation } from "../../hooks/useApi";
import { formatDateTime, formatNumber } from "../../lib/format";
import "./audit.css";

export default function VerifyPage() {
  const [result, setResult] = useState<{ at: string; v: AuditVerification } | null>(null);
  const run = useMutation(async () => {
    const v = await api.verifyAuditLog();
    setResult({ at: new Date().toISOString(), v });
    return v;
  });

  return (
    <div className="stack">
      <PageHeader title="Log verification" subtitle="Recompute the chain of row hashes from the first row to the last. Each row's hash covers the row before it, so changing or removing any row breaks every hash after it." />
      <Panel
        title="Check the log"
        actions={
          <Button variant="primary" icon="shield" loading={run.pending} onClick={() => run.run().catch(() => undefined)}>
            {result ? "Run the check again" : "Run the check"}
          </Button>
        }
      >
        {run.error ? <ErrorState error={run.error} onRetry={() => run.run().catch(() => undefined)} /> : null}
        {!result && !run.error ? <p className="muted" style={{ margin: 0 }}>Nothing has been checked in this session. Run the check to see the result.</p> : null}
        {result ? (
          <div className="stack" role="status" aria-live="polite">
            <Callout tone={result.v.ok ? "pass" : "held"} title={result.v.ok ? "The log is intact" : "The log has been altered"}>
              {result.v.ok ? `All ${formatNumber(result.v.rows_checked)} rows match their hashes. Nothing was changed, removed or inserted.` : `A row no longer matches its hash. Rows from the first altered row onward cannot be trusted.`}
            </Callout>
            <KeyValue
              columns={3}
              items={[
                { label: "Result", value: result.v.ok ? "Intact" : "Altered" },
                { label: "Rows checked", value: formatNumber(result.v.rows_checked) },
                { label: "Checked at", value: formatDateTime(result.at) },
                { label: "First altered row", value: result.v.first_bad_row ? <code>{result.v.first_bad_row}</code> : "None found" },
              ]}
            />
            {result.v.first_bad_row ? (
              <div>
                <ButtonLink to={`/audit?row=${result.v.first_bad_row}`} variant="secondary" iconRight="arrow-right">
                  Open the altered row in the audit log
                </ButtonLink>
              </div>
            ) : null}
          </div>
        ) : null}
      </Panel>
    </div>
  );
}
