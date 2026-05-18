export type ChatThreadSummary = {
  thread_id: string;
  title: string;
  last_item_preview: string;
  last_item_type: string | null;
  last_message_at: string | null;
  item_count: number;
  created_at: string;
  updated_at: string;
  archived_at: string | null;
};

export type ChatTimelineItem = {
  id: number;
  thread_id: string;
  seq: number;
  item_type: "user" | "assistant" | "clarification" | "capability_notice" | "milestone";
  content: string;
  metadata: Record<string, unknown>;
  created_at: string;
};

export type WeReadArtifact = {
  artifact_id: string;
  kind: "weread_markdown" | "weread_qr_code";
  doc_id?: string;
  book_id?: string;
  book_title?: string;
  source_type?: "marks" | "reviews";
  name: string;
  generated_at?: string;
  qr_image_base64?: string;
  session_id?: string;
  expires_at?: string;
};

export type ClarificationPayload = {
  question: string;
  options: string[];
  clarification_type?: string;
};

/** SSE from chat stream when a tool signals missing capability (e.g. WeRead binding). */
export type CapabilityRequiredPayload = {
  provider: string;
  reason: string;
  message: string;
  action?: string;
  source_tool?: string;
};

export type ToolCallPayload = {
  message?: string;
};

export type ErrorStreamPayload = {
  message: string;
  error_type?: string;
  detail?: string;
};

export type StreamEvent =
  | { type: "chunk"; data: { content?: string } }
  | { type: "status"; data: { stage?: string; message?: string; error?: string } }
  | { type: "clarification"; data: ClarificationPayload }
  | { type: "capability_required"; data: CapabilityRequiredPayload }
  | { type: "artifact"; data: { artifacts?: WeReadArtifact[] } }
  | { type: "tool_call"; data: ToolCallPayload }
  | { type: "error"; data: ErrorStreamPayload }
  | { type: "done"; data: Record<string, never> };

export type ChatTurnResponse = {
  type: "answer" | "clarification";
  data: Record<string, unknown>;
};

export type WeReadBinding = {
  connected: boolean;
  status: "active" | "expired" | "reauth_required" | null;
  source: "manual" | "cookie_cloud" | "qr" | null;
  last_error: string | null;
  last_validated_at: string | null;
  updated_at: string | null;
};

export type WeReadQrLoginSession = {
  session_id: string;
  status: "pending" | "qr_ready" | "success" | "expired" | "failed" | "cancelled";
  qr_image_base64: string | null;
  last_error: string | null;
  expires_at: string;
  created_at: string;
  updated_at: string;
  completed_at: string | null;
};

export type WereadDocMeta = {
  doc_id: string;
  book_title: string;
  source_type: "marks" | "reviews";
  item_count: number;
  generated_at: string;
};

export type UserWereadBook = {
  book_id: string;
  book_title: string;
  author: string;
  documents: WereadDocMeta[];
};

export type SavedFileMeta = {
  path: string;
  size: number;
  modified_at: number;
  book_id?: string;
};

export type MemoryFileMeta = {
  path: string;
  size: number;
  version?: string;
  modified_at?: string;
};

export type BookNode = {
  book_id: string;
  book_title: string;
  weread_docs: WereadDocMeta[];
  saved_files: SavedFileMeta[];
};
