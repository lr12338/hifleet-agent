export interface ArkModelOption {
  label: string;
  value: string;
  supportsAutoThinking: boolean;
}

export const AUTO_ROUTE_MODEL_OPTION: ArkModelOption = {
  label: '自动路由（按配置页）',
  value: 'auto',
  supportsAutoThinking: true
};

export const ARK_MODEL_OPTIONS: ArkModelOption[] = [
  {
    label: 'doubao-seed-2-0-lite-260428',
    value: 'doubao-seed-2-0-lite-260428',
    supportsAutoThinking: false
  },
  {
    label: 'doubao-seed-2-0-pro-260215',
    value: 'doubao-seed-2-0-pro-260215',
    supportsAutoThinking: false
  },
  {
    label: 'doubao-seed-2-0-mini-260428',
    value: 'doubao-seed-2-0-mini-260428',
    supportsAutoThinking: false
  },
  {
    label: 'deepseek-v4-pro-260425',
    value: 'deepseek-v4-pro-260425',
    supportsAutoThinking: true
  },
  {
    label: 'deepseek-v4-flash-260425',
    value: 'deepseek-v4-flash-260425',
    supportsAutoThinking: true
  }
];

export const TEXT_MODEL_OPTIONS = ARK_MODEL_OPTIONS.filter((item) => item.value !== 'doubao-seed-2-0-lite-260428');
export const MULTIMODAL_MODEL_OPTIONS = [ARK_MODEL_OPTIONS[0]];

export function modelSupportsAutoThinking(model: string): boolean {
  if (!model || model === 'auto') return true;
  const matched = ARK_MODEL_OPTIONS.find((item) => item.value === model);
  return matched ? matched.supportsAutoThinking : !/^doubao-seed|^glm-4-7/i.test(model);
}
