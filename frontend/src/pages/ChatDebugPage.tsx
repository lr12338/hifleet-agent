import { useEffect, useMemo, useRef, useState } from "react";
import { Button, Card, Divider, Form, Input, Modal, Select, Space, Tag, Tooltip, Typography, Upload, type UploadProps, message } from "antd";
import type { UploadFile } from "antd/es/upload/interface";
import { useNavigate, useSearchParams } from "react-router-dom";

import {
  deleteChatDebugSession,
  fetchChatDebugSessions,
  fetchLlmConfig,
  saveChatDebugSession,
  type StreamRunEvent,
  streamTestRun,
  uploadAdminAttachment
} from "../api/client";
import { JsonViewer } from "../components/common/JsonViewer";
import { StatusTag } from "../components/common/StatusTag";
import { ARK_MODEL_OPTIONS, AUTO_ROUTE_MODEL_OPTION, modelSupportsAutoThinking } from "../config/arkModels";
import { useAdminShell } from "../layouts/AdminShell";
import "./ChatDebugPage.css";

type DebugEventType = "message_start" | "thinking" | "tool_request" | "tool_response" | "answer" | "message_end" | "upload" | "raw";
type PanelType = "chat" | "backend" | "api" | "request";

interface DebugEvent {
  id: string;
  type: DebugEventType;
  title: string;
  payload: unknown;
  timestamp: string;
  raw?: string;
}

interface ThinkingLine {
  id: string;
  text: string;
}

interface ToolTrace {
  id: string;
  name: string;
  request?: unknown;
  response?: unknown;
}

interface UserMessage {
  id: string;
  text: string;
  timestamp: string;
}

interface AssistantMessage {
  id: string;
  timestamp: string;
  status: "streaming" | "done";
  thinking: ThinkingLine[];
  tools: ToolTrace[];
  answer: string;
}

interface TimelineEvent {
  id: string;
  text: string;
  timestamp: string;
  turnId?: string;
}

interface SessionMeta {
  model: string;
  thinking: "enabled" | "disabled" | "auto";
  session_id: string;
  user_id: string;
  source_channel: string;
  agent_profile: string;
}

interface ChatSession {
  id: string;
  title: string;
  status: "running" | "ended";
  createdAt: string;
  meta: SessionMeta;
  userMessages: UserMessage[];
  assistantMessages: AssistantMessage[];
  timeline: TimelineEvent[];
  debugEvents: DebugEvent[];
  lastRequest?: unknown;
  lastResponse?: unknown;
  attachment?: {
    name: string;
    url: string;
    key: string;
  };
}

interface ChatTurn {
  id: string;
  user?: UserMessage;
  assistant?: AssistantMessage;
  timeline: TimelineEvent[];
}

function getEventText(data: unknown): string {
  if (typeof data === "string") return data;
  if (!data || typeof data !== "object") return "";
  const candidate = data as Record<string, unknown>;
  const contentObj =
    candidate.content && typeof candidate.content === "object"
      ? (candidate.content as Record<string, unknown>)
      : undefined;
  const possibleValues: unknown[] = [
    candidate.text,
    candidate.delta,
    candidate.output_text,
    candidate.answer,
    contentObj?.answer,
    contentObj?.text,
    contentObj?.content
  ];
  for (const value of possibleValues) {
    if (typeof value === "string" && value.trim()) return value;
  }
  return "";
}

function nowLabel(date = new Date()): string {
  const month = String(date.getMonth() + 1).padStart(2, "0");
  const day = String(date.getDate()).padStart(2, "0");
  const hour = String(date.getHours()).padStart(2, "0");
  const minute = String(date.getMinutes()).padStart(2, "0");
  return `${month}/${day} ${hour}:${minute}`;
}

function createSession(seed = 1): ChatSession {
  const createdAt = nowLabel();
  return {
    id: `session-${Date.now()}-${seed}`,
    title: "新对话",
    status: "ended",
    createdAt,
    meta: {
      model: AUTO_ROUTE_MODEL_OPTION.value,
      thinking: "enabled",
      session_id: `admin_chat_debug_${Date.now()}`,
      user_id: "admin_debug_user",
      source_channel: "admin_panel",
      agent_profile: "employee_assistant"
    },
    userMessages: [],
    assistantMessages: [],
    timeline: [],
    debugEvents: []
  };
}

function summarizeTitle(text: string): string {
  const trimmed = text.trim();
  return trimmed ? trimmed.slice(0, 10) : "新对话";
}

function hasSessionContent(session: ChatSession): boolean {
  return Boolean(
    session.userMessages.length ||
    session.assistantMessages.length ||
    session.debugEvents.length ||
    session.timeline.length ||
    session.attachment
  );
}

function normalizeStoredSession(payload: unknown, seed = 1): ChatSession {
  const fallback = createSession(seed);
  if (!payload || typeof payload !== "object") {
    return fallback;
  }
  const session = payload as Partial<ChatSession> & { meta?: Partial<SessionMeta> };
  return {
    ...fallback,
    ...session,
    id: typeof session.id === "string" && session.id ? session.id : fallback.id,
    title: typeof session.title === "string" && session.title ? session.title : fallback.title,
    status: session.status === "running" ? "running" : "ended",
    createdAt: typeof session.createdAt === "string" && session.createdAt ? session.createdAt : fallback.createdAt,
    meta: {
      ...fallback.meta,
      ...(session.meta || {})
    },
    userMessages: Array.isArray(session.userMessages) ? session.userMessages : [],
    assistantMessages: Array.isArray(session.assistantMessages) ? session.assistantMessages : [],
    timeline: Array.isArray(session.timeline) ? session.timeline : [],
    debugEvents: Array.isArray(session.debugEvents) ? session.debugEvents : [],
    lastRequest: session.lastRequest,
    lastResponse: session.lastResponse,
    attachment: session.attachment
  };
}

