import { useState, useEffect, useRef } from 'react';
import Modal from './Modal';
import Button from './Button';

interface Props {
  open: boolean;
  title: string;
  message?: string;
  placeholder?: string;
  defaultValue?: string;
  confirmLabel?: string;
  onConfirm: (value: string) => void;
  onCancel: () => void;
}

export default function PromptDialog({
  open,
  title,
  message,
  placeholder = '',
  defaultValue = '',
  confirmLabel = 'Save',
  onConfirm,
  onCancel,
}: Props) {
  const [value, setValue] = useState(defaultValue);
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (open) {
      setValue(defaultValue);
      setTimeout(() => inputRef.current?.focus(), 100);
    }
  }, [open, defaultValue]);

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (value.trim()) onConfirm(value.trim());
  };

  return (
    <Modal open={open} onClose={onCancel} position="center">
      <div className="p-5" role="dialog" aria-label={title}>
        <h3 className="text-base font-bold mb-2" style={{ color: 'var(--text-primary)' }}>{title}</h3>
        {message && <p className="text-sm mb-3" style={{ color: 'var(--text-secondary)' }}>{message}</p>}
        <form onSubmit={handleSubmit}>
          <input
            ref={inputRef}
            value={value}
            onChange={(e) => setValue(e.target.value)}
            placeholder={placeholder}
            className="glass-input w-full text-sm mb-4"
          />
          <div className="flex gap-3">
            <Button type="button" variant="secondary" size="md" className="flex-1" onClick={onCancel}>
              Cancel
            </Button>
            <Button type="submit" variant="primary" size="md" className="flex-1" disabled={!value.trim()}>
              {confirmLabel}
            </Button>
          </div>
        </form>
      </div>
    </Modal>
  );
}
