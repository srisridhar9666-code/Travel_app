import { useMutation } from '@tanstack/react-query';
import { AlertTriangle, CheckCircle2, Download, FileSpreadsheet, Upload, XCircle } from 'lucide-react';
import { useState } from 'react';
import toast from 'react-hot-toast';

import { Modal } from '@/components/Modal';
import { Badge, Button, Skeleton } from '@/components/ui';
import {
  commitImport,
  errorMessage,
  fetchImportTemplate,
  previewImport,
} from '@/lib/api';
import { cn } from '@/lib/utils';
import type { ImportPreview, ImportResult } from '@/types';

interface BulkImportModalProps {
  open: boolean;
  onClose: () => void;
  onImported: () => void;
}

/**
 * Two-step import: preview, then commit.
 *
 * Creating a hundred accounts, each with an invitation, is not something an
 * admin should first understand the shape of after it has happened. The preview
 * writes nothing; the commit re-parses the same file server-side.
 */
export function BulkImportModal({ open, onClose, onImported }: BulkImportModalProps) {
  const [file, setFile] = useState<File | null>(null);
  const [preview, setPreview] = useState<ImportPreview | null>(null);
  const [result, setResult] = useState<ImportResult | null>(null);
  const [error, setError] = useState<string | null>(null);

  const reset = () => {
    setFile(null);
    setPreview(null);
    setResult(null);
    setError(null);
  };

  const close = () => {
    reset();
    onClose();
  };

  const runPreview = useMutation({
    mutationFn: (chosen: File) => previewImport(chosen),
    onSuccess: setPreview,
    onError: (err) => setError(errorMessage(err, 'Could not read that file.')),
  });

  const runCommit = useMutation({
    mutationFn: () => commitImport(file!),
    onSuccess: (data) => {
      setResult(data);
      onImported();
      toast.success(`${data.created} account(s) created`);
    },
    onError: (err) => setError(errorMessage(err, 'Import failed.')),
  });

  const downloadTemplate = useMutation({
    mutationFn: fetchImportTemplate,
    onSuccess: (blob) => {
      const url = URL.createObjectURL(blob);
      const anchor = document.createElement('a');
      anchor.href = url;
      anchor.download = 'team-import-template.csv';
      anchor.click();
      URL.revokeObjectURL(url);
    },
    onError: (err) => toast.error(errorMessage(err)),
  });

  const choose = (chosen: File | null) => {
    setPreview(null);
    setResult(null);
    setError(null);
    setFile(chosen);
    if (chosen) runPreview.mutate(chosen);
  };

  return (
    <Modal
      open={open}
      onClose={close}
      title="Import the team"
      description="Preview first — nothing is written until you confirm."
      className="sm:max-w-2xl"
      footer={
        result ? (
          <Button onClick={close}>Done</Button>
        ) : (
          <>
            <Button variant="secondary" onClick={close}>
              Cancel
            </Button>
            <Button
              disabled={!preview || preview.importable === 0}
              loading={runCommit.isPending}
              onClick={() => runCommit.mutate()}
            >
              Import {preview?.importable ?? 0} account
              {preview?.importable === 1 ? '' : 's'}
            </Button>
          </>
        )
      }
    >
      {result ? (
        <div className="space-y-4">
          <div className="flex items-start gap-2.5 rounded-md bg-success-soft px-3 py-2.5 text-sm text-success">
            <CheckCircle2 size={16} className="mt-px shrink-0" />
            <span>
              {result.created} account{result.created === 1 ? '' : 's'} created
              {result.skipped > 0 && `, ${result.skipped} row(s) skipped`}.
            </span>
          </div>

          <div>
            <p className="mb-2 text-xs text-text-muted">
              {result.emailing
                ? 'Each person is being emailed their link. The links are also here if anyone needs one resent.'
                : `Emails were not sent${result.email_detail ? ` (${result.email_detail})` : ''}. Send each person their link.`}
            </p>
            <ul className="max-h-64 space-y-1.5 overflow-y-auto rounded-md bg-surface-sunken p-3">
              {Object.entries(result.invite_urls).map(([email, url]) => (
                <li key={email} className="text-xs">
                  <div className="font-medium">{email}</div>
                  <code className="break-all text-2xs text-text-muted">{url}</code>
                </li>
              ))}
            </ul>
            <Button
              variant="secondary"
              size="sm"
              className="mt-2"
              onClick={() => {
                const text = Object.entries(result.invite_urls)
                  .map(([email, url]) => `${email}\t${url}`)
                  .join('\n');
                navigator.clipboard
                  .writeText(text)
                  .then(() => toast.success('All links copied'))
                  .catch(() => toast.error('Could not copy'));
              }}
            >
              Copy all links
            </Button>
          </div>
        </div>
      ) : (
        <div className="space-y-4">
          <div className="flex flex-wrap items-center justify-between gap-3 rounded-md border border-dashed border-border-strong px-4 py-4">
            <div className="flex items-center gap-2.5">
              <FileSpreadsheet size={18} className="text-text-subtle" />
              <div>
                <div className="text-sm font-medium">{file ? file.name : 'Choose a CSV'}</div>
                <div className="text-xs text-text-subtle">
                  Columns: full_name and email are required
                </div>
              </div>
            </div>
            <div className="flex gap-2">
              <Button
                variant="ghost"
                size="sm"
                loading={downloadTemplate.isPending}
                onClick={() => downloadTemplate.mutate()}
              >
                <Download size={14} />
                Template
              </Button>
              <label className="inline-flex h-8 cursor-pointer items-center gap-2 rounded-md border border-border bg-surface px-3 text-xs font-medium hover:bg-surface-sunken">
                <Upload size={14} />
                {file ? 'Change' : 'Select file'}
                <input
                  type="file"
                  accept=".csv,text/csv"
                  className="sr-only"
                  onChange={(e) => choose(e.target.files?.[0] ?? null)}
                />
              </label>
            </div>
          </div>

          {error && (
            <p role="alert" className="rounded-md bg-danger-soft px-3 py-2 text-xs text-danger">
              {error}
            </p>
          )}

          {runPreview.isPending && <Skeleton className="h-40 w-full" />}

          {preview && (
            <>
              <div className="flex flex-wrap gap-2">
                <Badge tone="success">{preview.importable} will import</Badge>
                {preview.skipped > 0 && <Badge tone="danger">{preview.skipped} skipped</Badge>}
                <Badge tone="neutral">{preview.total} rows read</Badge>
              </div>

              {preview.file_errors.length > 0 && (
                <ul className="space-y-1 rounded-md bg-warning-soft px-3 py-2.5">
                  {preview.file_errors.map((message) => (
                    <li key={message} className="flex items-start gap-2 text-xs text-warning">
                      <AlertTriangle size={13} className="mt-px shrink-0" />
                      {message}
                    </li>
                  ))}
                </ul>
              )}

              {preview.rows.length > 0 && (
                <div className="max-h-72 overflow-auto rounded-md border border-border">
                  <table className="w-full text-xs">
                    <thead className="sticky top-0 bg-surface-sunken">
                      <tr className="text-left text-2xs uppercase tracking-widest text-text-subtle">
                        <th className="px-3 py-2 font-semibold">Line</th>
                        <th className="px-3 py-2 font-semibold">Name</th>
                        <th className="px-3 py-2 font-semibold">Email</th>
                        <th className="px-3 py-2 font-semibold">Status</th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-border">
                      {preview.rows.map((row) => (
                        <tr
                          key={row.line}
                          className={cn(row.errors.length > 0 && 'bg-danger-soft/40')}
                        >
                          <td className="px-3 py-2 font-mono text-text-subtle">{row.line}</td>
                          <td className="px-3 py-2">{row.full_name || '—'}</td>
                          <td className="px-3 py-2 text-text-muted">{row.email || '—'}</td>
                          <td className="px-3 py-2">
                            {row.errors.length > 0 ? (
                              <span className="flex items-start gap-1.5 text-danger">
                                <XCircle size={12} className="mt-0.5 shrink-0" />
                                <span>{row.errors.join('; ')}</span>
                              </span>
                            ) : row.warnings.length > 0 ? (
                              <span className="flex items-start gap-1.5 text-warning">
                                <AlertTriangle size={12} className="mt-0.5 shrink-0" />
                                <span>{row.warnings.join('; ')}</span>
                              </span>
                            ) : (
                              <span className="flex items-center gap-1.5 text-success">
                                <CheckCircle2 size={12} />
                                Ready
                              </span>
                            )}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </>
          )}
        </div>
      )}
    </Modal>
  );
}