function safeFormat(value: unknown): string {
  try {
    return JSON.stringify(value, null, 2);
  } catch {
    return String(value);
  }
}

function getAudioFormat(filename: string): string | undefined {
  if (!filename.includes(".")) return undefined;
  return filename.split(".").pop()?.toLowerCase();
}

function buildAttachmentMessage(file: File, url: string): Record<string, unknown> {
  const mime = (file.type || "").toLowerCase();
  if (mime.startsWith("image/")) {
    return { type: "image_url", image_url: { url } };
  }
  if (mime.startsWith("audio/")) {
    return {
      type: "input_audio",
      input_audio: {
        url,
        format: getAudioFormat(file.name)
      }
    };
  }
  if (mime.startsWith("video/")) {
    return { type: "video_url", video_url: { url } };
  }
  throw new Error("仅支持图片、音频、视频附件");
}

function renderStatus(status: ChatSession["status"]) {
  return status === "running" ? <span className="chat-debug-running">运行中</span> : <span className="chat-debug-ended">已结束</span>;
}

function normalizeThinkingText(text: string): ThinkingLine[] {
  return text
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter(Boolean)
    .map((line, index) => ({
      id: `${Date.now()}-${index}`,
      text: line.replace(/^[*-]\s*/, "")
    }));
}

function eventTitleMap(event: string): string {
  const mapping: Record<string, string> = {
    message_start: "消息开始",
    thinking: "思考过程",
    tool_request: "工具请求",
    tool_response: "工具响应",
    answer: "回答输出",
    message_end: "消息结束",
    upload: "附件上传"
  };
  return mapping[event] || event;
}

function resolveEventType(event: string, data: unknown): DebugEventType {
  if (event && event !== "message") {
    return event as DebugEventType;
  }
  if (data && typeof data === "object") {
    const candidate = String((data as Record<string, unknown>).type || "").trim();
    if (
      candidate === "message_start" ||
      candidate === "thinking" ||
      candidate === "tool_request" ||
      candidate === "tool_response" ||
      candidate === "answer" ||
      candidate === "message_end"
    ) {
      return candidate as DebugEventType;
    }
  }
  return "raw";
}

