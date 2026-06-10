export interface ApiCallItem {
  id: number;
  run_id: string;
  session_id?: string;
  user_id?: string;
  source_channel?: string;
  agent_profile?: string;
  route: string;
  intent_hint?: string;
  http_status_code?: number;
  status: string;
  latency_ms: number;
  created_at: string;
  request_json?: Record<string, unknown>;
  response_json?: Record<string, unknown>;
}

export interface ToolInvocationItem {
  id: number;
  run_id: string;
  tool_name: string;
  status: string;
  code?: string;
  message?: string;
  latency_ms: number;
  source?: string;
  tool_args?: Record<string, unknown>;
  tool_result?: Record<string, unknown>;
  created_at: string;
  layer_trace?: Record<string, unknown>;
}

export interface AgentErrorItem {
  id: number;
  run_id: string;
  route?: string;
  error_code: string;
  error_message?: string;
  stack_trace?: string;
  created_at: string;
}

export interface LogStats {
  request_count: number;
  failure_count: number;
  timeout_count: number;
  avg_latency_ms: number;
  stream_ratio: number;
}

export interface SessionSummaryItem {
  session_id: string;
  user_id?: string;
  source_channel?: string;
  agent_profile?: string;
  latest_run_id?: string;
  latest_route?: string;
  latest_status?: string;
  turn_count: number;
  error_count: number;
  avg_latency_ms: number;
  tool_count: number;
  title?: string;
  last_message?: string;
  started_at: string;
  updated_at: string;
}

export interface DashboardSummary {
  kpis: {
    request_count: number;
    session_count: number;
    success_rate: number;
    avg_latency_ms: number;
    error_count: number;
    tool_success_rate: number;
    estimated_cost: number;
  };
  trends: Array<{
    bucket: string;
    requests: number;
    errors: number;
    avg_latency_ms: number;
  }>;
  distribution: {
    by_channel: Array<{ label: string; value: number }>;
    by_route: Array<{ label: string; value: number }>;
    by_profile: Array<{ label: string; value: number }>;
  };
  health: {
    service: string;
    model: string;
    dependencies: string;
    version: string;
  };
  risky_sessions: Array<{
    session_id: string;
    user_id?: string;
    source_channel?: string;
    agent_profile?: string;
    updated_at: string;
    turn_count: number;
    error_count: number;
    avg_latency_ms: number;
  }>;
}
