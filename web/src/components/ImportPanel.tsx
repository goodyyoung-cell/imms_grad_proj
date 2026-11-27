import { useRef, useState } from "react";
import { api } from "../api/client";
import { invalidate, useAction } from "../api/hooks";
import { useToast } from "../toast";
import type { ImportResult } from "../types";
import { Badge, Card } from "./ui";

/** CSV·엑셀·구글 시트로 리드를 올린다. 먼저 미리보기를 보고, 확인한 뒤 저장한다. */
export function ImportPanel() {
  const toast = useToast();
  const fileRef = useRef<HTMLInputElement>(null);
  const [file, setFile] = useState<File | null>(null);
  const [sheet, setSheet] = useState("");
  const [preview, setPreview] = useState<ImportResult | null>(null);

  const build = (dry: boolean) => {
    const f = new FormData();
    if (file) f.append("file", file); else f.append("google_sheet", sheet.trim());
    f.append("dry_run", dry ? "true" : "false");
    return f;
  };
  const ready = !!file || sheet.trim().length > 0;

  const check = useAction(async () => { setPreview(await api.importLeads(build(true))); });
  const save = useAction(async () => {
    const r = await api.importLeads(build(false));
    toast(`리드 ${r.created}건을 추가하고 ${r.merged}건을 기존 리드에 합쳤습니다.`);
    setPreview(null); setFile(null); setSheet(""); if (fileRef.current) fileRef.current.value = "";
    invalidate();
  });

  return (
    <Card title="리드 올리기">
      <p className="muted">컬럼 이름은 달라도 됩니다. 이름·이메일·회사명 같은 열을 자동으로 찾아 맞춥니다. 같은 회사·사람은 합칩니다.</p>
      <div className="form-grid">
        <label>CSV 또는 엑셀 파일
          <input ref={fileRef} type="file" accept=".csv,.xlsx,.xls,.tsv" onChange={(e) => { setFile(e.target.files?.[0] ?? null); setPreview(null); }} />
        </label>
        <label>또는 구글 시트 주소
          <input value={sheet} disabled={!!file} onChange={(e) => { setSheet(e.target.value); setPreview(null); }} placeholder="https://docs.google.com/spreadsheets/d/..." />
        </label>
      </div>
      <div className="decision-actions">
        <button className="btn" disabled={!ready || check.busy} onClick={() => check.run()}>{check.busy ? "확인 중" : "미리보기"}</button>
      </div>
      {check.error && <p className="notice err" role="alert">{check.error}</p>}
      {preview && (
        <div className="import-preview">
          <p>
            <Badge tone="green">새 리드 {preview.created}</Badge> <Badge tone="blue">기존에 합침 {preview.merged}</Badge>{" "}
            <Badge>건너뜀 {preview.skipped}</Badge> {preview.errors > 0 && <Badge tone="red">오류 {preview.errors}</Badge>}
          </p>
          {preview.columns && <p className="muted small">인식한 열: {Object.entries(preview.columns).map(([k, v]) => `${k}→${v}`).join(", ")}{preview.ignored_columns?.length ? ` · 무시한 열: ${preview.ignored_columns.join(", ")}` : ""}</p>}
          {preview.details?.errors?.length > 0 && <ul className="err-list">{preview.details.errors.map((e, i) => <li key={i}>{e.row ? `${e.row}행: ` : ""}{e.error}</li>)}</ul>}
          <p className="muted small">미리보기에서는 아무것도 저장되지 않았습니다.</p>
          <div className="decision-actions">
            <button className="btn primary" disabled={save.busy || preview.created + preview.merged === 0} onClick={() => save.run()}>{save.busy ? "저장 중" : "이대로 저장"}</button>
          </div>
          {save.error && <p className="notice err" role="alert">{save.error}</p>}
        </div>
      )}
    </Card>
  );
}