export function ChatDebugPage() {
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const { environmentLabel } = useAdminShell();
  const [form] = Form.useForm();
  const [advancedForm] = Form.useForm();
  const [sending, setSending] = useState(false);
  const [attachmentFiles, setAttachmentFiles] = useState<UploadFile[]>([]);
  const [sessions, setSessions] = useState<ChatSession[]>([createSession()]);
  const [activeSessionId, setActiveSessionId] = useState<string>("");
  const [activePanel, setActivePanel] = useState<PanelType>("chat");
  const [advancedOpen, setAdvancedOpen] = useState(false);
  const [sessionKeyword, setSessionKeyword] = useState("");
  const [historyReady, setHistoryReady] = useState(false);
  const [defaultTextModel, setDefaultTextModel] = useState(AUTO_ROUTE_MODEL_OPTION.value);
  const abortRef = useRef<AbortController | null>(null);
  const streamContainerRef = useRef<HTMLDivElement | null>(null);
  const saveTimerRef = useRef<number | null>(null);
  const lastSavedSnapshotRef = useRef<string>("");
  const deletedSessionIdsRef = useRef<string[]>([]);

  useEffect(() => {
    let cancelled = false;
    const loadLlmDefaults = async () => {
      try {
        const response = await fetchLlmConfig();
        if (cancelled) return;
        setDefaultTextModel(response.text_model || AUTO_ROUTE_MODEL_OPTION.value);
      } catch {
        if (!cancelled) {
          setDefaultTextModel(AUTO_ROUTE_MODEL_OPTION.value);
        }
      }
    };
    void loadLlmDefaults();
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    let cancelled = false;
    const loadHistory = async () => {
      try {
        const response = await fetchChatDebugSessions(20);
        if (cancelled) return;
        if (response.items.length) {
          const restored = response.items.map((item, index) => normalizeStoredSession(item.payload, index + 1));
          setSessions(restored);
          setActiveSessionId(restored[0]?.id || "");
        }
      } catch (error) {
        const msg = error instanceof Error ? error.message : "加载历史会话失败";
        if (msg !== "UNAUTHORIZED") {
          message.warning("历史会话加载失败，已使用本地空白会话");
        }
      } finally {
        if (!cancelled) {
          setHistoryReady(true);
        }
      }
    };
    void loadHistory();
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    if (!activeSessionId && sessions[0]) {
      setActiveSessionId(sessions[0].id);
    }
  }, [activeSessionId, sessions]);

  const activeSession = useMemo(
    () => sessions.find((item) => item.id === activeSessionId) || sessions[0],
    [activeSessionId, sessions]
  );

  useEffect(() => {
    if (!activeSession) return;
    form.setFieldsValue({
      text: "",
      model: activeSession.meta.model,
      thinkingMode: activeSession.meta.thinking
    });
    advancedForm.setFieldsValue({
      session_id: activeSession.meta.session_id,
      user_id: activeSession.meta.user_id,
      source_channel: activeSession.meta.source_channel,
      agent_profile: activeSession.meta.agent_profile
    });
  }, [activeSession, advancedForm, form]);

  useEffect(() => {
    if (!streamContainerRef.current || !activeSession) return;
    streamContainerRef.current.scrollTop = streamContainerRef.current.scrollHeight;
  }, [activeSession?.assistantMessages, activeSession?.timeline, activeSession?.userMessages]);

  useEffect(() => {
    if (!historyReady || !activeSession) return;
    if (!hasSessionContent(activeSession)) return;
    const persistPayload = {
      session_key: activeSession.id,
      title: activeSession.title,
      status: activeSession.status,
      meta_session_id: activeSession.meta.session_id,
      user_id: activeSession.meta.user_id,
      source_channel: activeSession.meta.source_channel,
      agent_profile: activeSession.meta.agent_profile,
      model: activeSession.meta.model,
      payload: activeSession
    };
    const snapshot = safeFormat(persistPayload);
    if (snapshot === lastSavedSnapshotRef.current) {
      return;
    }
    if (saveTimerRef.current) {
      window.clearTimeout(saveTimerRef.current);
    }
    saveTimerRef.current = window.setTimeout(() => {
      void saveChatDebugSession(activeSession.id, persistPayload)
        .then(() => {
          lastSavedSnapshotRef.current = snapshot;
        })
        .catch((error) => {
          const msg = error instanceof Error ? error.message : "保存会话失败";
          if (msg !== "UNAUTHORIZED") {
            message.warning("当前对话保存到数据库失败");
          }
        });
    }, 800);
    return () => {
      if (saveTimerRef.current) {
        window.clearTimeout(saveTimerRef.current);
      }
    };
  }, [activeSession, historyReady]);

  useEffect(() => {
    if (!historyReady || !deletedSessionIdsRef.current.length) return;
    const deleted = [...deletedSessionIdsRef.current];
    deletedSessionIdsRef.current = [];
    deleted.forEach((sessionId) => {
      void deleteChatDebugSession(sessionId).catch(() => {
        message.warning("删除数据库中的历史会话失败");
      });
    });
  }, [historyReady, sessions]);

  useEffect(() => {
    if (!historyReady) return;
    const linkedSessionId = searchParams.get("session_id");
    if (!linkedSessionId) return;
    const existing = sessions.find((item) => item.meta.session_id === linkedSessionId);
    if (existing) {
      setActiveSessionId(existing.id);
      return;
    }
    const next = createSession(sessions.length + 1);
    next.meta.session_id = linkedSessionId;
    next.title = `调试 ${linkedSessionId.slice(0, 12)}`;
    setSessions((prev) => [next, ...prev]);
    setActiveSessionId(next.id);
  }, [historyReady, searchParams, sessions]);

  const eventStats = useMemo(() => {
    if (!activeSession) {
      return { thought: 0, tool: 0, stream: 0 };
    }
    return {
      thought: activeSession.debugEvents.filter((item) => item.type === "thinking").length,
      tool: activeSession.debugEvents.filter((item) => item.type === "tool_request" || item.type === "tool_response" || item.type === "upload").length,
      stream: activeSession.debugEvents.filter((item) => item.type === "answer").length
    };
  }, [activeSession]);

  const filteredSessions = useMemo(
    () =>
      sessions.filter((item) => {
        if (!sessionKeyword.trim()) return true;
        const keyword = sessionKeyword.trim().toLowerCase();
        return (
          item.title.toLowerCase().includes(keyword) ||
          item.meta.session_id.toLowerCase().includes(keyword) ||
          item.meta.user_id.toLowerCase().includes(keyword)
        );
      }),
    [sessionKeyword, sessions]
  );

  const chatTurns = useMemo<ChatTurn[]>(() => {
    if (!activeSession) return [];
    const turnCount = Math.max(activeSession.userMessages.length, activeSession.assistantMessages.length);
    return Array.from({ length: turnCount }, (_, index) => {
      const user = activeSession.userMessages[index];
      const assistant = activeSession.assistantMessages[index];
      const turnId = assistant?.id;
      const timeline = turnId
        ? activeSession.timeline.filter((event) => event.turnId === turnId)
        : [];
      return {
        id: assistant?.id || user?.id || `turn-${index}`,
        user,
        assistant,
        timeline
      };
    });
  }, [activeSession]);

  const textValue = Form.useWatch("text", form) || "";
  const selectedModel = Form.useWatch("model", form) || AUTO_ROUTE_MODEL_OPTION.value;
  const selectedThinkingMode = Form.useWatch("thinkingMode", form) || "enabled";
  const supportsAutoThinking = selectedModel === AUTO_ROUTE_MODEL_OPTION.value ? true : modelSupportsAutoThinking(selectedModel);

  useEffect(() => {
    if (!supportsAutoThinking && selectedThinkingMode === "auto") {
      form.setFieldValue("thinkingMode", "enabled");
    }
  }, [form, selectedThinkingMode, supportsAutoThinking]);

  const stopStream = () => {
    abortRef.current?.abort();
    abortRef.current = null;
    setSending(false);
    if (!activeSession) return;
    setSessions((prev) =>
      prev.map((session) => {
        if (session.id !== activeSession.id) return session;
        return {
          ...session,
          status: "ended",
          assistantMessages: session.assistantMessages.map((item) => (item.status === "streaming" ? { ...item, status: "done" } : item))
        };
      })
    );
  };

  const buildMultimodalContent = async (values: Record<string, unknown>, sessionId: string, turnId: string) => {
    const text = String(values.text || "").trim();
    const content: Array<Record<string, unknown>> = [];

    if (attachmentFiles[0]?.originFileObj) {
      const localFile = attachmentFiles[0].originFileObj as File;
      const uploaded = await uploadAdminAttachment(localFile);
      const multimodalPart = buildAttachmentMessage(localFile, uploaded.url);
      content.push(multimodalPart);
      const timestamp = nowLabel();
      setSessions((prev) =>
        prev.map((session) =>
          session.id !== sessionId
            ? session
            : {
                ...session,
                attachment: {
                  name: localFile.name,
                  key: uploaded.key,
                  url: uploaded.url
                },
                debugEvents: [
                  ...session.debugEvents,
                  {
                    id: `${Date.now()}-upload`,
                    type: "upload",
                    title: "附件上传完成",
                    payload: {
                      filename: localFile.name,
                      key: uploaded.key,
                      url: uploaded.url,
                      size: uploaded.size,
                      content_type: uploaded.content_type
                    },
                    timestamp
                  }
                ],
                timeline: [
                  ...session.timeline,
                  {
                    id: `${Date.now()}-inline-upload`,
                    text: `${timestamp} 上传了附件：${localFile.name}`,
                    timestamp,
                    turnId
                  }
                ]
              }
        )
      );
    }

    if (text) {
      content.push({ type: "text", text });
    }

    if (!content.length) {
      throw new Error("请至少输入文本或提供一个多模态输入");
    }

    if (content.length === 1 && content[0].type === "text") {
      return content[0].text as string;
    }
    return content;
  };

  const sendMessage = async (values: Record<string, unknown>) => {
    if (!activeSession) return;
    setSending(true);
    abortRef.current = new AbortController();
    const advancedValues = advancedForm.getFieldsValue();
    const timestamp = nowLabel();
    const userText = String(values.text || "").trim();
    const assistantId = `assistant-${Date.now()}`;
    const nextMeta: SessionMeta = {
      model: String(values.model || activeSession.meta.model),
      thinking: String(values.thinkingMode || activeSession.meta.thinking) as SessionMeta["thinking"],
      session_id: String(advancedValues.session_id || activeSession.meta.session_id),
      user_id: String(advancedValues.user_id || activeSession.meta.user_id),
      source_channel: String(advancedValues.source_channel || activeSession.meta.source_channel),
      agent_profile: String(advancedValues.agent_profile || activeSession.meta.agent_profile)
    };

    setSessions((prev) =>
      prev.map((session) =>
        session.id !== activeSession.id
          ? session
          : {
              ...session,
              title: summarizeTitle(userText || session.title),
              status: "running",
              meta: nextMeta,
              userMessages: [
                ...session.userMessages,
                {
                  id: `user-${Date.now()}`,
                  text: userText || (attachmentFiles[0]?.name ?? "附件消息"),
                  timestamp
                }
              ],
              assistantMessages: [
                ...session.assistantMessages,
                {
                  id: assistantId,
                  timestamp,
                  status: "streaming",
                  thinking: [],
                  tools: [],
                  answer: ""
                }
              ]
            }
      )
    );

    try {
      const content = await buildMultimodalContent(values, activeSession.id, assistantId);
      const payload = {
        messages: [
          {
            role: "user",
            content
          }
        ],
        session_id: nextMeta.session_id,
        user_id: nextMeta.user_id,
        source_channel: nextMeta.source_channel,
        agent_profile: nextMeta.agent_profile,
        thinking: nextMeta.thinking,
        ...(nextMeta.model && nextMeta.model !== AUTO_ROUTE_MODEL_OPTION.value ? { model: nextMeta.model } : {})
      };
      setSessions((prev) =>
        prev.map((session) =>
          session.id !== activeSession.id
            ? session
            : {
                ...session,
                lastRequest: payload
              }
        )
      );

      await streamTestRun(
        payload,
        {
          onEvent: ({ event, data, raw }: StreamRunEvent) => {
            const normalizedEvent = resolveEventType(event, data);
            const chunkText = getEventText(data);
            const eventTimestamp = nowLabel();
            setSessions((prev) =>
              prev.map((session) => {
                if (session.id !== activeSession.id) return session;
                const debugEvent: DebugEvent = {
                  id: `${Date.now()}-${session.debugEvents.length}`,
                  type: normalizedEvent,
                  title: eventTitleMap(normalizedEvent),
                  payload: data,
                  timestamp: eventTimestamp,
                  raw
                };
                const nextSession: ChatSession = {
                  ...session,
                  lastResponse: data,
                  debugEvents: [...session.debugEvents, debugEvent]
                };

                const dataObj = data && typeof data === "object" ? (data as Record<string, unknown>) : {};
                const contentObj =
                  dataObj.content && typeof dataObj.content === "object"
                    ? (dataObj.content as Record<string, unknown>)
                    : {};
                const thinkingText =
                  typeof dataObj.text === "string"
                    ? dataObj.text
                    : typeof dataObj.thinking === "string"
                      ? dataObj.thinking
                      : typeof contentObj.thinking === "string"
                        ? contentObj.thinking
                        : typeof contentObj.reasoning === "string"
                          ? contentObj.reasoning
                        : "";

                if (normalizedEvent === "thinking" && thinkingText) {
                  nextSession.assistantMessages = session.assistantMessages.map((item) =>
                    item.id === assistantId
                      ? {
                          ...item,
                          thinking: [...item.thinking, ...normalizeThinkingText(thinkingText)]
                        }
                      : item
                  );
                  return nextSession;
                }

                if (normalizedEvent === "tool_request") {
                  const toolPayload =
                    (contentObj.tool_request && typeof contentObj.tool_request === "object"
                      ? (contentObj.tool_request as Record<string, unknown>)
                      : dataObj) || {};
                  const toolName = String(toolPayload.tool_name || toolPayload.tool || toolPayload.name || "unknown_tool");
                  nextSession.assistantMessages = session.assistantMessages.map((item) =>
                    item.id === assistantId
                      ? {
                          ...item,
                          tools: [
                            ...item.tools,
                            {
                              id: `${Date.now()}-tool`,
                              name: toolName,
                              request: toolPayload.arguments || toolPayload.tool_args || toolPayload.args || toolPayload
                            }
                          ]
                        }
                      : item
                  );
                  nextSession.timeline = [
                    ...session.timeline,
                    {
                      id: `${Date.now()}-timeline-tool-request`,
                      text: `${eventTimestamp} 调用了工具：${toolName}`,
                      timestamp: eventTimestamp,
                      turnId: assistantId
                    }
                  ];
                  return nextSession;
                }

                if (normalizedEvent === "tool_response") {
                  const toolPayload =
                    (contentObj.tool_response && typeof contentObj.tool_response === "object"
                      ? (contentObj.tool_response as Record<string, unknown>)
                      : dataObj) || {};
                  const toolName = String(toolPayload.tool_name || toolPayload.tool || toolPayload.name || "unknown_tool");
                  nextSession.assistantMessages = session.assistantMessages.map((item) =>
                    item.id === assistantId
                      ? {
                          ...item,
                          tools: item.tools.map((tool, index) =>
                            index === item.tools.length - 1 && tool.name === toolName
                              ? { ...tool, response: toolPayload.result || toolPayload.output || toolPayload.tool_result || toolPayload }
                              : tool
                          )
                        }
                      : item
                  );
                  nextSession.timeline = [
                    ...session.timeline,
                    {
                      id: `${Date.now()}-timeline-tool-response`,
                      text: `${eventTimestamp} 工具返回：${toolName}`,
                      timestamp: eventTimestamp,
                      turnId: assistantId
                    }
                  ];
                  return nextSession;
                }

                if (normalizedEvent === "answer" || (normalizedEvent === "raw" && chunkText)) {
                  nextSession.assistantMessages = session.assistantMessages.map((item) =>
                    item.id === assistantId ? { ...item, answer: chunkText ? item.answer + chunkText : item.answer } : item
                  );
                  return nextSession;
                }

                if (normalizedEvent === "message_end") {
                  nextSession.status = "ended";
                  nextSession.assistantMessages = session.assistantMessages.map((item) =>
                    item.id === assistantId ? { ...item, status: "done" } : item
                  );
                  return nextSession;
                }

                return {
                  ...nextSession
                };
              })
            );
          },
          onDone: () => {
            setSending(false);
            abortRef.current = null;
            setAttachmentFiles([]);
            form.setFieldValue("text", "");
            setSessions((prev) =>
              prev.map((session) => {
                if (session.id !== activeSession.id) return session;
                return {
                  ...session,
                  status: "ended",
                  assistantMessages: session.assistantMessages.map((item) =>
                    item.id === assistantId ? { ...item, status: "done" } : item
                  )
                };
              })
            );
          }
        },
        abortRef.current.signal
      );
    } catch (error) {
      const msg = error instanceof Error ? error.message : "发送失败";
      if (msg !== "The user aborted a request.") {
        message.error(msg);
      }
      setSending(false);
      abortRef.current = null;
      setSessions((prev) =>
        prev.map((session) => {
          if (session.id !== activeSession.id) return session;
          return {
            ...session,
            status: "ended",
            assistantMessages: session.assistantMessages.map((item) =>
              item.id === assistantId
                ? {
                    ...item,
                    status: "done",
                    thinking: [...item.thinking, { id: `${Date.now()}-error`, text: `Error: ${msg}` }]
                  }
                : item
            )
          };
        })
      );
    }
  };

  const uploadProps: UploadProps = {
    maxCount: 1,
    fileList: attachmentFiles,
    beforeUpload: () => false,
    onChange: ({ fileList }) => setAttachmentFiles(fileList),
    accept: "image/*,audio/*,video/*"
  };

  const createNewSession = () => {
    const next = createSession(sessions.length + 1);
    setSessions((prev) => [next, ...prev]);
    setActiveSessionId(next.id);
    setAttachmentFiles([]);
    form.resetFields();
  };

  const deleteSession = (id: string) => {
    deletedSessionIdsRef.current.push(id);
    setSessions((prev) => {
      const filtered = prev.filter((item) => item.id !== id);
      if (!filtered.length) {
        const fallback = createSession(1);
        setActiveSessionId(fallback.id);
        return [fallback];
      }
      if (activeSessionId === id) {
        setActiveSessionId(filtered[0].id);
      }
      return filtered;
    });
  };

  const refreshCurrentSession = () => {
    if (!activeSession) return;
    advancedForm.setFieldsValue({
      session_id: activeSession.meta.session_id,
      user_id: activeSession.meta.user_id,
      source_channel: activeSession.meta.source_channel,
      agent_profile: activeSession.meta.agent_profile
    });
    form.setFieldsValue({
      model: activeSession.meta.model,
      thinkingMode: activeSession.meta.thinking
    });
    message.success("已刷新当前会话状态");
  };

  if (!activeSession) {
    return null;
  }

  const panelTitleMap: Record<PanelType, string> = {
    chat: "对话视图",
    backend: "Trace视图",
    api: "API视图",
    request: "原始日志视图"
  };
  const environmentTagColor = environmentLabel === "production" ? "blue" : environmentLabel === "staging" ? "gold" : "default";
  const getSelectPopupContainer = (triggerNode: HTMLElement) => triggerNode.parentElement ?? document.body;

  return (
    <div className="chat-debug-page">
      <div className="chat-debug-top-shell">
        <div className="chat-debug-toolbar">
          <div className="chat-debug-toolbar-main">
            <div className="chat-debug-toolbar-title">Chat Debug 工作台</div>
            <div className="chat-debug-toolbar-description">
              统一查看对话、Trace、API 与原始日志，支持会话持久化、附件上传、保存案例与跨页跳转。
            </div>
          </div>
          <div className="chat-debug-toolbar-meta">
            <Tag color={environmentTagColor}>环境：{environmentLabel}</Tag>
            <Tag>Agent：Hifleet 主 Agent</Tag>
            <Tag>Model：{activeSession.meta.model === AUTO_ROUTE_MODEL_OPTION.value ? `自动路由 / ${defaultTextModel}` : activeSession.meta.model}</Tag>
            <Tag>版本：admin-ui-v2</Tag>
          </div>
          <Space size={8} wrap className="chat-debug-toolbar-actions">
            <Button type="primary" className="chat-debug-primary-button" onClick={createNewSession}>
              新对话
            </Button>
            <Button
              className="chat-debug-secondary-button"
              onClick={() => {
                if (!activeSession) return;
                void saveChatDebugSession(activeSession.id, {
                  session_key: activeSession.id,
                  title: activeSession.title,
                  status: activeSession.status,
                  meta_session_id: activeSession.meta.session_id,
                  user_id: activeSession.meta.user_id,
                  source_channel: activeSession.meta.source_channel,
                  agent_profile: activeSession.meta.agent_profile,
                  model: activeSession.meta.model,
                  payload: activeSession
                }).then(() => message.success("当前调试案例已保存"));
              }}
            >
              保存案例
            </Button>
            <Button
              className="chat-debug-secondary-button"
              onClick={async () => {
                await navigator.clipboard.writeText(`${window.location.origin}/admin-ui/chat?session_id=${encodeURIComponent(activeSession.meta.session_id)}`);
                message.success("分享链接已复制");
              }}
            >
              分享链接
            </Button>
            <Button
              className="chat-debug-secondary-button"
              onClick={() => {
                const blob = new Blob([JSON.stringify(activeSession, null, 2)], { type: "application/json" });
                const url = window.URL.createObjectURL(blob);
                const anchor = document.createElement("a");
                anchor.href = url;
                anchor.download = `${activeSession.title || "chat-debug"}-${Date.now()}.json`;
                anchor.click();
                window.URL.revokeObjectURL(url);
              }}
            >
              导出记录
            </Button>
            <Button className="chat-debug-secondary-button" onClick={refreshCurrentSession}>
              刷新
            </Button>
          </Space>
        </div>

        <div className="chat-debug-toolbar-strip">
          <div className="chat-debug-tabbar">
            {([
              ["chat", "对话视图"],
              ["backend", "Trace视图"],
              ["api", "API视图"],
              ["request", "原始日志视图"]
            ] as Array<[PanelType, string]>).map(([key, label]) => (
              <button
                type="button"
                key={key}
                className={`chat-debug-tab ${activePanel === key ? "active" : ""}`}
                onClick={() => setActivePanel(key)}
              >
                {label}
              </button>
            ))}
          </div>
          <Space size={8} wrap>
            <Tag color="purple">思考 {eventStats.thought}</Tag>
            <Tag color="gold">工具 {eventStats.tool}</Tag>
            <Tag color="blue">流式 {eventStats.stream}</Tag>
            <Typography.Text type="secondary">所有会话已自动缓存到 Postgres，刷新后可恢复。</Typography.Text>
          </Space>
        </div>
      </div>

      <div className="chat-debug-main">
        <aside className="chat-debug-sidebar">
          <div className="chat-debug-sidebar-header">
            <div>
              <div className="chat-debug-sidebar-title">会话历史</div>
              <div className="chat-debug-sidebar-subtitle">最近 / 收藏 / 异常</div>
            </div>
          </div>

          <div className="chat-debug-sidebar-search">
            <Input.Search
              placeholder="搜索标题 / session_id / user_id"
              allowClear
              value={sessionKeyword}
              onChange={(event) => setSessionKeyword(event.target.value)}
            />
          </div>

          <div className="chat-debug-session-list">
            {filteredSessions.map((session) => (
              <div
                key={session.id}
                className={`chat-debug-session-card ${session.id === activeSession.id ? "active" : ""}`}
                onClick={() => setActiveSessionId(session.id)}
              >
                <div className="chat-debug-session-card-header">
                  <div className="chat-debug-session-title-wrap">
                    <div className="chat-debug-session-title">{session.title}</div>
                    <div className="chat-debug-session-model">{session.meta.model}</div>
                  </div>
                  <button
                    type="button"
                    className="chat-debug-session-delete"
                    onClick={(event) => {
                      event.stopPropagation();
                      deleteSession(session.id);
                    }}
                  >
                    删除
                  </button>
                </div>
                <div className="chat-debug-session-meta">
                  <div>{renderStatus(session.status)}</div>
                  <div className="chat-debug-session-meta-right">
                    <span>{session.createdAt}</span>
                    <span>
                      消息 {session.userMessages.length + session.assistantMessages.length} / 工具{" "}
                      {session.assistantMessages.reduce((sum, item) => sum + item.tools.length, 0)}
                    </span>
                  </div>
                </div>
              </div>
            ))}
          </div>
        </aside>

        <section className="chat-debug-content">
          <div className="chat-debug-content-header">
            <div>
              <div className="chat-debug-content-title">{activeSession.title}</div>
              <div className="chat-debug-content-subtitle">
                {activeSession.status === "running" ? "运行中" : "已结束"} | {activeSession.meta.model} | profile={activeSession.meta.agent_profile} |{" "}
                {activeSession.createdAt} | session={activeSession.meta.session_id}
              </div>
            </div>
            <Space size={8}>
              <Button className="chat-debug-secondary-button" onClick={() => navigate(`/sessions/${encodeURIComponent(activeSession.meta.session_id)}`)}>
                会话中心
              </Button>
              <Button className="chat-debug-secondary-button" onClick={() => navigate(`/logs?session_id=${encodeURIComponent(activeSession.meta.session_id)}`)}>
                请求日志
              </Button>
            </Space>
          </div>

          {activePanel === "chat" ? (
            <div className="chat-debug-stream-wrapper" ref={streamContainerRef}>
              {chatTurns.map((turn) => (
                <div key={turn.id} className="chat-debug-turn">
                  {turn.user ? (
                    <div className="chat-debug-bubble-row user">
                      <div className="chat-debug-bubble">
                        <div className="chat-debug-bubble-meta">
                          <span>你</span>
                          <span>{turn.user.timestamp}</span>
                        </div>
                        <div className="chat-debug-bubble-box">
                          <div className="chat-debug-bubble-text">{turn.user.text}</div>
                        </div>
                      </div>
                    </div>
                  ) : null}

                  {turn.assistant ? (
                    <>
                      <div className="chat-debug-bubble-row assistant">
                        <div className="chat-debug-bubble">
                          <div className="chat-debug-bubble-meta">
                            <span>助手</span>
                            <span>{turn.assistant.timestamp}</span>
                          </div>
                          <div className="chat-debug-bubble-box">
                            <details className="chat-debug-thinking" open>
                              <summary>▼ THINKING</summary>
                              <div className="chat-debug-thinking-content">
                                {turn.assistant.thinking.length ? (
                                  <ul>
                                    {turn.assistant.thinking.map((line) => (
                                      <li key={line.id}>{line.text}</li>
                                    ))}
                                  </ul>
                                ) : (
                                  <Typography.Text type="secondary">暂无思考过程</Typography.Text>
                                )}
                              </div>
                            </details>

                            {turn.assistant.tools.length ? (
                              <div className="chat-debug-tool-block">
                                <div className="chat-debug-tool-title">工具调用</div>
                                {turn.assistant.tools.map((tool) => (
                                  <div key={tool.id} className="chat-debug-tool-card">
                                    <div className="chat-debug-tool-name">{tool.name}</div>
                                    <div className="chat-debug-tool-section">
                                      <strong>请求参数</strong>
                                      <JsonViewer value={tool.request || {}} maxHeight={180} />
                                    </div>
                                    <div className="chat-debug-tool-section">
                                      <strong>返回结果</strong>
                                      <JsonViewer value={tool.response || {}} maxHeight={180} />
                                    </div>
                                  </div>
                                ))}
                              </div>
                            ) : null}

                            <div className="chat-debug-divider" />
                            <div className="chat-debug-bubble-text">{turn.assistant.answer || "生成中..."}</div>
                            {turn.assistant.status === "streaming" ? (
                              <div style={{ marginTop: 10, color: "#6b7280", fontSize: 12 }}>生成中...</div>
                            ) : null}
                          </div>
                        </div>
                      </div>

                      {turn.timeline.map((event) => (
                        <div key={event.id} className="chat-debug-inline-event">
                          {event.text}
                        </div>
                      ))}
                    </>
                  ) : null}
                </div>
              ))}
            </div>
          ) : (
            <div className="chat-debug-panel">
              {activePanel === "backend" ? (
                activeSession.debugEvents.length ? (
                  activeSession.debugEvents.map((event) => (
                    <div key={event.id} className="chat-debug-panel-card">
                      <div style={{ marginBottom: 8, display: "flex", justifyContent: "space-between" }}>
                        <strong>{event.title}</strong>
                        <span style={{ color: "#6b7280", fontSize: 12 }}>{event.timestamp}</span>
                      </div>
                      <JsonViewer value={event.raw || event.payload} maxHeight={260} />
                    </div>
                  ))
                ) : (
                  <Typography.Text type="secondary">暂无后端日志</Typography.Text>
                )
              ) : null}

              {activePanel === "api" ? (
                <>
                  <div className="chat-debug-panel-card">
                    <strong>最近请求</strong>
                    <Divider style={{ margin: "10px 0" }} />
                    <JsonViewer value={activeSession.lastRequest || {}} maxHeight={260} />
                  </div>
                  <div className="chat-debug-panel-card">
                    <strong>最近响应事件</strong>
                    <Divider style={{ margin: "10px 0" }} />
                    <JsonViewer value={activeSession.lastResponse || {}} maxHeight={260} />
                  </div>
                </>
              ) : null}

              {activePanel === "request" ? (
                <>
                  <div className="chat-debug-panel-card">
                    <strong>会话参数</strong>
                    <Divider style={{ margin: "10px 0" }} />
                    <JsonViewer value={activeSession.meta} maxHeight={220} />
                  </div>
                  <div className="chat-debug-panel-card">
                    <strong>附件信息</strong>
                    <Divider style={{ margin: "10px 0" }} />
                    <JsonViewer value={activeSession.attachment || {}} maxHeight={220} />
                  </div>
                </>
              ) : null}
            </div>
          )}

          <div className="chat-debug-input-bar">
            <Form form={form} layout="vertical" onFinish={sendMessage} initialValues={activeSession.meta} className="chat-debug-input-form">
              <div className="chat-debug-input-config">
                <div className="chat-debug-input-config-left">
                  <Form.Item label="模型" name="model" className="chat-debug-model-item">
                    <Select options={[AUTO_ROUTE_MODEL_OPTION, ...ARK_MODEL_OPTIONS]} getPopupContainer={getSelectPopupContainer} />
                  </Form.Item>
                  <Form.Item label="深度思考" name="thinkingMode" className="chat-debug-model-item">
                    <Select
                      placement="topLeft"
                      listHeight={96}
                      getPopupContainer={getSelectPopupContainer}
                      options={[
                        { label: "强制开启", value: "enabled" },
                        { label: "强制关闭", value: "disabled" },
                        { label: "自动判断", value: "auto", disabled: !supportsAutoThinking }
                      ]}
                    />
                  </Form.Item>
                </div>

                <div className="chat-debug-input-config-right">
                  <Upload {...uploadProps} showUploadList={false}>
                    <Button className="chat-debug-secondary-button chat-debug-config-action">上传附件</Button>
                  </Upload>
                  <Button className="chat-debug-secondary-button chat-debug-config-action" onClick={() => setAdvancedOpen(true)}>
                    高级参数
                  </Button>
                  {attachmentFiles[0] ? (
                    <Typography.Text type="secondary" className="chat-debug-attachment-name">
                      {attachmentFiles[0].name}
                    </Typography.Text>
                  ) : (
                    <span className="chat-debug-attachment-placeholder" />
                  )}
                </div>
              </div>

              <div className="chat-debug-input-hints">
                {!supportsAutoThinking ? <Typography.Text type="secondary">当前模型不支持自动判断</Typography.Text> : <Typography.Text type="secondary">默认按配置页自动路由，也可在此手动覆盖。</Typography.Text>}
                <Space size={12} wrap>
                  <Tooltip title="上传依赖环境变量：OSS_ACCESS_KEY_ID / OSS_ACCESS_KEY_SECRET / OSS_BUCKET_NAME / OSS_ENDPOINT">
                    <Typography.Text style={{ color: "#2563eb", cursor: "help" }}>OSS配置提示</Typography.Text>
                  </Tooltip>
                  <Typography.Text type="secondary">{panelTitleMap[activePanel]} 面板</Typography.Text>
                </Space>
              </div>

              <div className="chat-debug-input-main">
                <div className="chat-debug-input-box">
                  <Form.Item name="text" style={{ marginBottom: 0 }}>
                    <Input.TextArea rows={4} placeholder="输入消息..." />
                  </Form.Item>
                  <div className="chat-debug-hint-row">
                    <div>Ctrl + Enter 发送</div>
                    <div className="chat-debug-count">{String(textValue).length} 字</div>
                  </div>
                </div>

                <div className="chat-debug-input-actions">
                  <Button type="primary" htmlType="submit" className="chat-debug-primary-button chat-debug-side-action" loading={sending}>
                    发送
                  </Button>
                  <Button className="chat-debug-stop-button chat-debug-side-action" onClick={stopStream} disabled={!sending}>
                    停止
                  </Button>
                </div>
              </div>
            </Form>
          </div>
        </section>

        <aside className="chat-debug-inspector">
          <div className="chat-debug-inspector-body">
            <Card title="调试详情" bordered={false} className="chat-debug-inspector-card">
              <Space direction="vertical" size={10} style={{ width: "100%" }}>
                <div><Typography.Text type="secondary">当前状态</Typography.Text><div><StatusTag status={activeSession.status} /></div></div>
                <div><Typography.Text type="secondary">Prompt / Context</Typography.Text><div>{activeSession.meta.session_id}</div></div>
                <div><Typography.Text type="secondary">Token / Latency / Tool Stats</Typography.Text><div>请求数 {activeSession.debugEvents.length} · 工具 {eventStats.tool}</div></div>
                <div><Typography.Text type="secondary">错误与重试</Typography.Text><div>{activeSession.debugEvents.filter((item) => item.type === "raw").length} 条原始事件</div></div>
              </Space>
            </Card>

            <Card title="当前请求参数" bordered={false} className="chat-debug-inspector-card">
              <JsonViewer value={activeSession.lastRequest || {}} maxHeight={220} />
            </Card>

            <Card title="最近响应" bordered={false} className="chat-debug-inspector-card">
              <JsonViewer value={activeSession.lastResponse || {}} maxHeight={220} />
            </Card>
          </div>

          <div className="chat-debug-inspector-footer">
            <Card title="关联入口" bordered={false} className="chat-debug-inspector-card">
              <Space direction="vertical" style={{ width: "100%" }} size={8}>
                <Button block className="chat-debug-secondary-button" onClick={() => navigate(`/sessions/${encodeURIComponent(activeSession.meta.session_id)}`)}>打开会话中心</Button>
                <Button block className="chat-debug-secondary-button" onClick={() => navigate(`/logs?session_id=${encodeURIComponent(activeSession.meta.session_id)}`)}>打开请求日志</Button>
                <Button block className="chat-debug-secondary-button" onClick={() => setActivePanel("backend")}>切换 Trace 视图</Button>
              </Space>
            </Card>
          </div>
        </aside>
      </div>

      <Modal
        title="高级参数"
        open={advancedOpen}
        onCancel={() => setAdvancedOpen(false)}
        footer={[
          <Button key="close" className="chat-debug-secondary-button" onClick={() => setAdvancedOpen(false)}>
            完成
          </Button>
        ]}
        width={560}
      >
        <Form form={advancedForm} layout="vertical" className="chat-debug-advanced-form">
          <Form.Item label="session_id" name="session_id">
            <Input />
          </Form.Item>
          <Form.Item label="user_id" name="user_id">
            <Input />
          </Form.Item>
          <Form.Item label="source_channel" name="source_channel">
            <Input />
          </Form.Item>
          <Form.Item label="agent_profile" name="agent_profile">
            <Select allowClear getPopupContainer={getSelectPopupContainer} options={[{ value: "customer_support", label: "customer_support" }, { value: "employee_assistant", label: "employee_assistant" }]} />
          </Form.Item>
        </Form>
      </Modal>
    </div>
  );
}
