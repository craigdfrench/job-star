import React, { useCallback, useRef, useState } from 'react';

export interface UploadedFile {
  file: File;
  id: string;
}

interface FileUploadProps {
  files: UploadedFile[];
  onFilesChange: (files: UploadedFile[]) => void;
  label?: string;
}

/**
 * Format bytes into a human-readable string.
 */
function formatSize(bytes: number): string {
  if (bytes === 0) return '0 B';
  const units = ['B', 'KB', 'MB', 'GB', 'TB'];
  const i = Math.floor(Math.log(bytes) / Math.log(1024));
  const value = bytes / Math.pow(1024, i);
  return `${value.toFixed(value >= 10 || i === 0 ? 0 : 1)} ${units[i]}`;
}

/**
 * Drag-and-drop file upload zone.
 * Supports multiple files of any type. Maintains an id per file for stable
 * React keys and removal.
 */
export const FileUpload: React.FC<FileUploadProps> = ({
  files,
  onFilesChange,
  label = 'Drop files here or click to browse',
}) => {
  const [isDragging, setIsDragging] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);

  const addFiles = useCallback(
    (incoming: FileList | File[]) => {
      const arr = Array.from(incoming);
      const newFiles: UploadedFile[] = arr.map((file) => ({
        file,
        id:
          typeof crypto !== 'undefined' && 'randomUUID' in crypto
            ? crypto.randomUUID()
            : `${file.name}-${file.size}-${Date.now()}-${Math.random()
                .toString(36)
                .slice(2)}`,
      }));
      onFilesChange([...files, ...newFiles]);
    },
    [files, onFilesChange]
  );

  const handleDrop = useCallback(
    (e: React.DragEvent<HTMLDivElement>) => {
      e.preventDefault();
      e.stopPropagation();
      setIsDragging(false);
      if (e.dataTransfer.files && e.dataTransfer.files.length > 0) {
        addFiles(e.dataTransfer.files);
      }
    },
    [addFiles]
  );

  const handleDragOver = useCallback((e: React.DragEvent<HTMLDivElement>) => {
    e.preventDefault();
    e.stopPropagation();
    setIsDragging(true);
  }, []);

  const handleDragLeave = useCallback((e: React.DragEvent<HTMLDivElement>) => {
    e.preventDefault();
    e.stopPropagation();
    setIsDragging(false);
  }, []);

  const handleInputChange = useCallback(
    (e: React.ChangeEvent<HTMLInputElement>) => {
      if (e.target.files) {
        addFiles(e.target.files);
      }
      // Reset so the same file can be re-added if removed and re-dropped.
      e.target.value = '';
    },
    [addFiles]
  );

  const removeFile = useCallback(
    (id: string) => {
      onFilesChange(files.filter((f) => f.id !== id));
    },
    [files, onFilesChange]
  );

  const openPicker = useCallback(() => {
    inputRef.current?.click();
  }, []);

  return (
    <div className="file-upload">
      <div
        className={`file-upload__dropzone${isDragging ? ' is-dragging' : ''}`}
        onDrop={handleDrop}
        onDragOver={handleDragOver}
        onDragLeave={handleDragLeave}
        onClick={openPicker}
        role="button"
        tabIndex={0}
        onKeyDown={(e) => {
          if (e.key === 'Enter' || e.key === ' ') {
            e.preventDefault();
            openPicker();
          }
        }}
      >
        <input
          ref={inputRef}
          type="file"
          multiple
          className="file-upload__input"
          onChange={handleInputChange}
          aria-label={label}
        />
        <div className="file-upload__label">
          <span className="file-upload__icon" aria-hidden="true">
            ⬆
          </span>
          <span>{label}</span>
          <span className="file-upload__hint">Any file type · multiple allowed</span>
        </div>
      </div>

      {files.length > 0 && (
        <ul className="file-upload__list">
          {files.map(({ id, file }) => (
            <li key={id} className="file-upload__item">
              <span className="file-upload__name" title={file.name}>
                {file.name}
              </span>
              <span className="file-upload__size">{formatSize(file.size)}</span>
              <button
                type="button"
                className="file-upload__remove"
                onClick={() => removeFile(id)}
                aria-label={`Remove ${file.name}`}
              >
                ✕
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
};

export default FileUpload;
