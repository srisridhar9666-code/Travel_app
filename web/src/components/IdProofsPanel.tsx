import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  Download,
  Eye,
  EyeOff,
  FileWarning,
  Paperclip,
  Plus,
  ShieldAlert,
  Trash2,
} from 'lucide-react';
import { useEffect, useRef, useState, type FormEvent } from 'react';
import toast from 'react-hot-toast';

import { Badge, Button, EmptyState, Field, Input, Select, Skeleton } from '@/components/ui';
import {
  addIdProof,
  deleteIdProof,
  errorMessage,
  fetchIdProofFile,
  fetchIdProofs,
  revealIdProof,
} from '@/lib/api';
import { ID_PROOF_LABELS, type IdProof, type IdProofType, type UserRow } from '@/types';

/** How long a revealed number stays on screen before hiding itself again. */
const REVEAL_SECONDS = 20;

function fileSize(bytes: number | null) {
  if (!bytes) return '';
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

function ProofRow({ proof, onDeleted }: { proof: IdProof; onDeleted: () => void }) {
  const [revealed, setRevealed] = useState<string | null>(null);
  const timerRef = useRef<number | null>(null);

  // Clear the timer if the panel closes while a number is on screen.
  useEffect(
    () => () => {
      if (timerRef.current) window.clearTimeout(timerRef.current);
    },
    [],
  );

  const reveal = useMutation({
    mutationFn: () => revealIdProof(proof.id),
    onSuccess: (data) => {
      setRevealed(data.number);
      toast('This view has been recorded in the activity log.', { icon: '📋' });
      if (timerRef.current) window.clearTimeout(timerRef.current);
      timerRef.current = window.setTimeout(() => setRevealed(null), REVEAL_SECONDS * 1000);
    },
    onError: (err) => toast.error(errorMessage(err)),
  });

  const download = useMutation({
    mutationFn: () => fetchIdProofFile(proof.id),
    onSuccess: (blob) => {
      // Opened from a blob rather than linked directly, so the request carries
      // the bearer token and lands in the audit log like any other read.
      const url = URL.createObjectURL(blob);
      window.open(url, '_blank', 'noopener,noreferrer');
      window.setTimeout(() => URL.revokeObjectURL(url), 60_000);
      toast('Download recorded in the activity log.', { icon: '📋' });
    },
    onError: (err) => toast.error(errorMessage(err)),
  });

  const remove = useMutation({
    mutationFn: () => deleteIdProof(proof.id),
    onSuccess: () => {
      toast.success('Document deleted');
      onDeleted();
    },
    onError: (err) => toast.error(errorMessage(err)),
  });

  if (proof.is_purged) {
    return (
      <li className="flex items-center gap-3 border-b border-border px-1 py-3 last:border-b-0">
        <FileWarning size={16} className="shrink-0 text-text-subtle" />
        <div className="min-w-0 flex-1">
          <div className="text-sm font-medium text-text-muted">
            {ID_PROOF_LABELS[proof.proof_type]}
          </div>
          <div className="text-xs text-text-subtle">
            Purged under the retention policy on{' '}
            {new Date(`${proof.purged_at}Z`).toLocaleDateString()}
          </div>
        </div>
        <Badge tone="neutral">Purged</Badge>
      </li>
    );
  }

  return (
    <li className="flex flex-wrap items-center gap-3 border-b border-border px-1 py-3 last:border-b-0">
      <div className="min-w-0 flex-1">
        <div className="flex flex-wrap items-center gap-2">
          <span className="text-sm font-medium">{ID_PROOF_LABELS[proof.proof_type]}</span>
          {proof.label && <span className="text-xs text-text-subtle">{proof.label}</span>}
          {proof.is_expired && <Badge tone="danger">Expired</Badge>}
        </div>

        <div className="mt-0.5 font-mono text-sm tracking-wide">
          {revealed ? (
            <span className="rounded bg-warning-soft px-1.5 py-0.5 text-warning">{revealed}</span>
          ) : (
            <span className="text-text-muted">{proof.masked_number ?? '—'}</span>
          )}
        </div>

        {proof.has_file && (
          <div className="mt-1 flex items-center gap-1.5 text-2xs text-text-subtle">
            <Paperclip size={11} />
            {proof.file_name} {fileSize(proof.file_size)}
          </div>
        )}
      </div>

      <div className="flex gap-1">
        <Button
          variant="ghost"
          size="sm"
          title={revealed ? 'Hide' : 'Reveal the full number (recorded)'}
          loading={reveal.isPending}
          onClick={() => (revealed ? setRevealed(null) : reveal.mutate())}
        >
          {revealed ? <EyeOff size={14} /> : <Eye size={14} />}
        </Button>
        {proof.has_file && (
          <Button
            variant="ghost"
            size="sm"
            title="Open the scan (recorded)"
            loading={download.isPending}
            onClick={() => download.mutate()}
          >
            <Download size={14} />
          </Button>
        )}
        <Button
          variant="ghost"
          size="sm"
          title="Delete"
          className="text-danger hover:text-danger"
          loading={remove.isPending}
          onClick={() => {
            if (window.confirm(`Delete this ${ID_PROOF_LABELS[proof.proof_type]} permanently?`))
              remove.mutate();
          }}
        >
          <Trash2 size={14} />
        </Button>
      </div>
    </li>
  );
}

export function IdProofsPanel({ user }: { user: UserRow }) {
  const queryClient = useQueryClient();
  const [adding, setAdding] = useState(false);
  const [proofType, setProofType] = useState<IdProofType>('AADHAAR');
  const [number, setNumber] = useState('');
  const [expiresOn, setExpiresOn] = useState('');
  const [file, setFile] = useState<File | null>(null);
  const [error, setError] = useState<string | null>(null);

  const proofs = useQuery({
    queryKey: ['id-proofs', user.id],
    queryFn: () => fetchIdProofs(user.id),
  });

  const refresh = () => queryClient.invalidateQueries({ queryKey: ['id-proofs', user.id] });

  const add = useMutation({
    mutationFn: () =>
      addIdProof(
        user.id,
        { proof_type: proofType, number: number.trim(), expires_on: expiresOn || undefined },
        file,
      ),
    onSuccess: () => {
      toast.success('Document added');
      setAdding(false);
      setNumber('');
      setExpiresOn('');
      setFile(null);
      setError(null);
      refresh();
    },
    onError: (err) => setError(errorMessage(err, 'Could not save this document.')),
  });

  const submit = (event: FormEvent) => {
    event.preventDefault();
    setError(null);
    add.mutate();
  };

  const rows = proofs.data ?? [];

  return (
    <div>
      <div className="mb-3 flex items-start gap-2 rounded-md bg-surface-sunken px-3 py-2.5 text-xs leading-relaxed text-text-muted">
        <ShieldAlert size={14} className="mt-px shrink-0 text-text-subtle" />
        <span>
          Numbers are encrypted at rest. Revealing one or opening a scan is recorded against your
          name. Documents are purged 90 days after the employee&rsquo;s exit date.
        </span>
      </div>

      {proofs.isPending ? (
        <div className="space-y-2">
          {Array.from({ length: 2 }).map((_, i) => (
            <Skeleton key={i} className="h-14 w-full" />
          ))}
        </div>
      ) : rows.length === 0 && !adding ? (
        <EmptyState
          title="No documents on file"
          description="Add the identity documents this person travels on."
          action={
            <Button size="sm" onClick={() => setAdding(true)}>
              <Plus size={14} />
              Add document
            </Button>
          }
        />
      ) : (
        <ul>
          {rows.map((proof) => (
            <ProofRow key={proof.id} proof={proof} onDeleted={refresh} />
          ))}
        </ul>
      )}

      {adding ? (
        <form onSubmit={submit} className="mt-4 space-y-3 rounded-md border border-border p-3">
          <div className="grid gap-3 sm:grid-cols-2">
            <Field label="Document type" htmlFor="proof_type" required>
              <Select
                id="proof_type"
                value={proofType}
                onChange={(e) => setProofType(e.target.value as IdProofType)}
              >
                {(Object.keys(ID_PROOF_LABELS) as IdProofType[]).map((t) => (
                  <option key={t} value={t}>
                    {ID_PROOF_LABELS[t]}
                  </option>
                ))}
              </Select>
            </Field>

            <Field label="Number" htmlFor="number" required>
              <Input
                id="number"
                required
                value={number}
                onChange={(e) => setNumber(e.target.value)}
                placeholder="4321 8765 2109"
                className="font-mono"
                autoComplete="off"
              />
            </Field>

            <Field label="Expires on" htmlFor="expires_on">
              <Input
                id="expires_on"
                type="date"
                value={expiresOn}
                onChange={(e) => setExpiresOn(e.target.value)}
              />
            </Field>

            <Field label="Scan" htmlFor="scan" hint="JPEG, PNG, WebP, HEIC or PDF. Max 10 MB.">
              <Input
                id="scan"
                type="file"
                accept="image/jpeg,image/png,image/webp,image/heic,application/pdf"
                onChange={(e) => setFile(e.target.files?.[0] ?? null)}
                className="h-auto py-1.5 text-xs file:mr-2 file:rounded file:border-0 file:bg-surface-sunken file:px-2 file:py-1 file:text-xs"
              />
            </Field>
          </div>

          {error && (
            <p role="alert" className="rounded-md bg-danger-soft px-3 py-2 text-xs text-danger">
              {error}
            </p>
          )}

          <div className="flex justify-end gap-2">
            <Button
              type="button"
              variant="secondary"
              size="sm"
              onClick={() => {
                setAdding(false);
                setError(null);
              }}
            >
              Cancel
            </Button>
            <Button type="submit" size="sm" loading={add.isPending}>
              Save document
            </Button>
          </div>
        </form>
      ) : (
        rows.length > 0 && (
          <Button variant="secondary" size="sm" className="mt-4" onClick={() => setAdding(true)}>
            <Plus size={14} />
            Add document
          </Button>
        )
      )}
    </div>
  );
}
