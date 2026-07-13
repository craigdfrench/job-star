import { useCallback, useRef, useState } from 'react';

export interface CapturedScreen {
  id: string;
  file: File;
  previewUrl: string;
  label: string;
}

interface ScreenCaptureProps {
  onCapture: (capture: CapturedScreen) => void;
  onError?: (message: string) => void;
}

/**
 * ScreenCapture
 *
 * Uses navigator.mediaDevices.getDisplayMedia() to prompt the user to pick a
 * window or screen to share. We grab a single video frame, draw it to a canvas,
 * and export it as a PNG File. The track is stopped immediately after capture
 * so the browser's "sharing" indicator disappears.
 *
 * This is an alternative to clipboard-paste screenshots — useful when the user
 * wants to capture something that isn't easily copyable, or wants to pick a
 * specific window.
 */
export function ScreenCapture({ onCapture, onError }: ScreenCaptureProps) {
  const [capturing, setCapturing] = useState(false);
  const [lastPreview, setLastPreview] = useState<string | null>(null);
  const canvasRef = useRef<HTMLCanvasElement | null>(null);

  const isSupported =
    typeof navigator !== 'undefined' &&
    !!navigator.mediaDevices &&
    typeof navigator.mediaDevices.getDisplayMedia === 'function';

  const capture = useCallback(async () => {
    if (!isSupported) {
      onError?.('Screen capture is not supported in this browser.');
      return;
    }

    setCapturing(true);
    let stream: MediaStream | null = null;

    try {
      stream = await navigator.mediaDevices.getDisplayMedia({
        video: { frameRate: { ideal: 1, max: 1 } } as MediaTrackConstraints,
        audio: false,
      });

      const video = document.createElement('video');
      video.srcObject = stream;
      video.muted = true;
      video.playsInline = true;

      // Wait for metadata + one frame to be available.
      await new Promise<void>((resolve, reject) => {
        video.onloadedmetadata = () => resolve();
        video.onerror = () => reject(new Error('Failed to load video stream.'));
      });

      await video.play();

      // Give the compositor a tick to paint the first frame.
      await new Promise((r) => requestAnimationFrame(r));
      await new Promise((r) => requestAnimationFrame(r));

      const width = video.videoWidth;
      const height = video.videoHeight;

      const canvas = canvasRef.current ?? document.createElement('canvas');
      canvas.width = width;
      canvas.height = height;
      const ctx = canvas.getContext('2d');
      if (!ctx) throw new Error('Could not get 2D canvas context.');
      ctx.drawImage(video, 0, 0, width, height);

      const blob = await new Promise<Blob | null>((resolve) =>
        canvas.toBlob((b) => resolve(b), 'image/png'),
      );
      if (!blob) throw new Error('Failed to encode screenshot as PNG.');

      const timestamp = new Date().toISOString().replace(/[:.]/g, '-');
      const file = new File([blob], `screen-${timestamp}.png`, {
        type: 'image/png',
      });
      const previewUrl = URL.createObjectURL(blob);

      setLastPreview(previewUrl);

      onCapture({
        id: `screen-${crypto.randomUUID()}`,
        file,
        previewUrl,
        label: file.name,
      });
    } catch (err) {
      const message =
        err instanceof DOMException && err.name === 'NotAllowedError'
          ? 'Screen capture permission was denied.'
          : err instanceof Error
            ? err.message
            : 'Screen capture failed.';
      onError?.(message);
    } finally {
      // Always stop tracks so the "sharing" bar goes away.
      if (stream) {
        stream.getTracks().forEach((t) => t.stop());
      }
      setCapturing(false);
    }
  }, [isSupported, onCapture, onError]);

  return (
    <div className="screen-capture">
      <canvas ref={canvasRef} style={{ display: 'none' }} />

      <div className="screen-capture__header">
        <h3>Screen / Window Capture</h3>
        <p className="screen-capture__hint">
          Pick a window or screen to capture a single screenshot. Useful when you
          can't easily copy-paste (e.g. another app, a PDF, a browser tab).
        </p>
      </div>

      <button
        type="button"
        className="btn btn--secondary screen-capture__button"
        onClick={capture}
        disabled={!isSupported || capturing}
        title={!isSupported ? 'Not supported in this browser' : undefined}
      >
        {capturing ? 'Capturing…' : 'Capture screen / window'}
      </button>

      {lastPreview && (
        <div className="screen-capture__preview">
          <span className="screen-capture__preview-label">Last capture:</span>
          <img src={lastPreview} alt="Last screen capture" />
        </div>
      )}

      {!isSupported && (
        <p className="screen-capture__unsupported">
          Your browser does not support screen capture (getDisplayMedia).
        </p>
      )}
    </div>
  );
}

export default ScreenCapture;
