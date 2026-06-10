export interface ArkModelOption {
  label: string;
  value: string;
  supportsAutoThinking: boolean;
}

export const ARK_MODEL_OPTIONS: ArkModelOption[] = [
  {
    label: "doubao-seed-2-0-lite-260428",
    value: "doubao-seed-2-0-lite-260428",
    supportsAutoThinking: false
  },
  {
    label: "doubao-seed-2-0-pro-260215",
    value: "doubao-seed-2-0-pro-260215",
    supportsAutoThinking: false
  },
  {
    label: "doubao-seed-2-0-mini-260428",
    value: "doubao-seed-2-0-mini-260428",
    supportsAutoThinking: false
  },
  {
    label: "deepseek-v4-pro-260425",
    value: "deepseek-v4-pro-260425",
    supportsAutoThinking: true
  },
  {
    label: "deepseek-v4-flash-260425",
    value: "deepseek-v4-flash-260425",
    supportsAutoThinking: true
  }
];

export function modelSupportsAutoThinking(model: string): boolean {
  const matched = ARK_MODEL_OPTIONS.find((item) => item.value === model);
  return matched ? matched.supportsAutoThinking : !/^doubao-seed|^glm-4-7/i.test(model);
}
