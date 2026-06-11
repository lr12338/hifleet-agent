import { Button, DatePicker, Space, Typography } from "antd";
import dayjs from "dayjs";

import { useAdminShell, type TimeRangePreset } from "../../layouts/AdminShell";

const { RangePicker } = DatePicker;

interface TimeRangeControlsProps {
  label?: string;
}

export function TimeRangeControls({ label = "时间范围" }: TimeRangeControlsProps) {
  const { timeRange, setTimeRange, customRange, setCustomRange } = useAdminShell();

  return (
    <Space size={12} wrap style={{ width: "100%", justifyContent: "space-between" }}>
      <Space size={12} wrap>
        <Typography.Text strong>{label}</Typography.Text>
        <Button.Group>
          {(["1h", "24h", "7d", "30d"] as TimeRangePreset[]).map((item) => (
            <Button
              key={item}
              type={!customRange && timeRange === item ? "primary" : "default"}
              onClick={() => {
                setCustomRange(null);
                setTimeRange(item);
              }}
            >
              {item}
            </Button>
          ))}
        </Button.Group>
      </Space>

      <RangePicker
        size="middle"
        showTime
        value={customRange ? [dayjs(customRange[0]), dayjs(customRange[1])] : null}
        onChange={(values) => {
          if (!values?.[0] || !values?.[1]) {
            setCustomRange(null);
            return;
          }
          setCustomRange([values[0].toISOString(), values[1].toISOString()]);
        }}
      />
    </Space>
  );
}
