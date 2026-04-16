/** 玩法列表 API 类型（与 backend PLAY_CODE_GROUPS 对应） */

export interface PlayCodeItem {
  key_code: string;
  name: string;
}

export interface PlayCodeGroup {
  group_name: string;
  items: PlayCodeItem[];
}
