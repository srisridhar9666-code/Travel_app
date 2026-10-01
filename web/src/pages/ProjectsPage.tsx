import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Archive, ArchiveRestore, FolderKanban, Pencil, Plus, Search } from 'lucide-react';
import { useState, type FormEvent } from 'react';
import toast from 'react-hot-toast';

import { Modal } from '@/components/Modal';
import {
  Badge,
  Button,
  Card,
  CardHeader,
  EmptyState,
  Field,
  Input,
  Select,
  Skeleton,
} from '@/components/ui';
import {
  archiveProject,
  createProject,
  errorMessage,
  fetchProjects,
  restoreProject,
  updateProject,
  type ProjectPayload,
} from '@/lib/api';
import {
  PROJECT_STATUS_LABELS,
  type Project,
  type ProjectStatus,
} from '@/types';

const BLANK: ProjectPayload = {
  name: '',
  code: '',
  client_name: '',
  location: '',
  status: 'ACTIVE',
  start_date: '',
  end_date: '',
  description: '',
};

const STATUS_TONE: Record<ProjectStatus, 'success' | 'warning' | 'info' | 'neutral'> = {
  ACTIVE: 'success',
  PAUSED: 'warning',
  COMPLETED: 'info',
  ARCHIVED: 'neutral',
};

function formatRange(project: Project) {
  const fmt = (iso: string) =>
    new Date(`${iso}T00:00:00`).toLocaleDateString(undefined, {
      day: '2-digit',
      month: 'short',
      year: 'numeric',
    });
  if (project.start_date && project.end_date)
    return `${fmt(project.start_date)} – ${fmt(project.end_date)}`;
  if (project.start_date) return `from ${fmt(project.start_date)}`;
  if (project.end_date) return `until ${fmt(project.end_date)}`;
  return '—';
}

