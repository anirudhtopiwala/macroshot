import { useRef, useCallback, useMemo, useEffect } from 'react';
import { Plus, X } from './icons';

const MAX_DIMENSION = 1280;
const JPEG_QUALITY = 0.8;

/** Compress an image file via canvas - returns a smaller JPEG File */
async function compressImage(file: File): Promise<File> {
  // Skip small files (< 500 KB)
  if (file.size < 500 * 1024) return file;

  return new Promise((resolve) => {
    let settled = false;
    const finish = (out: File) => {
      if (settled) return;
      settled = true;
      resolve(out);
    };
    // Hard ceiling: if onload/onerror never fires or toBlob never returns,
    // fall back to the original file so the upload button isn't stuck.
    const watchdog = setTimeout(() => finish(file), 5000);

    const img = new Image();
    img.onload = () => {
      let { width, height } = img;
      if (width > MAX_DIMENSION || height > MAX_DIMENSION) {
        const ratio = Math.min(MAX_DIMENSION / width, MAX_DIMENSION / height);
        width = Math.round(width * ratio);
        height = Math.round(height * ratio);
      }
      const canvas = document.createElement('canvas');
      canvas.width = width;
      canvas.height = height;
      const ctx = canvas.getContext('2d');
      ctx?.drawImage(img, 0, 0, width, height);
      canvas.toBlob(
        (blob) => {
          clearTimeout(watchdog);
          if (blob && blob.size < file.size) {
            finish(new File([blob], file.name.replace(/\.\w+$/, '.jpg'), { type: 'image/jpeg' }));
          } else {
            finish(file);
          }
        },
        'image/jpeg',
        JPEG_QUALITY,
      );
      URL.revokeObjectURL(img.src);
    };
    img.onerror = () => {
      clearTimeout(watchdog);
      URL.revokeObjectURL(img.src);
      finish(file);
    };
    img.src = URL.createObjectURL(file);
  });
}

interface Props {
  images: File[];
  onChange: (images: File[]) => void;
  maxImages?: number;
  /** When true, hides the "add photo" button (cap reached). Existing
   *  photos stay visible and removable so the user can recover from an
   *  accidental capture. */
  disabled?: boolean;
  /** aria-label for the add button; defaults to "Add photo". */
  addLabel?: string;
}

export default function ImageCapture({ images, onChange, maxImages = 5, disabled = false, addLabel }: Props) {
  const inputRef = useRef<HTMLInputElement>(null);

  // Create stable blob URLs and revoke old ones on change
  const blobUrls = useMemo(() => images.map((img) => URL.createObjectURL(img)), [images]);

  useEffect(() => {
    return () => {
      blobUrls.forEach((url) => URL.revokeObjectURL(url));
    };
  }, [blobUrls]);

  const handleFiles = useCallback(async (files: FileList | null) => {
    if (!files) return;
    const newImages = [...images];
    for (let i = 0; i < files.length && newImages.length < maxImages; i++) {
      const compressed = await compressImage(files[i]);
      newImages.push(compressed);
    }
    onChange(newImages);
  }, [images, onChange, maxImages]);

  const removeImage = useCallback((index: number) => {
    onChange(images.filter((_, i) => i !== index));
  }, [images, onChange]);

  // Show ghost slots to hint at multi-photo support
  const ghostCount = images.length === 0 ? 2 : 0;

  return (
    <div>
      <div className="flex gap-2 overflow-x-auto pb-2 pt-2">
        {images.map((_, i) => (
          <div key={i} className="relative shrink-0">
            <img
              src={blobUrls[i]}
              alt={`Photo ${i + 1}`}
              className="w-24 h-24 object-cover rounded-xl"
              style={{ border: '1px solid var(--border-glass)' }}
            />
            <button
              onClick={() => removeImage(i)}
              className="absolute -top-2 -right-2 w-7 h-7 bg-red-500/90 backdrop-blur-sm rounded-full flex items-center justify-center"
            >
              <X className="w-3.5 h-3.5 text-white" />
            </button>
          </div>
        ))}
        {images.length < maxImages && (
          <button
            onClick={() => { if (!disabled) inputRef.current?.click(); }}
            disabled={disabled}
            aria-label={addLabel ?? 'Add photo'}
            aria-disabled={disabled}
            className="w-24 h-24 border-2 border-dashed rounded-xl flex flex-col items-center justify-center transition-all duration-200 shrink-0 disabled:cursor-not-allowed disabled:opacity-50 enabled:hover:border-emerald-500/50 enabled:hover:text-emerald-400"
            style={{ borderColor: 'var(--border-glass)', color: 'var(--text-muted)', background: 'var(--track-bg)' }}
          >
            <Plus className="w-6 h-6" />
            <span className="text-[10px] mt-1">Photo</span>
          </button>
        )}
        {/* Ghost slots - visible only when no photos added yet */}
        {Array.from({ length: ghostCount }).map((_, i) => (
          <div
            key={`ghost-${i}`}
            onClick={() => { if (!disabled) inputRef.current?.click(); }}
            className="w-24 h-24 border-2 border-dashed rounded-xl flex items-center justify-center shrink-0"
            style={{ borderColor: 'var(--border-glass)', opacity: disabled ? 0.25 : 0.45 - i * 0.12, cursor: disabled ? 'not-allowed' : 'pointer' }}
          >
            <Plus className="w-5 h-5" style={{ color: 'var(--text-muted)' }} />
          </div>
        ))}
      </div>
      <input
        ref={inputRef}
        type="file"
        accept="image/*"
        multiple
        onChange={(e) => handleFiles(e.target.files)}
        className="hidden"
      />
    </div>
  );
}
