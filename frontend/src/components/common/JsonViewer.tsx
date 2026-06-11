import { Button, Typography, message } from "antd";
import { useMemo } from "react";

interface JsonViewerProps {
  value: unknown;
  maxHeight?: number;
}

function escapeHtml(value: string): string {
  return value
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;");
}

function highlightJson(value: string): string {
  const escaped = escapeHtml(value);
  return escaped.replace(
    /("(?:\\u[\da-fA-F]{4}|\\[^u]|[^\\"])*"(\s*:)?|\btrue\b|\bfalse\b|\bnull\b|-?\d+(?:\.\d+)?(?:[eE][+\-]?\d+)?)/g,
    (match, _group, isKey) => {
      const className = isKey
        ? "json-viewer-key"
        : match.startsWith('"')
          ? "json-viewer-string"
          : match === "true" || match === "false"
            ? "json-viewer-boolean"
            : match === "null"
              ? "json-viewer-null"
              : "json-viewer-number";
      return `<span class="${className}">${match}</span>`;
    }
  );
}

export function JsonViewer({ value, maxHeight = 360 }: JsonViewerProps) {
  const formatted = useMemo(() => JSON.stringify(value ?? {}, null, 2), [value]);
  const highlighted = useMemo(() => highlightJson(formatted), [formatted]);

  return (
    <div className="json-viewer">
      <div className="json-viewer-toolbar">
        <Typography.Text type="secondary" style={{ fontSize: 12 }}>
          JSON
        </Typography.Text>
        <Button
          size="small"
          onClick={async () => {
            await navigator.clipboard.writeText(formatted);
            message.success("JSON 已复制");
          }}
        >
          复制
        </Button>
      </div>
      <pre
        className="json-viewer-content"
        style={{
          maxHeight
        }}
      >
        <code dangerouslySetInnerHTML={{ __html: highlighted }} />
      </pre>
    </div>
  );
}

export function TruncatedText({ value }: { value?: string | null }) {
  return (
    <Typography.Text ellipsis={{ tooltip: value || "-" }} style={{ maxWidth: "100%", display: "block" }}>
      {value || "-"}
    </Typography.Text>
  );
}