export default function ProjectsPage() {
  const queryClient = useQueryClient();

  const [search, setSearch] = useState('');
  const [statusFilter, setStatusFilter] = useState('');
  const [editing, setEditing] = useState<Project | null>(null);
  const [formOpen, setFormOpen] = useState(false);
  const [form, setForm] = useState<ProjectPayload>(BLANK);
  const [formError, setFormError] = useState<string | null>(null);

  const projects = useQuery({
    queryKey: ['projects', search, statusFilter],
    queryFn: () =>
      fetchProjects({
        search: search.trim() || undefined,
        status: statusFilter || undefined,
        page_size: 100,
      }),
  });

  const refresh = () => queryClient.invalidateQueries({ queryKey: ['projects'] });

  const save = useMutation({
    mutationFn: () => {
      // Blank date inputs come through as "" - send null so the API clears them
      // rather than failing to parse an empty string as a date.
      const payload: ProjectPayload = {
        ...form,
        start_date: form.start_date || null,
        end_date: form.end_date || null,
        client_name: form.client_name || null,
        location: form.location || null,
        description: form.description || null,
      };
      return editing ? updateProject(editing.id, payload) : createProject(payload);
    },
    onSuccess: (project) => {
      toast.success(editing ? `${project.name} updated` : `${project.name} created`);
      setFormOpen(false);
      setEditing(null);
      setForm(BLANK);
      setFormError(null);
      refresh();
    },
    onError: (err) => setFormError(errorMessage(err, 'Could not save this campaign.')),
  });

  const toggleArchive = useMutation({
    mutationFn: (project: Project) =>
      project.status === 'ARCHIVED' ? restoreProject(project.id) : archiveProject(project.id),
    onSuccess: (updated) => {
      toast.success(
        updated.status === 'ARCHIVED'
          ? `${updated.name} archived — hidden from new requests`
          : `${updated.name} restored`,
      );
      refresh();
    },
    onError: (err) => toast.error(errorMessage(err)),
  });

  const openCreate = () => {
    setEditing(null);
    setForm(BLANK);
    setFormError(null);
    setFormOpen(true);
  };

  const openEdit = (project: Project) => {
    setEditing(project);
    setForm({
      name: project.name,
      code: project.code,
      client_name: project.client_name ?? '',
      location: project.location ?? '',
      status: project.status,
      start_date: project.start_date ?? '',
      end_date: project.end_date ?? '',
      description: project.description ?? '',
    });
    setFormError(null);
    setFormOpen(true);
  };

  const submit = (event: FormEvent) => {
    event.preventDefault();
    setFormError(null);
    save.mutate();
  };

  const rows = projects.data?.items ?? [];

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Projects</h1>
          <p className="mt-1.5 max-w-2xl text-sm text-text-muted">
            Every travel, cab and hotel request is tagged against a campaign. Archiving hides one
            from new requests while keeping its history intact — campaigns are never deleted.
          </p>
        </div>
        <Button onClick={openCreate}>
          <Plus size={15} />
          New campaign
        </Button>
      </div>

      <Card>
        <CardHeader
          title={`${projects.data?.total ?? 0} ${projects.data?.total === 1 ? 'campaign' : 'campaigns'}`}
          action={
            <div className="flex gap-2">
              <div className="relative">
                <Search
                  size={14}
                  className="pointer-events-none absolute left-2.5 top-1/2 -translate-y-1/2 text-text-subtle"
                />
                <Input
                  value={search}
                  onChange={(e) => setSearch(e.target.value)}
                  placeholder="Search name, code or client"
                  className="w-56 pl-8"
                  aria-label="Search campaigns"
                />
              </div>
              <Select
                value={statusFilter}
                onChange={(e) => setStatusFilter(e.target.value)}
                aria-label="Filter by status"
                className="w-36"
              >
                <option value="">All statuses</option>
                {(Object.keys(PROJECT_STATUS_LABELS) as ProjectStatus[]).map((s) => (
                  <option key={s} value={s}>
                    {PROJECT_STATUS_LABELS[s]}
                  </option>
                ))}
              </Select>
            </div>
          }
        />

        {projects.isPending ? (
          <div className="space-y-2 p-5">
            {Array.from({ length: 4 }).map((_, i) => (
              <Skeleton key={i} className="h-12 w-full" />
            ))}
          </div>
        ) : projects.isError ? (
          <EmptyState
            icon={<FolderKanban size={28} />}
            title="Could not load campaigns"
            description={errorMessage(projects.error)}
          />
        ) : rows.length === 0 ? (
          <EmptyState
            icon={<FolderKanban size={28} />}
            title={search || statusFilter ? 'Nothing matches that' : 'No campaigns yet'}
            description={
              search || statusFilter
                ? 'Try a different search or filter.'
                : 'Create one so ground staff have something to tag their requests against.'
            }
            action={
              !search && !statusFilter ? (
                <Button onClick={openCreate}>
                  <Plus size={15} />
                  New campaign
                </Button>
              ) : undefined
            }
          />
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-border text-left text-2xs uppercase tracking-widest text-text-subtle">
                  <th className="px-5 py-2.5 font-semibold">Campaign</th>
                  <th className="hidden px-5 py-2.5 font-semibold md:table-cell">Client</th>
                  <th className="hidden px-5 py-2.5 font-semibold lg:table-cell">Location</th>
                  <th className="hidden px-5 py-2.5 font-semibold xl:table-cell">Dates</th>
                  <th className="px-5 py-2.5 font-semibold">Status</th>
                  <th className="px-5 py-2.5 text-right font-semibold">Actions</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-border">
                {rows.map((project) => (
                  <tr
                    key={project.id}
                    className={
                      project.status === 'ARCHIVED'
                        ? 'opacity-60 transition-colors hover:bg-surface-sunken/60'
                        : 'transition-colors hover:bg-surface-sunken/60'
                    }
                  >
                    <td className="px-5 py-3">
                      <div className="font-medium">{project.name}</div>
                      <div className="font-mono text-xs text-text-muted">{project.code}</div>
                    </td>
                    <td className="hidden px-5 py-3 text-text-muted md:table-cell">
                      {project.client_name || '—'}
                    </td>
                    <td className="hidden px-5 py-3 text-text-muted lg:table-cell">
                      {project.location || '—'}
                    </td>
                    <td className="hidden px-5 py-3 text-xs text-text-muted xl:table-cell">
                      {formatRange(project)}
                    </td>
                    <td className="px-5 py-3">
                      <Badge tone={STATUS_TONE[project.status]}>
                        {PROJECT_STATUS_LABELS[project.status]}
                      </Badge>
                    </td>
                    <td className="px-5 py-3">
                      <div className="flex justify-end gap-1">
                        <Button
                          variant="ghost"
                          size="sm"
                          title="Edit"
                          onClick={() => openEdit(project)}
                        >
                          <Pencil size={14} />
                        </Button>
                        <Button
                          variant="ghost"
                          size="sm"
                          title={project.status === 'ARCHIVED' ? 'Restore' : 'Archive'}
                          loading={
                            toggleArchive.isPending && toggleArchive.variables?.id === project.id
                          }
                          onClick={() => toggleArchive.mutate(project)}
                        >
                          {project.status === 'ARCHIVED' ? (
                            <ArchiveRestore size={14} />
                          ) : (
                            <Archive size={14} />
                          )}
                        </Button>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>

      <Modal
        open={formOpen}
        onClose={() => {
          setFormOpen(false);
          setFormError(null);
        }}
        title={editing ? `Edit ${editing.name}` : 'New campaign'}
        description={
          editing
            ? 'Changes are recorded in the activity log.'
            : 'Ground staff will tag their requests against this.'
        }
        footer={
          <>
            <Button variant="secondary" onClick={() => setFormOpen(false)}>
              Cancel
            </Button>
            <Button form="project-form" type="submit" loading={save.isPending}>
              {editing ? 'Save changes' : 'Create campaign'}
            </Button>
          </>
        }
      >
        <form id="project-form" onSubmit={submit} className="space-y-4" noValidate>
          <div className="grid gap-4 sm:grid-cols-2">
            <Field label="Campaign name" htmlFor="name" required className="sm:col-span-2">
              <Input
                id="name"
                required
                value={form.name}
                onChange={(e) => setForm({ ...form, name: e.target.value })}
                placeholder="Monsoon Field Survey"
              />
            </Field>

            <Field
              label="Code"
              htmlFor="code"
              required
              hint="Stored uppercase. Must be unique."
            >
              <Input
                id="code"
                required
                value={form.code}
                onChange={(e) => setForm({ ...form, code: e.target.value })}
                placeholder="MFS-2026"
                className="font-mono"
              />
            </Field>

            <Field label="Status" htmlFor="status">
              <Select
                id="status"
                value={form.status}
                onChange={(e) => setForm({ ...form, status: e.target.value })}
              >
                {(Object.keys(PROJECT_STATUS_LABELS) as ProjectStatus[]).map((s) => (
                  <option key={s} value={s}>
                    {PROJECT_STATUS_LABELS[s]}
                  </option>
                ))}
              </Select>
            </Field>

            <Field label="Client" htmlFor="client_name">
              <Input
                id="client_name"
                value={form.client_name ?? ''}
                onChange={(e) => setForm({ ...form, client_name: e.target.value })}
                placeholder="Acme Retail"
              />
            </Field>

            <Field label="Location" htmlFor="location" hint="Drives the deployment view.">
              <Input
                id="location"
                value={form.location ?? ''}
                onChange={(e) => setForm({ ...form, location: e.target.value })}
                placeholder="Telangana"
              />
            </Field>

            <Field label="Start date" htmlFor="start_date">
              <Input
                id="start_date"
                type="date"
                value={form.start_date ?? ''}
                onChange={(e) => setForm({ ...form, start_date: e.target.value })}
              />
            </Field>

            <Field label="End date" htmlFor="end_date">
              <Input
                id="end_date"
                type="date"
                value={form.end_date ?? ''}
                onChange={(e) => setForm({ ...form, end_date: e.target.value })}
              />
            </Field>
          </div>

          {formError && (
            <p role="alert" className="rounded-md bg-danger-soft px-3 py-2 text-xs text-danger">
              {formError}
            </p>
          )}
        </form>
      </Modal>
    </div>
  );
}
