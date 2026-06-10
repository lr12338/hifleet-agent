import { Tag } from "antd";

export function StatusTag({ status }: { status?: string | null }) {
  const value = (status || "unknown").toLowerCase();
  if (value === "success" || value === "ok" || value === "ended" || value === "done") {
    return <Tag color="green">{status}</Tag>;
  }
  if (value === "error" || value === "failed") {
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
