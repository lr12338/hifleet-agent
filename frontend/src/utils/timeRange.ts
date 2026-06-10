import dayjs from "dayjs";

import type { TimeRangePreset } from "../layouts/AdminShell";

export function buildTimeRangeQuery(
  preset: TimeRangePreset,
  customRange: [string, string] | null
): { start_time?: string; end_time?: string } {
  if (customRange) {
    return {
      start_time: customRange[0],
      end_time: customRange[1]
    };
  }
  const end = dayjs();
  const start = preset === "1h" ? end.subtract(1, "hour") : preset === "24h" ? end.subtract(24, "hour") : preset === "7d" ? end.subtract(7, "day") : end.subtract(30, "day");
  return {
    start_time: start.toISOString(),
    end_time: end.toISOString()
  };
}

export function formatDuration(ms?: number | null): string {
  if (!ms && ms !== 0) return "-";
  if (ms < 1000) return `${ms} ms`;
  return `${(ms / 1000).toFixed(2)} s`;
}
