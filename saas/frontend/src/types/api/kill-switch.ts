/** 熔断开关 API 类型（与 backend/app/schemas/kill_switch.py 一一对应） */

export interface GlobalKillSwitchRequest {
  enabled: boolean;
}

export interface GlobalKillSwitchInfo {
  enabled: boolean;
}
