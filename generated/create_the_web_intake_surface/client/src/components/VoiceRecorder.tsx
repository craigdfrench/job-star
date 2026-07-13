import React, { useState, useRef, useEffect, useCallback } from 'react';

export interface VoiceRecorderProps {
  /** Called whenever the recorded blob changes (including when cleared). */
  onAudioChange: (blob: Blob | null, meta: { durationMs: number; mimeType: string } | null) => void;
  /** Disable all controls. */
  disabled?: boolean;
}

type RecorderState = 'idle' | 'recording' | 'stopped' | 'error';

const MIME_CANDIDATES = [
  'audio/webm;codecs=opus',
  'audio/webm',
  'audio/ogg;codecs=opus',
  'audio/mp4',
];

function pickMimeType(): string {
  if (typeof MediaRecorder === 'undefined') return '';
  for (const candidate of MIME_CANDIDATES) {
    if (MediaRecorder.isTypeSupported(candidate)) return candidate;
  }
  return '';
}

function formatDuration(ms: number): string {
  const totalSeconds = Math.floor(ms / 1000);
  const minutes = Math.floor(totalSeconds / 60);
  const seconds = totalSeconds % 60;
  return `${minutes.toString().padStart(2, '0')}:${seconds.toString().padStart(2, '0')}`;
}

