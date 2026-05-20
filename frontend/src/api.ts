import type {
  ChatThreadSummary,
  ChatTimelineItem,
  ChatTurnResponse,
  StreamEvent,
  WeReadArtifact,
  WeReadBinding,
  WeReadQrLoginSession,
  WereadDocMeta,
  SavedFileMeta,
  MemoryFileMeta,
} from "./types";

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL?.replace(/\/$/, "") ?? "";

type LoginResponse = {
  access_token: string;
  token_type: string;
  expires_at: string;
};

type SessionResponse = {
  session_id: string;
  name: string;
  token: {
    access_token: string;
    token_type: string;
    expires_at: string;
  };
};

function buildUrl(path: string): string {
  return `${API_BASE_URL}/api/v1${path}`;
}

async function parseJsonResponse<T>(response: Response): Promise<T> {
  if (!response.ok) {
    const detail = await response.text();
    throw new Error(detail || `${response.status} ${response.statusText}`);
  }
  return (await response.json()) as T;
}

export async function register(email: string, password: string): Promise<string> {
  const response = await fetch(buildUrl("/auth/register"), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ email, password }),
  });
  const payload = await parseJsonResponse<{ token: { access_token: string } }>(response);
  return payload.token.access_token;
}

export async function login(email: string, password: string): Promise<string> {
  const form = new URLSearchParams();
  form.set("username", email);
  form.set("password", password);
  form.set("grant_type", "password");

  const response = await fetch(buildUrl("/auth/login"), {
    method: "POST",
    headers: { "Content-Type": "application/x-www-form-urlencoded" },
    body: form.toString(),
  });
  const payload = await parseJsonResponse<LoginResponse>(response);
  return payload.access_token;
}

export async function createAuthSession(userToken: string): Promise<SessionResponse> {
  const response = await fetch(buildUrl("/auth/session"), {
    method: "POST",
    headers: {
      Authorization: `Bearer ${userToken}`,
    },
  });
  return parseJsonResponse<SessionResponse>(response);
}

export async function listThreads(sessionToken: string): Promise<ChatThreadSummary[]> {
  const response = await fetch(buildUrl("/chatbot/threads"), {
    headers: { Authorization: `Bearer ${sessionToken}` },
  });
  const payload = await parseJsonResponse<{ threads: ChatThreadSummary[] }>(response);
  return payload.threads;
}

export async function createThread(sessionToken: string, title = ""): Promise<ChatThreadSummary> {
  const response = await fetch(buildUrl("/chatbot/threads"), {
    method: "POST",
    headers: {
      Authorization: `Bearer ${sessionToken}`,
      "Content-Type": "application/json",
    },
    body: JSON.stringify({ title }),
  });
  return parseJsonResponse<ChatThreadSummary>(response);
}

export async function renameThread(
  sessionToken: string,
  threadId: string,
  title: string,
): Promise<ChatThreadSummary> {
  const response = await fetch(buildUrl(`/chatbot/threads/${threadId}`), {
    method: "PATCH",
    headers: {
      Authorization: `Bearer ${sessionToken}`,
      "Content-Type": "application/json",
    },
    body: JSON.stringify({ title }),
  });
  return parseJsonResponse<ChatThreadSummary>(response);
}

export async function deleteThread(sessionToken: string, threadId: string): Promise<void> {
  const response = await fetch(buildUrl(`/chatbot/threads/${threadId}`), {
    method: "DELETE",
    headers: { Authorization: `Bearer ${sessionToken}` },
  });
  await parseJsonResponse<{ message: string }>(response);
}

export async function listThreadItems(sessionToken: string, threadId: string): Promise<ChatTimelineItem[]> {
  const response = await fetch(buildUrl(`/chatbot/threads/${threadId}/items`), {
    headers: { Authorization: `Bearer ${sessionToken}` },
  });
  const payload = await parseJsonResponse<{ items: ChatTimelineItem[] }>(response);
  return payload.items;
}

