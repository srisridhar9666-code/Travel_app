import type { ReactNode } from 'react';

import { Modal } from '@/components/Modal';
import { Button } from '@/components/ui';

interface ConfirmDialogProps {
  open: boolean;
  title: string;
  description?: string;
  /** What will happen, in a sentence or two. */
  children?: ReactNode;
  confirmLabel: string;
  cancelLabel?: string;
  /** `danger` for what cannot be undone; `primary` for what can. */
  tone?: 'danger' | 'primary';
  loading?: boolean;
  onConfirm: () => void;
  onClose: () => void;
}

/**
 * "Are you sure?" for actions that remove something or lock someone out.
 *
 * Replaces the browser's own confirm box, which looks foreign next to the app
 * and is one careless tap on a phone. The confirm button names the action
 * ("Delete campaign"), so the choice reads without the title.
 */
export function ConfirmDialog({
  open,
  title,
  description,
  children,
  confirmLabel,
  cancelLabel = 'Cancel',
  tone = 'danger',
  loading,
  onConfirm,
  onClose,
}: ConfirmDialogProps) {
  return (
    <Modal
      open={open}
      onClose={onClose}
      title={title}
      description={description}
      footer={
        <>
          <Button variant="secondary" onClick={onClose} disabled={loading}>
            {cancelLabel}
          </Button>
          <Button variant={tone} loading={loading} onClick={onConfirm}>
            {confirmLabel}
          </Button>
        </>
      }
    >
      {children && <div className="space-y-2 text-sm text-text-muted">{children}</div>}
    </Modal>
  );
}
