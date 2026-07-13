/**
 * Shared intake payload types used by both client and server.
 * These define the contract for rich intake: screenshots, voice, files, visual context.
 */

/** A single screenshot captured during intake. */
export interface ScreenshotAsset {
  id: string;
  /** Original filename or generated label. */
  filename: string;
  /** MIME type, e.g. "image/png". */
  mimeType: string;
  /** Size in bytes. */
  sizeBytes: number;
  /** Captured screen dimensions. */
  width: number;
  height: number;
  /** Relative path under the server's storage dir, or a blob reference id. */
  storagePath: string;
  /** ISO timestamp of capture. */
  capturedAt: string;
}

/** A voice recording captured during intake. */
export interface VoiceAsset {
  id: string;
  filename: string;
  mimeType: string;
  sizeBytes: number;
  /** Duration in seconds. */
  durationSeconds: number;
  storagePath: string;
  recordedAt: string;
  /** Optional transcription once available. */
  transcript?: string;
}

/** An arbitrary uploaded file (PDF, doc, image, etc.). */
export interface UploadedFileAsset {
  id: string;
  filename: string;
  mimeType: string;
  sizeBytes: number;
  storagePath: string;
  uploadedAt: string;
}

/** Visual context metadata describing the user's environment at intake time. */
export interface VisualContext {
  /** Active application or window title, if known. */
  activeWindow?: string;
  /** Browser URL if captured from a browser. */
  activeUrl?: string;
  /** Display resolution(s). */
  displays?: Array<{ width: number; height: number; scaleFactor: number }>;
  /** Freeform notes about what the user was looking at. */
  notes?: string;
}

/** The full intake payload submitted by the client. */
export interface IntakePayload {
  id: string;
  /** Human-readable title or summary the user typed. */
  title: string;
  /** Long-form description / freeform text. */
  description: string;
  /** Tags for categorization. */
  tags: string[];
  screenshots: ScreenshotAsset[];
  voice: VoiceAsset[];
  files: UploadedFileAsset[];
  visualContext: VisualContext;
  /** ISO timestamp of submission. */
  submittedAt: string;
}

/** Server response after intake is accepted. */
export interface IntakeAck {
  id: string;
  accepted: boolean;
  receivedAssetCount: number;
  message: string;
}

/** Generic API envelope. */
export interface ApiError {
  error: string;
  detail?: string;
}


// --- DUPLICATE BLOCK ---

// shared/types.ts
// Shared types between client and server for Job-Star intake system

export type IntakeStatus = 'draft' | 'submitted' | 'processing';

export interface IntakeAttachment {
  id: string;
  filename: string;
  mimeType: string;
  size: number;
  /** Path on server where the file is stored */
  path?: string;
}

export interface IntakeScreenshot {
  id: string;
  filename: string;
  /** Base64 data URL preview, used by client; server stores the file */
  dataUrl: string;
  mimeType: string;
}

export interface IntakeVoiceRecording {
  id: string;
  filename: string;
  durationMs: number;
  mimeType: string;
  /** Base64 data URL preview */
  dataUrl: string;
}

export interface IntakeScreenCapture {
  id: string;
  filename: string;
  dataUrl: string;
  mimeType: string;
}

export interface Intake {
  id: string;
  title: string;
  description: string;
  status: IntakeStatus;
  attachments: IntakeAttachment[];
  screenshots: IntakeScreenshot[];
  voiceRecordings: IntakeVoiceRecording[];
  screenCaptures: IntakeScreenCapture[];
  createdAt: string;
  updatedAt: string;
}

/** Payload sent from client to server on intake submission */
export interface IntakeCreatePayload {
  title: string;
  description: string;
  status?: IntakeStatus;
  attachments?: IntakeAttachment[];
  screenshots?: IntakeScreenshot[];
  voiceRecordings?: IntakeVoiceRecording[];
  screenCaptures?: IntakeScreenCapture[];
}

/** Response from the server after creating an intake */
export interface IntakeCreateResponse {
  intake: Intake;
}

/** Response from the list endpoint */
export interface IntakeListResponse {
  intakes: Intake[];
}
