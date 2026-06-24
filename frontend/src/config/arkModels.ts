export interface ArkModelOption {
  label: string;
  value: string;
}

export const AUTO_ROUTE_MODEL_OPTION: ArkModelOption = {
  label: '自动路由（按配置页）',
  value: 'auto'
};

export const ARK_MODEL_OPTIONS: ArkModelOption[] = [
  {
    label: 'doubao-seed-2-0-lite-260428',
    value: 'doubao-seed-2-0-lite-260428'
  },
  {
    label: 'doubao-seed-2-0-pro-260215',
    value: 'doubao-seed-2-0-pro-260215'
  },
  {
    label: 'doubao-seed-2-0-mini-260428',
    value: 'doubao-seed-2-0-mini-260428'
  },
  {
    label: 'deepseek-v4-pro-260425',
    value: 'deepseek-v4-pro-260425'
  },
  {
    label: 'deepseek-v4-flash-260425',
    value: 'deepseek-v4-flash-260425'
  }
];

export const TEXT_MODEL_OPTIONS = ARK_MODEL_OPTIONS;
export const MULTIMODAL_MODEL_OPTIONS = [ARK_MODEL_OPTIONS[0]];
