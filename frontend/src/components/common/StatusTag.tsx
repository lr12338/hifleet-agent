import { Tag } from "antd";

export function StatusTag({ status }: { status?: string | null }) {
  const value = (status || "unknown").toLowerCase();
  if (["success", "ok", "ended", "done", "healthy", "connected", "operational"].includes(value)) {
    return <Tag color="green">{status}</Tag>;
  }
  if (["warning", "degraded", "partial"].includes(value)) {
    return <Tag color="orange">{status}</Tag>;
  }
  if (["error", "failed", "unhealthy", "disconnected"].includes(value)) {
    return <Tag color="red">{status}</Tag>;
  }
  if (value === "timeout") {
    return <Tag color="orange">{status}</Tag>;
  }
  if (value === "running" || value === "streaming") {
    return <Tag color="blue">{status}</Tag>;
  }
  return <Tag>{status || "unknown"}</Tag>;
}