export async function listThreadArtifacts(sessionToken: string, threadId: string): Promise<WeReadArtifact[]> {
  const response = await fetch(buildUrl(`/chatbot/threads/${threadId}/artifacts`), {
    headers: { Authorization: `Bearer ${sessionToken}` },
  });
  const payload = await parseJsonResponse<{ artifacts: WeReadArtifact[] }>(response);
  return payload.artifacts;
}

export async function sendChat(sessionToken: string, threadId: string, message: string): Promise<ChatTurnResponse> {
  const response = await fetch(buildUrl("/chatbot/chat"), {
    method: "POST",
    headers: {
      Authorization: `Bearer ${sessionToken}`,
      "Content-Type": "application/json",
    },
    body: JSON.stringify({ message, thread_id: threadId }),
  });
  return parseJsonResponse<ChatTurnResponse>(response);
}

export async function streamChat(
  sessionToken: string,
  threadId: string,
  message: string,
  onEvent: (event: StreamEvent) => void,
): Promise<void> {
  const response = await fetch(buildUrl("/chatbot/chat/stream"), {
    method: "POST",
    headers: {
      Authorization: `Bearer ${sessionToken}`,
      "Content-Type": "application/json",
      Accept: "text/event-stream",
    },
    body: JSON.stringify({ message, thread_id: threadId }),
  });

  if (!response.ok || !response.body) {
    const detail = await response.text();
    throw new Error(detail || `${response.status} ${response.statusText}`);
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder("utf-8");
  let buffer = "";

  const emitFrame = (frame: string) => {
    const lines = frame.split("\n");
    let eventType = "message";
    const dataLines: string[] = [];
    for (const line of lines) {
      if (line.startsWith("event:")) {
        eventType = line.slice(6).trim();
      } else if (line.startsWith("data:")) {
        dataLines.push(line.slice(5).trim());
      }
    }

    if (!dataLines.length) {
      return;
    }

    const rawData = dataLines.join("\n");
    const parsed = rawData ? JSON.parse(rawData) : {};
    onEvent({ type: eventType as StreamEvent["type"], data: parsed } as StreamEvent);
  };

  while (true) {
    const { done, value } = await reader.read();
    if (done) {
      break;
    }

    buffer += decoder.decode(value, { stream: true });
    const frames = buffer.split("\n\n");
    buffer = frames.pop() ?? "";
    for (const frame of frames) {
      if (frame.trim()) {
        emitFrame(frame);
      }
    }
  }

  if (buffer.trim()) {
    emitFrame(buffer);
  }
}

export async function getWeReadBinding(sessionToken: string): Promise<WeReadBinding> {
  const response = await fetch(buildUrl("/weread/binding"), {
    headers: { Authorization: `Bearer ${sessionToken}` },
  });
  return parseJsonResponse<WeReadBinding>(response);
}

export async function validateWeReadBinding(sessionToken: string): Promise<WeReadBinding> {
  const response = await fetch(buildUrl("/weread/binding/validate"), {
    method: "POST",
    headers: { Authorization: `Bearer ${sessionToken}` },
  });
  return parseJsonResponse<WeReadBinding>(response);
}

export async function startWeReadQrLoginSession(sessionToken: string): Promise<WeReadQrLoginSession> {
  const response = await fetch(buildUrl("/weread/binding/qr/session"), {
    method: "POST",
    headers: { Authorization: `Bearer ${sessionToken}` },
  });
  return parseJsonResponse<WeReadQrLoginSession>(response);
}

export async function getWeReadQrLoginSession(
  sessionToken: string,
  loginSessionId: string,
): Promise<WeReadQrLoginSession> {
  const response = await fetch(buildUrl(`/weread/binding/qr/session/${loginSessionId}`), {
    headers: { Authorization: `Bearer ${sessionToken}` },
  });
  return parseJsonResponse<WeReadQrLoginSession>(response);
}

export async function cancelWeReadQrLoginSession(
  sessionToken: string,
  loginSessionId: string,
): Promise<void> {
  const response = await fetch(buildUrl(`/weread/binding/qr/session/${loginSessionId}`), {
    method: "DELETE",
    headers: { Authorization: `Bearer ${sessionToken}` },
  });
  await parseJsonResponse<{ success: boolean; message: string }>(response);
}

export async function clearWeReadBinding(sessionToken: string): Promise<void> {
  const response = await fetch(buildUrl("/weread/binding"), {
    method: "DELETE",
    headers: { Authorization: `Bearer ${sessionToken}` },
  });
  await parseJsonResponse<{ success: boolean; message: string }>(response);
}

export function getWeReadDocumentUrl(docId: string, download = false): string {
  const suffix = download ? "?download=true" : "";
  return buildUrl(`/weread/documents/${encodeURIComponent(docId)}${suffix}`);
}

export async function getWeReadDocumentPreview(sessionToken: string, docId: string): Promise<string> {
  const response = await fetch(getWeReadDocumentUrl(docId), {
    headers: { Authorization: `Bearer ${sessionToken}` },
  });
  if (!response.ok) {
    const detail = await response.text();
    throw new Error(detail || `${response.status} ${response.statusText}`);
  }
  return response.text();
}

export async function getWeReadDocumentBlob(
  sessionToken: string,
  docId: string,
  download = false,
): Promise<Blob> {
  const response = await fetch(getWeReadDocumentUrl(docId, download), {
    headers: { Authorization: `Bearer ${sessionToken}` },
  });
  if (!response.ok) {
    const detail = await response.text();
    throw new Error(detail || `${response.status} ${response.statusText}`);
  }
  return response.blob();
}

export async function listWereadBookDocuments(
  sessionToken: string,
  bookId: string,
): Promise<WereadDocMeta[]> {
  const response = await fetch(buildUrl(`/weread/books/${encodeURIComponent(bookId)}/documents`), {
    headers: { Authorization: `Bearer ${sessionToken}` },
  });
  return parseJsonResponse<WereadDocMeta[]>(response);
}

export type UserWereadBook = {
  book_id: string;
  book_title: string;
  author: string;
  documents: WereadDocMeta[];
};

export async function listUserWereadBooks(sessionToken: string): Promise<UserWereadBook[]> {
  const response = await fetch(buildUrl("/weread/user/books"), {
    headers: { Authorization: `Bearer ${sessionToken}` },
  });
  return parseJsonResponse<UserWereadBook[]>(response);
}

export async function listSavedFiles(sessionToken: string): Promise<SavedFileMeta[]> {
  const response = await fetch(buildUrl("/saved-content"), {
    headers: { Authorization: `Bearer ${sessionToken}` },
  });
  return parseJsonResponse<SavedFileMeta[]>(response);
}

function encodePathSegments(filePath: string): string {
  return filePath.split("/").map(encodeURIComponent).join("/");
}

export function getSavedContentUrl(filePath: string): string {
  return buildUrl(`/saved-content/${encodePathSegments(filePath)}`);
}

export async function readSavedFile(sessionToken: string, filePath: string): Promise<string> {
  const response = await fetch(getSavedContentUrl(filePath), {
    headers: { Authorization: `Bearer ${sessionToken}` },
  });
  if (!response.ok) {
    const detail = await response.text();
    throw new Error(detail || `${response.status} ${response.statusText}`);
  }
  return response.text();
}

export async function writeSavedFile(
  sessionToken: string,
  filePath: string,
  content: string,
): Promise<void> {
  const response = await fetch(getSavedContentUrl(filePath), {
    method: "PUT",
    headers: { Authorization: `Bearer ${sessionToken}` },
    body: content,
  });
  if (!response.ok) {
    const detail = await response.text();
    throw new Error(detail || `${response.status} ${response.statusText}`);
  }
}

export function getMemoryFileUrl(filePath: string): string {
  return buildUrl(`/memory/files/${encodePathSegments(filePath)}`);
}

export async function listMemoryFiles(sessionToken: string): Promise<MemoryFileMeta[]> {
  const response = await fetch(buildUrl("/memory/files"), {
    headers: { Authorization: `Bearer ${sessionToken}` },
  });
  return parseJsonResponse<MemoryFileMeta[]>(response);
}

export async function readMemoryFile(sessionToken: string, filePath: string): Promise<string> {
  const response = await fetch(getMemoryFileUrl(filePath), {
    headers: { Authorization: `Bearer ${sessionToken}` },
  });
  if (!response.ok) {
    const detail = await response.text();
    throw new Error(detail || `${response.status} ${response.statusText}`);
  }
  const data = await response.json();
  return data.content ?? "";
}

export async function writeMemoryFile(
  sessionToken: string,
  filePath: string,
  content: string,
): Promise<void> {
  const response = await fetch(getMemoryFileUrl(filePath), {
    method: "PUT",
    headers: {
      Authorization: `Bearer ${sessionToken}`,
      "Content-Type": "application/json",
    },
    body: JSON.stringify({ content, force: true }),
  });
  if (!response.ok) {
    const detail = await response.text();
    throw new Error(detail || `${response.status} ${response.statusText}`);
  }
}

export async function rebuildMemoryIndex(sessionToken: string): Promise<void> {
  const response = await fetch(buildUrl("/memory/rebuild-index"), {
    method: "POST",
    headers: { Authorization: `Bearer ${sessionToken}` },
  });
  if (!response.ok) {
    const detail = await response.text();
    throw new Error(detail || `${response.status} ${response.statusText}`);
  }
}

export type WeixinLoginResponse = {
  qrcode_id: string;
  qrcode_img_content: string;
};

export type WeixinLoginStatus = {
  status: string;
  message: string;
};

export type ChannelStatus = {
  name: string;
  running: boolean;
  has_token: boolean;
  user_bound: boolean;
};

export async function startWeixinLogin(sessionToken: string): Promise<WeixinLoginResponse> {
  const response = await fetch(buildUrl("/channels/weixin/qrcode"), {
    method: "POST",
    headers: { Authorization: `Bearer ${sessionToken}` },
  });
  return parseJsonResponse<WeixinLoginResponse>(response);
}

export async function checkWeixinLoginStatus(
  sessionToken: string,
  threadId = "",
  sessionId = "",
): Promise<WeixinLoginStatus> {
  const params = new URLSearchParams();
  if (threadId) params.set("thread_id", threadId);
  if (sessionId) params.set("session_id", sessionId);
  const query = params.toString() ? `?${params.toString()}` : "";
  const response = await fetch(buildUrl(`/channels/weixin/qrcode/status${query}`), {
    headers: { Authorization: `Bearer ${sessionToken}` },
  });
  return parseJsonResponse<WeixinLoginStatus>(response);
}

export async function getChannelStatus(sessionToken: string): Promise<ChannelStatus[]> {
  const response = await fetch(buildUrl("/channels/status"), {
    headers: { Authorization: `Bearer ${sessionToken}` },
  });
  return parseJsonResponse<ChannelStatus[]>(response);
}

export async function startWeixinChannel(sessionToken: string): Promise<{ status: string }> {
  const response = await fetch(buildUrl("/channels/weixin/start"), {
    method: "POST",
    headers: { Authorization: `Bearer ${sessionToken}` },
  });
  return parseJsonResponse<{ status: string }>(response);
}

export async function stopWeixinChannel(sessionToken: string): Promise<{ status: string }> {
  const response = await fetch(buildUrl("/channels/weixin/stop"), {
    method: "POST",
    headers: { Authorization: `Bearer ${sessionToken}` },
  });
  return parseJsonResponse<{ status: string }>(response);
}
