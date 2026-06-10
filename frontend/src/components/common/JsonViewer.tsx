import { Typography } from "antd";

interface JsonViewerProps {
  value: unknown;
  maxHeight?: number;
}

export function JsonViewer({ value, maxHeight = 360 }: JsonViewerProps) {
  return (
    <pre
      style={{
        margin: 0,
        padding: 12,
        background: "#0f172a",
        color: "#e2e8f0",
        borderRadius: 10,
        overflow: "auto",
        maxHeight,
        fontSize: 12,
        lineHeight: 1.6
      }}
    >
      {JSON.stringify(value ?? {}, null, 2)}
    </pre>
  );
}

export function TruncatedText({ value }: { value?: string | null }) {
  return (
    <Typography.Text ellipsis={{ tooltip: value || "-" }} style={{ maxWidth: "100%", display: "block" }}>
      {value || "-"}
    </Typography.Text>
  );
}
