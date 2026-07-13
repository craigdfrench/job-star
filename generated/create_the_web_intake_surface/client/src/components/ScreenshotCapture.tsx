import React, { useCallback, useEffect, useRef, useState } from 'react';

export interface CapturedImage {
  id: string;
  file: File;
  previewUrl: string;
  source: 'paste' | 'upload';
}

interface ScreenshotCaptureProps {
  images: CapturedImage[];
  onAdd: (images: CapturedImage[]) => void;
  onRemove: (id: string) => void;
  // Optional: only capture pastes when this element (or a child) is focused.
  // If true, listens globally on window.
  globalPaste?: boolean;
}

const IMAGE_MIME_TYPES = ['image/png', 'image/jpeg', 'image/webp', 'image/gif'];

function isImageFile(file: File): boolean {
  return IMAGE_MIME_TYPES.includes(file.type);
}

function makeId(): string {
  return `img_${Date.now()}_${Math.random().toString(36).slice(2, 8)}`;
}

function fileToCaptured(file: File, source: 'paste' | 'upload'): CapturedImage {
  return {
    id: makeId(),
    file,
    previewUrl: URL.createObjectURL(file),
    source,
  };
}

export const ScreenshotCapture: React.FC<ScreenshotCaptureProps> = ({
  images,
  onAdd,
  onRemove,
  globalPaste = true,
}) => {
  const fileInputRef = useRef<HTMLInputElement>(null);
  const [isDragging, setIsDragging] = useState(false);
  const [pasteFlash, setPasteFlash] = useState(false);

  // Listen for paste events to grab screenshots from the clipboard.
  useEffect(() => {
    if (!globalPaste) return;

    const handlePaste = (e: ClipboardEvent) => {
      const items = e.clipboardData?.items;
      if (!items) return;

      const captured: CapturedImage[] = [];
      for (let i = 0; i < items.length; i++) {
        const item = items[i];
        if (item.kind === 'file' && isImageFile(item.type)) {
          const file = item.getAsFile();
          if (file) {
            // Browsers often name pasted images "image.png" — give a more
            // descriptive name so the backend can distinguish screenshots.
            const renamed = new File([file], `screenshot-${Date.now()}.png`, {
              type: file.type,
            });
            captured.push(fileToCaptured(renamed, 'paste'));
          }
        }
      }

      if (captured.length > 0) {
        e.preventDefault();
        onAdd(captured);
        setPasteFlash(true);
        window.setTimeout(() => setPasteFlash(false), 400);
      }
    };

    window.addEventListener('paste', handlePaste);
    return () => window.removeEventListener('paste', handlePaste);
  }, [globalPaste, onAdd]);

  // Revoke object URLs on unmount to avoid memory leaks.
  useEffect(() => {
    return () => {
      images.forEach((img) => URL.revokeObjectURL(img.previewUrl));
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const handleFilePick = useCallback(
    (files: FileList | null) => {
      if (!files) return;
      const picked: CapturedImage[] = [];
      Array.from(files).forEach((file) => {
        if (isImageFile(file)) {
          picked.push(fileToCaptured(file, 'upload'));
        }
      });
      if (picked.length > 0) onAdd(picked);
    },
    [onAdd]
  );

  const handleInputChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    handleFilePick(e.target.files);
    // Reset so picking the same file again still fires change.
    e.target.value = '';
  };

  const handleDrop = (e: React.DragEvent<HTMLDivElement>) => {
    e.preventDefault();
    setIsDragging(false);
    handleFilePick(e.dataTransfer.files);
  };

  const handleDragOver = (e: React.DragEvent<HTMLDivElement>) => {
    e.preventDefault();
    if (!isDragging) setIsDragging(true);
  };

  const handleDragLeave = (e: React.DragEvent<HTMLDivElement>) => {
    e.preventDefault();
    setIsDragging(false);
  };

  return (
    <div className="screenshot-capture">
      <div className="screenshot-capture__header">
        <span className="screenshot-capture__label">Screenshots &amp; images</span>
        <span
          className={`screenshot-capture__hint ${pasteFlash ? 'is-flash' : ''}`}
          aria-live="polite"
        >
          {pasteFlash ? 'Pasted!' : 'Press Ctrl/Cmd+V to paste a screenshot'}
        </span>
      </div>

      <div
        className={`screenshot-capture__dropzone ${isDragging ? 'is-dragging' : ''}`}
        onDrop={handleDrop}
        onDragOver={handleDragOver}
        onDragLeave={handleDragLeave}
        onClick={() => fileInputRef.current?.click()}
        role="button"
        tabIndex={0}
        onKeyDown={(e) => {
          if (e.key === 'Enter' || e.key === ' ') {
            e.preventDefault();
            fileInputRef.current?.click();
          }
        }}
      >
        <div className="screenshot-capture__dropzone-inner">
          <span className="screenshot-capture__icon">🖼️</span>
          <span className="screenshot-capture__dropzone-text">
            Click to upload an image, or paste from clipboard
          </span>
        </div>
        <input
          ref={fileInputRef}
          type="file"
          accept={IMAGE_MIME_TYPES.join(',')}
          multiple
          onChange={handleInputChange}
          style={{ display: 'none' }}
          aria-label="Upload images"
        />
      </div>

      {images.length > 0 && (
        <ul className="screenshot-capture__thumbs">
          {images.map((img) => (
            <li key={img.id} className="screenshot-capture__thumb">
              <img
                src={img.previewUrl}
                alt={img.file.name}
                className="screenshot-capture__thumb-img"
              />
              <div className="screenshot-capture__thumb-meta">
                <span className="screenshot-capture__thumb-name" title={img.file.name}>
                  {img.file.name}
                </span>
                <span className="screenshot-capture__thumb-source">
                  {img.source === 'paste' ? 'pasted' : 'uploaded'}
                </span>
              </div>
              <button
                type="button"
                className="screenshot-capture__remove"
                onClick={() => onRemove(img.id)}
                aria-label={`Remove ${img.file.name}`}
              >
                ×
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
};

export default ScreenshotCapture;