export const VoiceRecorder: React.FC<VoiceRecorderProps> = ({ onAudioChange, disabled = false }) => {
  const [state, setState] = useState<RecorderState>('idle');
  const [errorMsg, setErrorMsg] = useState<string>('');
  const [durationMs, setDurationMs] = useState<number>(0);
  const [audioUrl, setAudioUrl] = useState<string | null>(null);
  const [finalBlob, setFinalBlob] = useState<Blob | null>(null);
  const [finalDurationMs, setFinalDurationMs] = useState<number>(0);
  const [finalMime, setFinalMime] = useState<string>('');

  const mediaRecorderRef = useRef<MediaRecorder | null>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const chunksRef = useRef<BlobPart[]>([]);
  const startTimeRef = useRef<number>(0);
  const tickerRef = useRef<number | null>(null);

  // Clean up on unmount.
  useEffect(() => {
    return () => {
      stopTicker();
      stopStream();
      if (audioUrl) URL.revokeObjectURL(audioUrl);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const stopTicker = () => {
    if (tickerRef.current !== null) {
      window.clearInterval(tickerRef.current);
      tickerRef.current = null;
    }
  };

  const stopStream = () => {
    if (streamRef.current) {
      streamRef.current.getTracks().forEach((t) => t.stop());
      streamRef.current = null;
    }
  };

  const startTicker = useCallback(() => {
    stopTicker();
    startTimeRef.current = Date.now();
    setDurationMs(0);
    tickerRef.current = window.setInterval(() => {
      setDurationMs(Date.now() - startTimeRef.current);
    }, 200);
  }, []);

  const handleDataAvailable = useCallback((e: BlobEvent) => {
    if (e.data && e.data.size > 0) {
      chunksRef.current.push(e.data);
    }
  }, []);

  const handleStop = useCallback(() => {
    stopTicker();
    const mime = mediaRecorderRef.current?.mimeType || pickMimeType() || 'audio/webm';
    const blob = new Blob(chunksRef.current, { type: mime });
    const recordedDuration = Date.now() - startTimeRef.current;

    if (blob.size === 0) {
      setErrorMsg('Recording produced no audio data.');
      setState('error');
      stopStream();
      return;
    }

    const url = URL.createObjectURL(blob);
    setAudioUrl((prev) => {
      if (prev) URL.revokeObjectURL(prev);
      return url;
    });
    setFinalBlob(blob);
    setFinalDurationMs(recordedDuration);
    setFinalMime(mime);
    onAudioChange(blob, { durationMs: recordedDuration, mimeType: mime });
    setState('stopped');
    stopStream();
  }, [onAudioChange]);

  const startRecording = useCallback(async () => {
    setErrorMsg('');
    if (typeof MediaRecorder === 'undefined' || !navigator.mediaDevices?.getUserMedia) {
      setErrorMsg('Audio recording is not supported in this browser.');
      setState('error');
      return;
    }

    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      streamRef.current = stream;
      chunksRef.current = [];

      const mimeType = pickMimeType();
      const recorder = mimeType
        ? new MediaRecorder(stream, { mimeType })
        : new MediaRecorder(stream);

      recorder.ondataavailable = handleDataAvailable;
      recorder.onstop = handleStop;
      recorder.onerror = () => {
        setErrorMsg('Recording error occurred.');
        setState('error');
        stopTicker();
        stopStream();
      };

      // Reset previous recording state when starting a new one.
      if (audioUrl) {
        URL.revokeObjectURL(audioUrl);
        setAudioUrl(null);
      }
      setFinalBlob(null);
      setFinalDurationMs(0);
      setFinalMime('');
      onAudioChange(null, null);

      recorder.start(250); // collect data in 250ms chunks
      mediaRecorderRef.current = recorder;
      startTicker();
      setState('recording');
    } catch (err) {
      const message =
        err instanceof DOMException && err.name === 'NotAllowedError'
          ? 'Microphone permission denied.'
          : err instanceof Error
            ? err.message
            : 'Failed to access microphone.';
      setErrorMsg(message);
      setState('error');
      stopStream();
    }
  }, [audioUrl, handleDataAvailable, handleStop, onAudioChange, startTicker]);

  const stopRecording = useCallback(() => {
    const recorder = mediaRecorderRef.current;
    if (recorder && recorder.state !== 'inactive') {
      recorder.stop();
    }
    mediaRecorderRef.current = null;
  }, []);

  const clearRecording = useCallback(() => {
    if (audioUrl) URL.revokeObjectURL(audioUrl);
    setAudioUrl(null);
    setFinalBlob(null);
    setFinalDurationMs(0);
    setFinalMime('');
    setDurationMs(0);
    setErrorMsg('');
    setState('idle');
    onAudioChange(null, null);
  }, [audioUrl, onAudioChange]);

  const isRecording = state === 'recording';
  const hasRecording = state === 'stopped' && !!audioUrl;

  return (
    <div className="voice-recorder" style={containerStyle}>
      <div className="voice-recorder__header" style={headerStyle}>
        <span style={labelStyle}>🎙️ Voice note</span>
        <span style={durationStyle(isRecording)}>
          {isRecording ? formatDuration(durationMs) : hasRecording ? formatDuration(finalDurationMs) : '00:00'}
        </span>
      </div>

      <div className="voice-recorder__controls" style={controlsStyle}>
        {!isRecording && !hasRecording && (
          <button
            type="button"
            onClick={startRecording}
            disabled={disabled}
            style={recordBtnStyle}
            aria-label="Start recording"
          >
            ● Record
          </button>
        )}

        {isRecording && (
          <button
            type="button"
            onClick={stopRecording}
            disabled={disabled}
            style={stopBtnStyle}
            aria-label="Stop recording"
          >
            ■ Stop
          </button>
        )}

        {hasRecording && (
          <>
            <button
              type="button"
              onClick={startRecording}
              disabled={disabled}
              style={reRecordBtnStyle}
              aria-label="Record again"
            >
              ↻ Re-record
            </button>
            <button
              type="button"
              onClick={clearRecording}
              disabled={disabled}
              style={clearBtnStyle}
              aria-label="Clear recording"
            >
              ✕ Clear
            </button>
          </>
        )}
      </div>

      {isRecording && (
        <div className="voice-recorder__pulse" style={pulseStyle}>
          <span style={dotStyle} /> Recording…
        </div>
      )}

      {hasRecording && audioUrl && (
        <div className="voice-recorder__preview" style={previewStyle}>
          <audio src={audioUrl} controls style={{ width: '100%' }} />
          <div style={metaStyle}>
            {finalBlob && (
              <span>{(finalBlob.size / 1024).toFixed(1)} KB · {finalMime}</span>
            )}
          </div>
        </div>
      )}

      {state === 'error' && errorMsg && (
        <div className="voice-recorder__error" style={errorStyle}>
          ⚠ {errorMsg}
        </div>
      )}
    </div>
  );
};

/* ---- inline styles (consistent with other Job-Star client components) ---- */

const containerStyle: React.CSSProperties = {
  border: '1px solid #d0d7de',
  borderRadius: 8,
  padding: 16,
  background: '#f6f8fa',
  display: 'flex',
  flexDirection: 'column',
  gap: 12,
};

const headerStyle: React.CSSProperties = {
  display: 'flex',
  justifyContent: 'space-between',
  alignItems: 'center',
};

const labelStyle: React.CSSProperties = {
  fontWeight: 600,
  fontSize: 14,
  color: '#24292f',
};

const durationStyle = (recording: boolean): React.CSSProperties => ({
  fontFamily: 'monospace',
  fontSize: 14,
  color: recording ? '#cf222e' : '#57606a',
  fontWeight: 600,
});

const controlsStyle: React.CSSProperties = {
  display: 'flex',
  gap: 8,
  flexWrap: 'wrap',
};

const baseBtnStyle: React.CSSProperties = {
  padding: '8px 14px',
  borderRadius: 6,
  border: '1px solid #d0d7de',
  cursor: 'pointer',
  fontSize: 14,
  fontWeight: 500,
};

const recordBtnStyle: React.CSSProperties = {
  ...baseBtnStyle,
  background: '#fff',
  color: '#cf222e',
  borderColor: '#cf222e',
};

const stopBtnStyle: React.CSSProperties = {
  ...baseBtnStyle,
  background: '#cf222e',
  color: '#fff',
  borderColor: '#cf222e',
};

const reRecordBtnStyle: React.CSSProperties = {
  ...baseBtnStyle,
  background: '#fff',
  color: '#0969da',
  borderColor: '#0969da',
};

const clearBtnStyle: React.CSSProperties = {
  ...baseBtnStyle,
  background: '#fff',
  color: '#57606a',
};

const pulseStyle: React.CSSProperties = {
  display: 'flex',
  alignItems: 'center',
  gap: 8,
  color: '#cf222e',
  fontSize: 13,
};

const dotStyle: React.CSSProperties = {
  width: 10,
  height: 10,
  borderRadius: '50%',
  background: '#cf222e',
  display: 'inline-block',
  animation: 'voicePulse 1s infinite',
};

const previewStyle: React.CSSProperties = {
  display: 'flex',
  flexDirection: 'column',
  gap: 6,
};

const metaStyle: React.CSSProperties = {
  fontSize: 12,
  color: '#57606a',
};

const errorStyle: React.CSSProperties = {
  color: '#cf222e',
  fontSize: 13,
  background: '#ffebe9',
  padding: '6px 10px',
  borderRadius: 6,
  border: '1px solid #ffcecb',
};

export default VoiceRecorder;
