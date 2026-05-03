/**
 * Strategy create/edit form.
 */

import { type FormEvent, useCallback, useEffect, useState } from 'react';
import { isApiError } from '@/api/request';
import { createStrategy, updateStrategy } from '@/api/strategies';
import { listAccounts } from '@/api/accounts';
import PlayCodeMultiSelect from '@/components/PlayCodeMultiSelect';
import PlayCodeSelect from '@/components/PlayCodeSelect';
import type {
  OmissionRandomCategory,
  OmissionRandomPickCount,
  StrategyCreate,
  StrategyInfo,
  StrategyPermissionType,
  StrategyPlatformType,
  StrategyType,
  StrategyUpdate,
} from '@/types/api/strategy';
import type { AccountInfo } from '@/types/api/account';
import {
  buildDw3PlayCode,
  getDw3GroupLabel,
  getDw3GroupTabs,
  getDw3TheoreticalMaxStake,
  hasOnlyDw3GroupTokens,
  isDw3GroupToken,
  parseDw3PlayCode,
  type Dw3GroupTabKey,
} from '@/utils/dw3Groups';
import { getKeyCodeName } from '@/utils/key-code-map';
import { getPlatformLabel } from '@/utils/platformLabels';
import './StrategyForm.css';

type WaveStrategyType = 'red_wave_double_martin' | 'green_wave_single_martin';
type OmissionRandomStrategyType = 'omission_random_flat' | 'omission_random_martin';
type AiRandomStrategyType =
  | 'ai_random_flat'
  | 'ai_random_martin'
  | 'ai_same_random_flat'
  | 'ai_same_random_martin';
type RandomPickStrategyType = OmissionRandomStrategyType | AiRandomStrategyType;
type StrategyTypeValue = StrategyType | '';

interface StrategyFormProps {
  strategy: StrategyInfo | null;
  initialAccountId?: number;
  existingStrategies?: StrategyInfo[];
  onDone: () => void;
  onCancel: () => void;
}

const RED_WAVE_DOUBLE_TYPE: WaveStrategyType = 'red_wave_double_martin';
const GREEN_WAVE_SINGLE_TYPE: WaveStrategyType = 'green_wave_single_martin';
const OMISSION_RANDOM_FLAT_TYPE: OmissionRandomStrategyType = 'omission_random_flat';
const OMISSION_RANDOM_MARTIN_TYPE: OmissionRandomStrategyType = 'omission_random_martin';
const AI_RANDOM_FLAT_TYPE: AiRandomStrategyType = 'ai_random_flat';
const AI_RANDOM_MARTIN_TYPE: AiRandomStrategyType = 'ai_random_martin';
const AI_SAME_RANDOM_FLAT_TYPE: AiRandomStrategyType = 'ai_same_random_flat';
const AI_SAME_RANDOM_MARTIN_TYPE: AiRandomStrategyType = 'ai_same_random_martin';
const LUCKYSB_PLATFORM_TYPE: StrategyPlatformType = 'LUCKYSB';
const DW3_DISPLAY_NAME = '三字定位';
const DEFAULT_BET_TIMING = 88;
const BET_TIMING_MIN = 10;
const BET_TIMING_MAX = 100;
const LOW_BET_TIMING_WARNING_THRESHOLD = 20;
const LOW_BET_TIMING_MESSAGE =
  '当前下注时机低于20秒，可能因网络或平台响应延迟导致本期跳过或下注失败，确认继续使用该时机吗？';
const OMISSION_RANDOM_CATEGORY_OPTIONS: { key: OmissionRandomCategory; label: string }[] = [
  { key: 'ball1', label: '球1' },
  { key: 'ball2', label: '球2' },
  { key: 'ball3', label: '球3' },
  { key: 'sum', label: '和值' },
];
const OMISSION_RANDOM_PICK_COUNTS: OmissionRandomPickCount[] = [3, 4, 5, 6];
const OMISSION_RANDOM_PLAY_CODES: Record<OmissionRandomCategory, string> = {
  ball1: 'OMR_BALL1',
  ball2: 'OMR_BALL2',
  ball3: 'OMR_BALL3',
  sum: 'OMR_SUM',
};

const RED_WAVE_DIRECTION_ORDER = ['B1LM_S', 'B2LM_S', 'B3LM_S', 'DS4'] as const;
const GREEN_WAVE_DIRECTION_ORDER = ['B1LM_D', 'B2LM_D', 'B3LM_D', 'DS3'] as const;
const RED_WAVE_DIRECTION_OPTIONS = [
  { code: 'B1LM_S', label: '一球小' },
  { code: 'B2LM_S', label: '二球小' },
  { code: 'B3LM_S', label: '三球小' },
  { code: 'DS4', label: '双' },
] as const;

const LABEL_ACCOUNT = '账号';
const LABEL_PLATFORM = '盘口类型';
const LABEL_NAME = '策略名称';
const LABEL_AMOUNT = '基础金额（元）';
const BUTTON_CREATE = '创建';
const BUTTON_RED_WAVE = '红波追双';
const ERROR_SELECT_TYPE = '请选择策略类型';

const STRATEGY_NOT_AUTHORIZED_MESSAGE = '当前账号暂未开通策略，请联系管理员';
const STRATEGY_TYPE_OPTIONS: Array<{
  type: StrategyType;
  permission: StrategyPermissionType;
  label: string;
  jndOnly?: boolean;
}> = [
  { type: AI_SAME_RANDOM_FLAT_TYPE, permission: AI_SAME_RANDOM_FLAT_TYPE, label: 'AI推荐同号平注', jndOnly: true },
  { type: AI_SAME_RANDOM_MARTIN_TYPE, permission: AI_SAME_RANDOM_MARTIN_TYPE, label: 'AI推荐同号平注马丁', jndOnly: true },
  { type: 'flat', permission: 'flat', label: '平注' },
  { type: 'martin', permission: 'martin', label: '马丁' },
  { type: OMISSION_RANDOM_FLAT_TYPE, permission: OMISSION_RANDOM_FLAT_TYPE, label: '遗漏随机平注', jndOnly: true },
  { type: OMISSION_RANDOM_MARTIN_TYPE, permission: OMISSION_RANDOM_MARTIN_TYPE, label: '遗漏随机马丁', jndOnly: true },
  { type: AI_RANDOM_FLAT_TYPE, permission: AI_RANDOM_FLAT_TYPE, label: 'AI推荐平注', jndOnly: true },
  { type: AI_RANDOM_MARTIN_TYPE, permission: AI_RANDOM_MARTIN_TYPE, label: 'AI推荐马丁', jndOnly: true },
  { type: RED_WAVE_DOUBLE_TYPE, permission: RED_WAVE_DOUBLE_TYPE, label: '红波追双', jndOnly: true },
  { type: GREEN_WAVE_SINGLE_TYPE, permission: GREEN_WAVE_SINGLE_TYPE, label: '绿波追单', jndOnly: true },
];
const DW3_STRATEGY_TYPE_OPTIONS: Array<{
  type: StrategyType;
  permission: StrategyPermissionType;
  label: string;
}> = [
  { type: 'flat', permission: 'dw3_flat', label: '平注' },
  { type: 'martin', permission: 'dw3_martin', label: '马丁' },
];

function normalizeRedWaveDirections(codes: string[]): string[] {
  const upper = codes.map((code) => code.trim().toUpperCase()).filter(Boolean);
  const deduped = Array.from(new Set(upper));
  return RED_WAVE_DIRECTION_ORDER.filter((code) => deduped.includes(code));
}

function normalizeGreenWaveDirections(codes: string[]): string[] {
  const upper = codes.map((code) => code.trim().toUpperCase()).filter(Boolean);
  const deduped = Array.from(new Set(upper));
  return GREEN_WAVE_DIRECTION_ORDER.filter((code) => deduped.includes(code));
}

function isWaveStrategyType(type: StrategyTypeValue): type is WaveStrategyType {
  return type === RED_WAVE_DOUBLE_TYPE || type === GREEN_WAVE_SINGLE_TYPE;
}

function isOmissionRandomType(type: StrategyTypeValue): type is OmissionRandomStrategyType {
  return type === OMISSION_RANDOM_FLAT_TYPE || type === OMISSION_RANDOM_MARTIN_TYPE;
}

function isAiRandomType(type: StrategyTypeValue): type is AiRandomStrategyType {
  return (
    type === AI_RANDOM_FLAT_TYPE ||
    type === AI_RANDOM_MARTIN_TYPE ||
    type === AI_SAME_RANDOM_FLAT_TYPE ||
    type === AI_SAME_RANDOM_MARTIN_TYPE
  );
}

function isAiSameRandomType(type: StrategyTypeValue): type is AiRandomStrategyType {
  return type === AI_SAME_RANDOM_FLAT_TYPE || type === AI_SAME_RANDOM_MARTIN_TYPE;
}

function isRandomPickStrategyType(type: StrategyTypeValue): type is RandomPickStrategyType {
  return isOmissionRandomType(type) || isAiRandomType(type);
}

function getWaveDirectionDefault(type: WaveStrategyType): string[] {
  return type === RED_WAVE_DOUBLE_TYPE ? ['DS4'] : ['DS3'];
}

function getWaveDirectionOptions(type: WaveStrategyType) {
  if (type === RED_WAVE_DOUBLE_TYPE) {
    return RED_WAVE_DIRECTION_OPTIONS.map((option) => ({
      code: option.code,
      label: getKeyCodeName(option.code),
    }));
  }
  return GREEN_WAVE_DIRECTION_ORDER.map((code) => ({ code, label: getKeyCodeName(code) }));
}

function getWaveDirectionLabel(type: WaveStrategyType): string {
  return type === RED_WAVE_DOUBLE_TYPE ? '红波方向' : '绿波方向';
}

function normalizeWaveDirections(type: WaveStrategyType, codes: string[]): string[] {
  return type === RED_WAVE_DOUBLE_TYPE
    ? normalizeRedWaveDirections(codes)
    : normalizeGreenWaveDirections(codes);
}

function isMartinLike(type: StrategyTypeValue) {
  return (
    type === 'martin' ||
    isWaveStrategyType(type) ||
    type === OMISSION_RANDOM_MARTIN_TYPE ||
    type === AI_RANDOM_MARTIN_TYPE ||
    type === AI_SAME_RANDOM_MARTIN_TYPE
  );
}

function buildOmissionRandomPlayCode(categories: OmissionRandomCategory[]): string {
  return OMISSION_RANDOM_CATEGORY_OPTIONS
    .map((option) => option.key)
    .filter((category) => categories.includes(category))
    .map((category) => OMISSION_RANDOM_PLAY_CODES[category])
    .join(',');
}

function normalizeOmissionRandomCategories(value: unknown): OmissionRandomCategory[] {
  if (!Array.isArray(value)) return ['ball1'];
  const selected = new Set(value.filter((item): item is OmissionRandomCategory =>
    item === 'ball1' || item === 'ball2' || item === 'ball3' || item === 'sum'
  ));
  const normalized = OMISSION_RANDOM_CATEGORY_OPTIONS
    .map((option) => option.key)
    .filter((category) => selected.has(category));
  return normalized.length > 0 ? normalized : ['ball1'];
}

function normalizeOmissionRandomPickCount(value: unknown): OmissionRandomPickCount {
  const n = Number(value);
  return OMISSION_RANDOM_PICK_COUNTS.includes(n as OmissionRandomPickCount)
    ? (n as OmissionRandomPickCount)
    : 3;
}

function isRealPlatformType(platformType?: string | null): platformType is StrategyPlatformType {
  return platformType === 'JND28WEB' || platformType === 'JND282' || platformType === 'LUCKYSB';
}

function parsePlatformType(platformType?: string | null): StrategyPlatformType | null {
  if (isRealPlatformType(platformType)) return platformType;
  return null;
}

function isDw3Platform(platformType: string) {
  return platformType === 'JND28WEB' || platformType === 'JND282';
}

function getAccountGameTypeLabel(account: AccountInfo): string {
  const gameType = account.game_type;
  if (gameType === 'JND28') return '加拿大28';
  if (gameType === 'LUCKYSB') return '极速飞艇';
  return gameType ?? '-';
}

function getStrategyPlatformLabel(platformType: string): string {
  if (platformType === 'JND28WEB') return 'WEB';
  if (platformType === 'JND282') return '2.0';
  return getPlatformLabel(platformType);
}

type AccountVerificationMeta = {
  effectiveVerificationRunId: number | null;
  verificationStale: boolean;
};

function toTruthyFlag(value: unknown): boolean {
  if (typeof value === 'boolean') return value;
  if (typeof value === 'number') return value !== 0;
  if (typeof value === 'string') return ['1', 'true', 'yes', 'y'].includes(value.trim().toLowerCase());
  return false;
}

function getAccountVerificationMeta(account?: AccountInfo): AccountVerificationMeta {
  if (!account) {
    return {
      effectiveVerificationRunId: null,
      verificationStale: false,
    };
  }
  const verification = account as AccountInfo & {
    effective_verification_run_id?: number | string | null;
    verification_stale?: boolean | number | string | null;
  };
  const parsedRunId = Number(verification.effective_verification_run_id);
  const effectiveVerificationRunId =
    Number.isInteger(parsedRunId) && parsedRunId > 0 ? parsedRunId : null;
  return {
    effectiveVerificationRunId,
    verificationStale: toTruthyFlag(verification.verification_stale),
  };
}

function resolveAllowedPlatformTypes(account?: AccountInfo): StrategyPlatformType[] {
  const verificationMeta = getAccountVerificationMeta(account);
  if (!verificationMeta.effectiveVerificationRunId || verificationMeta.verificationStale) return [];
  const explicit = Array.from(
    new Set(
      (account?.allowed_strategy_platform_types ?? [])
        .map((item) => `${item}`.trim().toUpperCase())
        .filter((item): item is StrategyPlatformType => isRealPlatformType(item))
    )
  );
  return explicit;
}

function resolveAllowedStrategyTypes(account?: AccountInfo): StrategyPermissionType[] {
  return Array.from(new Set(account?.allowed_strategy_types ?? []));
}

function getSelectableStrategyTypeOptions(
  allowedStrategyTypes: StrategyPermissionType[],
  platformType: string,
  dw3Mode: boolean
) {
  const allowed = new Set(allowedStrategyTypes);
  if (dw3Mode) {
    return DW3_STRATEGY_TYPE_OPTIONS.filter((option) => allowed.has(option.permission));
  }
  const isJndPlatform = platformType !== LUCKYSB_PLATFORM_TYPE;
  return STRATEGY_TYPE_OPTIONS.filter((option) => {
    if (!allowed.has(option.permission)) return false;
    if (option.jndOnly && !isJndPlatform) return false;
    return true;
  });
}

function isStrategyTypeAuthorized(
  type: StrategyTypeValue,
  allowedStrategyTypes: StrategyPermissionType[],
  platformType: string,
  dw3Mode: boolean
) {
  if (!type) return false;
  return getSelectableStrategyTypeOptions(allowedStrategyTypes, platformType, dw3Mode)
    .some((option) => option.type === type);
}

function pickPlatformType(
  allowed: StrategyPlatformType[],
  preferred?: string | null
): string {
  if (allowed.length === 0) return '';
  const normalizedPreferred = parsePlatformType(preferred);
  if (normalizedPreferred && allowed.includes(normalizedPreferred)) return normalizedPreferred;
  return allowed[0];
}

function getVerificationGateError(account: AccountInfo | undefined, allowedPlatformTypes: StrategyPlatformType[]): string {
  if (!account) return '请选择账号';
  const { effectiveVerificationRunId, verificationStale } = getAccountVerificationMeta(account);
  if (!effectiveVerificationRunId) {
    return '当前账号暂无有效验证结果，请先完成账号验证。';
  }
  if (verificationStale) {
    return '当前账号验证结果已失效，请重新验证后再创建策略。';
  }
  if (allowedPlatformTypes.length === 0) {
    if (`${account.game_type ?? ''}`.toUpperCase() === 'JND28') {
      return '当前账号暂无可用盘口（WEB / 2.0），请先完成有效验证。';
    }
    return '当前账号暂无可用平台，请先完成有效验证。';
  }
  return '';
}

function isStrategyTypeValidForPlatform(
  type: StrategyTypeValue,
  platformType: string,
  dw3Mode: boolean
) {
  if (!type) return false;
  if (dw3Mode) return type === 'flat' || type === 'martin';
  if (platformType === LUCKYSB_PLATFORM_TYPE) return type === 'flat' || type === 'martin';
  return true;
}

const DW3_MULTI_WARNING =
  `同账号创建多个${DW3_DISPLAY_NAME}策略可能会造成无法下注的风险。如发现问题请截图联系管理员。`;

export default function StrategyForm({
  strategy,
  initialAccountId,
  existingStrategies = [],
  onDone,
  onCancel,
}: StrategyFormProps) {
  const isEdit = !!strategy;

  const initialStrategyPlatformType = parsePlatformType(strategy?.platform_type) ?? '';
  const initialDw3Parsed = parseDw3PlayCode(strategy?.play_code ?? '');
  const initialDw3Mode = Boolean(strategy && hasOnlyDw3GroupTokens(strategy.play_code));

  const [accounts, setAccounts] = useState<AccountInfo[]>([]);
  const [accountsLoading, setAccountsLoading] = useState(true);

  const [accountId, setAccountId] = useState<number>(strategy?.account_id ?? initialAccountId ?? 0);
  const [name, setName] = useState(strategy?.name ?? '');
  const [type, setType] = useState<StrategyTypeValue>((strategy?.type as StrategyType) ?? 'flat');
  const [playCode, setPlayCode] = useState<string[]>(
    strategy?.play_code && initialStrategyPlatformType !== LUCKYSB_PLATFORM_TYPE && !initialDw3Mode
      ? strategy.play_code.split(',')
      : []
  );
  const [luckySbPlayCode, setLuckySbPlayCode] = useState(
    initialStrategyPlatformType === LUCKYSB_PLATFORM_TYPE ? (strategy?.play_code ?? '') : ''
  );
  const [waveDirections, setWaveDirections] = useState<string[]>(
    (() => {
      const strategyWaveType: WaveStrategyType | null =
        strategy && isWaveStrategyType(strategy.type as StrategyTypeValue)
          ? (strategy.type as WaveStrategyType)
          : null;
      if (!strategyWaveType) return ['DS4'];
      const normalized = normalizeWaveDirections(strategyWaveType, (strategy?.play_code ?? '').split(','));
      return normalized.length > 0 ? normalized : getWaveDirectionDefault(strategyWaveType);
    })()
  );
  const [platformType, setPlatformType] = useState<string>(initialStrategyPlatformType);
  const [baseAmount, setBaseAmount] = useState(strategy?.base_amount?.toString() ?? '1');
  const [martinSequence, setMartinSequence] = useState(strategy?.martin_sequence?.join(',') ?? '1,2,4,8,16');
  const [omissionRandomCategories, setOmissionRandomCategories] = useState<OmissionRandomCategory[]>(
    normalizeOmissionRandomCategories(strategy?.strategy_config?.categories)
  );
  const [omissionRandomPickCount, setOmissionRandomPickCount] = useState<OmissionRandomPickCount>(
    normalizeOmissionRandomPickCount(strategy?.strategy_config?.pick_count)
  );
  const [betTiming, setBetTiming] = useState(
    strategy?.bet_timing?.toString() ?? String(DEFAULT_BET_TIMING)
  );
  const [betTimingTouched, setBetTimingTouched] = useState(false);
  const [simulation, setSimulation] = useState(strategy?.simulation ?? false);
  const [stopLoss, setStopLoss] = useState(strategy?.stop_loss?.toString() ?? '');
  const [takeProfit, setTakeProfit] = useState(strategy?.take_profit?.toString() ?? '');

  const [dw3Mode, setDw3Mode] = useState(initialDw3Mode);
  const [dw3ActiveTab, setDw3ActiveTab] = useState<Dw3GroupTabKey>('bs');
  const [dw3SelectedBs, setDw3SelectedBs] = useState<string[]>(initialDw3Parsed.selectedBs);
  const [dw3SelectedOe, setDw3SelectedOe] = useState<string[]>(initialDw3Parsed.selectedOe);
  const [gateWindowIssues, setGateWindowIssues] = useState<string>(
    strategy?.gate_window_issues?.toString() ?? '1'
  );

  const [formError, setFormError] = useState('');
  const [resetNotice, setResetNotice] = useState('');
  const [submitting, setSubmitting] = useState(false);

  const isLuckySb = platformType === LUCKYSB_PLATFORM_TYPE;
  const isWaveStrategy = isWaveStrategyType(type);
  const isAiRandomStrategy = isAiRandomType(type);
  const isAiSameRandomStrategy = isAiSameRandomType(type);
  const isRandomPickStrategy = isRandomPickStrategyType(type);
  const randomPickCategoryOptions = isAiSameRandomStrategy
    ? OMISSION_RANDOM_CATEGORY_OPTIONS.filter((option) => option.key !== 'sum')
    : OMISSION_RANDOM_CATEGORY_OPTIONS;
  const activeWaveStrategyType = isWaveStrategyType(type) ? type : null;

  const selectedAccount = accounts.find((a) => a.id === accountId);
  const allowedPlatformTypes = resolveAllowedPlatformTypes(selectedAccount);
  const allowedStrategyTypes = resolveAllowedStrategyTypes(selectedAccount);
  const verificationGateError = getVerificationGateError(selectedAccount, allowedPlatformTypes);
  const selectablePlatformTypes = (
    dw3Mode ? allowedPlatformTypes.filter((item) => isDw3Platform(item)) : allowedPlatformTypes
  );
  const effectivePlatformTypes = selectablePlatformTypes;
  const currentPlatformType = isRealPlatformType(platformType) ? platformType : '';
  const displayedPlatformType =
    currentPlatformType && effectivePlatformTypes.includes(currentPlatformType)
      ? currentPlatformType
      : '';
  const hasDw3Platform = allowedPlatformTypes.some((item) => isDw3Platform(item));
  const normalStrategyTypeOptions = getSelectableStrategyTypeOptions(
    allowedStrategyTypes,
    platformType,
    false
  );
  const dw3StrategyTypeOptions = getSelectableStrategyTypeOptions(
    allowedStrategyTypes,
    platformType,
    true
  );
  const selectableStrategyTypeOptions = dw3Mode ? dw3StrategyTypeOptions : normalStrategyTypeOptions;
  const hasAnySelectableStrategyType =
    normalStrategyTypeOptions.length > 0 || (hasDw3Platform && dw3StrategyTypeOptions.length > 0);
  const strategyPermissionError =
    !isEdit && selectedAccount && !hasAnySelectableStrategyType
      ? STRATEGY_NOT_AUTHORIZED_MESSAGE
      : '';
  const strategyTypeUnavailableForCurrentMode =
    !isEdit && selectableStrategyTypeOptions.length === 0;
  const submitDisabled =
    submitting ||
    (accounts.length === 0 && !isEdit) ||
    Boolean(verificationGateError) ||
    Boolean(strategyPermissionError) ||
    strategyTypeUnavailableForCurrentMode;
  const canUseDw3 =
    (hasDw3Platform && dw3StrategyTypeOptions.length > 0) ||
    dw3Mode;
  const sameAccountExistingDw3Count = existingStrategies.filter((item) => {
    if (item.account_id !== accountId) return false;
    if (strategy && item.id === strategy.id) return false;
    return hasOnlyDw3GroupTokens(item.play_code);
  }).length;
  const showDw3MultiWarning = !isEdit && dw3Mode && sameAccountExistingDw3Count > 0;

  const dw3Tabs = getDw3GroupTabs();
  const activeDw3Tab =
    dw3Tabs.find((tab) => tab.key === dw3ActiveTab) ??
    ({
      key: 'bs',
      label: '',
      groups: [],
    } as const);

  const dw3TheoreticalMaxStake = getDw3TheoreticalMaxStake(
    dw3SelectedBs.length,
    dw3SelectedOe.length,
    Number(baseAmount)
  );

  const applyPlatformChange = (nextPlatformType: string) => {
    if (nextPlatformType === platformType) return;

    const cleared: string[] = [];
    if (nextPlatformType === LUCKYSB_PLATFORM_TYPE) {
      if (playCode.length > 0) cleared.push('加拿大28玩法');
      setPlayCode([]);
      if (!isStrategyTypeValidForPlatform(type, nextPlatformType, false)) {
        setType('');
        setWaveDirections([]);
        cleared.push('策略类型');
      }
      if (dw3Mode) {
        setDw3Mode(false);
        setDw3SelectedBs([]);
        setDw3SelectedOe([]);
        cleared.push(`${DW3_DISPLAY_NAME}模式`);
      }
    } else if (platformType === LUCKYSB_PLATFORM_TYPE) {
      if (luckySbPlayCode) cleared.push('极速飞艇玩法');
      setLuckySbPlayCode('');
    }

    if (cleared.length > 0) {
      setResetNotice(`切换到${getStrategyPlatformLabel(nextPlatformType)}后已重置：${cleared.join('、')}`);
    } else {
      setResetNotice(`已切换到${getStrategyPlatformLabel(nextPlatformType)}`);
    }
    setPlatformType(nextPlatformType);
  };

  const fetchAccounts = useCallback(async () => {
    try {
      const res = await listAccounts();
      const list = res.data ?? [];
      setAccounts(list);
      if (list.length > 0) {
        const nextAccountId = accountId === 0 ? (initialAccountId ?? list[0].id) : accountId;
        const selected = list.find((account) => account.id === nextAccountId) ?? list[0];
        if (!isEdit) setAccountId(selected.id);
        const nextAllowed = resolveAllowedPlatformTypes(selected);
        setPlatformType((current) => pickPlatformType(nextAllowed, current));
      }
    } catch (err) {
      if (isApiError(err)) setFormError(`加载账号失败：${err.message}`);
    } finally {
      setAccountsLoading(false);
    }
  }, [isEdit, accountId, initialAccountId]);

  useEffect(() => {
    fetchAccounts();
  }, [fetchAccounts]);

  useEffect(() => {
    if (isEdit) return;
    if (selectableStrategyTypeOptions.length === 0) {
      if (type !== '') setType('');
      return;
    }
    if (!selectableStrategyTypeOptions.some((option) => option.type === type)) {
      const fallbackType =
        selectableStrategyTypeOptions.find((option) => option.type === 'flat')?.type ??
        selectableStrategyTypeOptions[0].type;
      setType(fallbackType);
    }
  }, [isEdit, selectableStrategyTypeOptions, type]);

  const handleAccountChange = (nextAccountId: number) => {
    setAccountId(nextAccountId);
    const nextAccount = accounts.find((a) => a.id === nextAccountId);
    const nextAllowed = resolveAllowedPlatformTypes(nextAccount);
    const nextPlatformType = pickPlatformType(nextAllowed, platformType);
    if (dw3Mode && !isDw3Platform(nextPlatformType)) {
      setDw3Mode(false);
    }
    applyPlatformChange(nextPlatformType);
  };

  const toggleDw3Mode = (enabled: boolean) => {
    if (enabled) {
      if (!canUseDw3) return;
      setDw3Mode(true);
      if (isWaveStrategyType(type) || isRandomPickStrategyType(type)) setType('flat');
      setPlayCode([]);
      setLuckySbPlayCode('');
      if (!isDw3Platform(platformType)) {
        const dw3Platforms = allowedPlatformTypes.filter((item) => isDw3Platform(item));
        setPlatformType(pickPlatformType(dw3Platforms, platformType));
      }
      if (dw3SelectedBs.length === 0 && dw3SelectedOe.length === 0) {
        setDw3SelectedBs(['DW3_BS_BBB']);
      }
      return;
    }

    setDw3Mode(false);
    setPlatformType((current) => pickPlatformType(allowedPlatformTypes, current));
  };

  const handleStrategyTypeSelect = (nextType: StrategyType) => {
    setType(nextType);
    if (isAiSameRandomType(nextType)) {
      setOmissionRandomCategories((current) => {
        const withoutSum = current.filter((category) => category !== 'sum');
        return withoutSum.length > 0 ? withoutSum : ['ball1'];
      });
    }
    if (nextType === RED_WAVE_DOUBLE_TYPE || nextType === GREEN_WAVE_SINGLE_TYPE) {
      setWaveDirections((current) => {
        const normalized = normalizeWaveDirections(nextType, current);
        return normalized.length > 0 ? normalized : getWaveDirectionDefault(nextType);
      });
    }
  };

  const toggleWaveDirection = (code: string) => {
    setWaveDirections((previous) => {
      if (!activeWaveStrategyType) return previous;
      if (previous.includes(code)) {
        return previous.filter((item) => item !== code);
      }
      return normalizeWaveDirections(activeWaveStrategyType, [...previous, code]);
    });
  };

  const toggleOmissionRandomCategory = (category: OmissionRandomCategory) => {
    if (isAiSameRandomStrategy && category === 'sum') return;
    setOmissionRandomCategories((previous) => {
      if (previous.includes(category)) {
        return previous.filter((item) => item !== category);
      }
      return randomPickCategoryOptions
        .map((option) => option.key)
        .filter((item) => [...previous, category].includes(item));
    });
  };

  const toggleDw3Group = (token: string) => {
    if (!isDw3GroupToken(token)) return;

    if (dw3ActiveTab === 'bs') {
      setDw3SelectedBs((previous) =>
        previous.includes(token) ? previous.filter((item) => item !== token) : [...previous, token]
      );
      return;
    }

    setDw3SelectedOe((previous) =>
      previous.includes(token) ? previous.filter((item) => item !== token) : [...previous, token]
    );
  };

  const handleSubmit = async (e: FormEvent) => {
    e.preventDefault();
    setFormError('');

    if (verificationGateError) {
      setFormError(verificationGateError);
      return;
    }
    const submitPlatformType = parsePlatformType(platformType);
    if (!submitPlatformType) {
      setFormError('请选择可用盘口类型');
      return;
    }
    const isLuckySbPlatform = submitPlatformType === LUCKYSB_PLATFORM_TYPE;

    if (!type) {
      setFormError(ERROR_SELECT_TYPE);
      return;
    }
    if (!isEdit && !isStrategyTypeAuthorized(type, allowedStrategyTypes, submitPlatformType, dw3Mode)) {
      setFormError(STRATEGY_NOT_AUTHORIZED_MESSAGE);
      return;
    }
    if (!allowedPlatformTypes.includes(submitPlatformType)) {
      setFormError('当前账号不支持所选盘口类型');
      return;
    }
    if (!isStrategyTypeValidForPlatform(type, submitPlatformType, dw3Mode)) {
      setFormError('当前平台与策略类型不匹配');
      return;
    }

    if (!name.trim()) {
      setFormError('请输入策略名称');
      return;
    }
    if (!baseAmount || Number(baseAmount) <= 0) {
      setFormError('基础金额必须大于 0');
      return;
    }
    const parsedBetTiming = Number(betTiming);
    const shouldSubmitBetTiming = !isEdit || betTimingTouched;
    if (shouldSubmitBetTiming) {
      if (
        !betTiming ||
        Number.isNaN(parsedBetTiming) ||
        parsedBetTiming < BET_TIMING_MIN ||
        parsedBetTiming > BET_TIMING_MAX
      ) {
        setFormError(`下注时机必须在 ${BET_TIMING_MIN} 到 ${BET_TIMING_MAX} 秒之间`);
        return;
      }
      if (parsedBetTiming < LOW_BET_TIMING_WARNING_THRESHOLD && !window.confirm(LOW_BET_TIMING_MESSAGE)) {
        setFormError(LOW_BET_TIMING_MESSAGE);
        return;
      }
    }

    if (dw3Mode && !isDw3Platform(submitPlatformType)) {
      setFormError(`${DW3_DISPLAY_NAME}平台必须是 WEB 或 2.0`);
      return;
    }
    if (isLuckySbPlatform && !dw3Mode && !luckySbPlayCode) {
      setFormError('请选择极速飞艇玩法');
      return;
    }
    if (!isLuckySbPlatform && !dw3Mode && !isWaveStrategy && !isRandomPickStrategy && playCode.length === 0) {
      setFormError('请选择玩法');
      return;
    }

    const selectedRandomPickCategories = isAiSameRandomStrategy
      ? omissionRandomCategories.filter((category) => category !== 'sum')
      : omissionRandomCategories;

    if (isRandomPickStrategy && selectedRandomPickCategories.length === 0) {
      setFormError('请选择随机选号类型');
      return;
    }

    const selectedDirections = activeWaveStrategyType
      ? normalizeWaveDirections(activeWaveStrategyType, waveDirections)
      : [];
    if (isWaveStrategy && selectedDirections.length === 0) {
      setFormError(activeWaveStrategyType ? `请选择${getWaveDirectionLabel(activeWaveStrategyType)}` : '请选择波色方向');
      return;
    }

    const dw3PlayCode = buildDw3PlayCode(dw3SelectedBs, dw3SelectedOe);
    const parsedGateWindowIssues = Number(gateWindowIssues);
    if (dw3Mode) {
      if (!dw3PlayCode) {
        setFormError(`请选择${DW3_DISPLAY_NAME}组合`);
        return;
      }
      if (!Number.isInteger(parsedGateWindowIssues) || parsedGateWindowIssues < 1) {
        setFormError('最近期数必须是大于等于 1 的整数');
        return;
      }
    }

    let parsedSequence: number[] | null = null;
    if (isMartinLike(type)) {
      const parts = martinSequence.split(',').map((s) => s.trim()).filter(Boolean);
      if (parts.length === 0) {
        setFormError('请输入马丁序列');
        return;
      }
      parsedSequence = parts.map(Number);
      if (parsedSequence.some((n) => Number.isNaN(n) || n <= 0)) {
        setFormError('马丁序列必须全部是大于 0 的数字');
        return;
      }
    }

    const randomPickConfig = isRandomPickStrategy
      ? {
          pick_count: omissionRandomPickCount,
          categories: selectedRandomPickCategories,
          weight_mode: isAiRandomStrategy ? 'pure_random' : 'omission_plus_random',
        }
      : null;
    const randomPickPlayCode = isRandomPickStrategy
      ? buildOmissionRandomPlayCode(selectedRandomPickCategories)
      : '';

    setSubmitting(true);
    try {
      if (isEdit && strategy) {
        const updatePayload: StrategyUpdate = {
          name: name.trim(),
          base_amount: Number(baseAmount),
          martin_sequence: parsedSequence,
          simulation,
          stop_loss: stopLoss ? Number(stopLoss) : null,
          take_profit: takeProfit ? Number(takeProfit) : null,
          platform_type: submitPlatformType,
        };
        if (betTimingTouched) {
          updatePayload.bet_timing = parsedBetTiming;
        }

        if (dw3Mode) {
          updatePayload.play_code = dw3PlayCode;
          updatePayload.gate_window_issues = parsedGateWindowIssues;
        } else if (isRandomPickStrategy && randomPickConfig) {
          updatePayload.play_code = randomPickPlayCode;
          updatePayload.strategy_config = randomPickConfig;
        } else if (isWaveStrategy) {
          updatePayload.play_code = selectedDirections.join(',');
        }

        await updateStrategy(strategy.id, updatePayload);
      } else {
        const data: StrategyCreate = {
          account_id: accountId,
          name: name.trim(),
          type,
          play_code: dw3Mode
            ? dw3PlayCode
            : isLuckySbPlatform
            ? luckySbPlayCode
            : isRandomPickStrategy
            ? randomPickPlayCode
            : isWaveStrategy
            ? selectedDirections.join(',')
            : playCode.join(','),
          base_amount: Number(baseAmount),
          martin_sequence: parsedSequence,
          strategy_config: randomPickConfig,
          bet_timing: parsedBetTiming,
          simulation,
          stop_loss: stopLoss ? Number(stopLoss) : null,
          take_profit: takeProfit ? Number(takeProfit) : null,
          platform_type: submitPlatformType,
          gate_window_issues: dw3Mode ? parsedGateWindowIssues : null,
        };
        await createStrategy(data);
      }
      onDone();
    } catch (err) {
      if (isApiError(err)) setFormError(err.message);
      else setFormError('提交失败，请稍后重试');
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="strategy-form-page">
      <form className="strategy-form" onSubmit={handleSubmit}>
        <div className="strategy-form-header">
          <h2 className="strategy-form-title">{isEdit ? '编辑策略' : '创建策略'}</h2>
        </div>

        {formError && <div role="alert" className="form-error">{formError}</div>}
        {resetNotice && <div role="status" className="form-hint form-hint-warn">{resetNotice}</div>}
        {verificationGateError && <div role="status" className="form-hint form-hint-warn">{verificationGateError}</div>}
        {showDw3MultiWarning && (
          <div role="status" className="form-warning">
            {DW3_MULTI_WARNING}
          </div>
        )}

        {!isEdit && (
          <div className="form-field">
            <label htmlFor="sf-account" className="form-label">{LABEL_ACCOUNT}</label>
            {accountsLoading ? (
              <div className="form-hint">正在加载账号...</div>
            ) : accounts.length === 0 ? (
              <div className="form-hint form-hint-warn">暂无可用账号</div>
            ) : (
              <select
                id="sf-account"
                className="form-select"
                value={accountId}
                onChange={(e) => handleAccountChange(Number(e.target.value))}
                disabled={submitting}
              >
                {accounts.map((a) => (
                  <option key={a.id} value={a.id}>
                    {a.account_name}（{getAccountGameTypeLabel(a)}）
                  </option>
                ))}
              </select>
            )}
          </div>
        )}

        <div className="form-field">
          <label htmlFor="sf-name" className="form-label">{LABEL_NAME}</label>
          <input
            id="sf-name"
            type="text"
            className="form-input"
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="请输入策略名称"
            disabled={submitting}
            autoComplete="off"
          />
        </div>

        {!isEdit && canUseDw3 && (
          <div className="form-field">
            <span className="form-label">{DW3_DISPLAY_NAME}模式</span>
            <div className="type-toggle">
              <button
                type="button"
                className={`type-toggle-btn ${!dw3Mode ? 'type-toggle-active' : ''}`}
                onClick={() => toggleDw3Mode(false)}
                disabled={submitting}
              >
                普通模式
              </button>
              <button
                type="button"
                className={`type-toggle-btn ${dw3Mode ? 'type-toggle-active' : ''}`}
                onClick={() => toggleDw3Mode(true)}
                disabled={submitting}
              >
                {DW3_DISPLAY_NAME}
              </button>
            </div>
          </div>
        )}

        {!isEdit && (
          <div className="form-field">
            <span className="form-label">策略类型</span>
            {strategyPermissionError && (
              <div className="form-hint form-hint-warn">{STRATEGY_NOT_AUTHORIZED_MESSAGE}</div>
            )}
            <div className="type-toggle">
              <button
                type="button"
                hidden={!selectableStrategyTypeOptions.some((option) => option.type === 'flat')}
                className={`type-toggle-btn ${type === 'flat' ? 'type-toggle-active' : ''}`}
                onClick={() => handleStrategyTypeSelect('flat')}
                disabled={submitting}
              >
                平注
              </button>
              <button
                type="button"
                hidden={!selectableStrategyTypeOptions.some((option) => option.type === 'martin')}
                className={`type-toggle-btn ${type === 'martin' ? 'type-toggle-active' : ''}`}
                onClick={() => handleStrategyTypeSelect('martin')}
                disabled={submitting}
              >
                马丁
              </button>
              {!isLuckySb && !dw3Mode && (
                <>
                  <button
                    type="button"
                    hidden={!selectableStrategyTypeOptions.some((option) => option.type === OMISSION_RANDOM_FLAT_TYPE)}
                    className={`type-toggle-btn ${type === OMISSION_RANDOM_FLAT_TYPE ? 'type-toggle-active' : ''}`}
                    onClick={() => handleStrategyTypeSelect(OMISSION_RANDOM_FLAT_TYPE)}
                    disabled={submitting}
                  >
                    遗漏随机平注
                  </button>
                  <button
                    type="button"
                    hidden={!selectableStrategyTypeOptions.some((option) => option.type === OMISSION_RANDOM_MARTIN_TYPE)}
                    className={`type-toggle-btn ${type === OMISSION_RANDOM_MARTIN_TYPE ? 'type-toggle-active' : ''}`}
                    onClick={() => handleStrategyTypeSelect(OMISSION_RANDOM_MARTIN_TYPE)}
                    disabled={submitting}
                  >
                    遗漏随机马丁
                  </button>
                  <button
                    type="button"
                    hidden={!selectableStrategyTypeOptions.some((option) => option.type === AI_RANDOM_FLAT_TYPE)}
                    className={`type-toggle-btn ${type === AI_RANDOM_FLAT_TYPE ? 'type-toggle-active' : ''}`}
                    onClick={() => handleStrategyTypeSelect(AI_RANDOM_FLAT_TYPE)}
                    disabled={submitting}
                  >
                    AI推荐平注
                  </button>
                  <button
                    type="button"
                    hidden={!selectableStrategyTypeOptions.some((option) => option.type === AI_RANDOM_MARTIN_TYPE)}
                    className={`type-toggle-btn ${type === AI_RANDOM_MARTIN_TYPE ? 'type-toggle-active' : ''}`}
                    onClick={() => handleStrategyTypeSelect(AI_RANDOM_MARTIN_TYPE)}
                    disabled={submitting}
                  >
                    AI推荐马丁
                  </button>
                  <button
                    type="button"
                    hidden={!selectableStrategyTypeOptions.some((option) => option.type === AI_SAME_RANDOM_FLAT_TYPE)}
                    className={`type-toggle-btn ${type === AI_SAME_RANDOM_FLAT_TYPE ? 'type-toggle-active' : ''}`}
                    onClick={() => handleStrategyTypeSelect(AI_SAME_RANDOM_FLAT_TYPE)}
                    disabled={submitting}
                  >
                    AI推荐同号平注
                  </button>
                  <button
                    type="button"
                    hidden={!selectableStrategyTypeOptions.some((option) => option.type === AI_SAME_RANDOM_MARTIN_TYPE)}
                    className={`type-toggle-btn ${type === AI_SAME_RANDOM_MARTIN_TYPE ? 'type-toggle-active' : ''}`}
                    onClick={() => handleStrategyTypeSelect(AI_SAME_RANDOM_MARTIN_TYPE)}
                    disabled={submitting}
                  >
                    AI推荐同号平注马丁
                  </button>
                  <button
                    type="button"
                    hidden={!selectableStrategyTypeOptions.some((option) => option.type === RED_WAVE_DOUBLE_TYPE)}
                    className={`type-toggle-btn ${type === RED_WAVE_DOUBLE_TYPE ? 'type-toggle-active' : ''}`}
                    onClick={() => {
                      setType(RED_WAVE_DOUBLE_TYPE);
                      setWaveDirections((current) => {
                        const normalized = normalizeWaveDirections(RED_WAVE_DOUBLE_TYPE, current);
                        return normalized.length > 0 ? normalized : getWaveDirectionDefault(RED_WAVE_DOUBLE_TYPE);
                      });
                    }}
                    disabled={submitting}
                  >
                    {BUTTON_RED_WAVE}
                  </button>
                  <button
                    type="button"
                    hidden={!selectableStrategyTypeOptions.some((option) => option.type === GREEN_WAVE_SINGLE_TYPE)}
                    className={`type-toggle-btn ${type === GREEN_WAVE_SINGLE_TYPE ? 'type-toggle-active' : ''}`}
                    onClick={() => {
                      setType(GREEN_WAVE_SINGLE_TYPE);
                      setWaveDirections((current) => {
                        const normalized = normalizeWaveDirections(GREEN_WAVE_SINGLE_TYPE, current);
                        return normalized.length > 0 ? normalized : getWaveDirectionDefault(GREEN_WAVE_SINGLE_TYPE);
                      });
                    }}
                    disabled={submitting}
                  >
                    绿波追单
                  </button>
                </>
              )}
            </div>
          </div>
        )}

        {dw3Mode && (
          <>
            <div className="form-field">
              <span className="form-label">{DW3_DISPLAY_NAME}组合</span>
              <div className="type-toggle">
                {dw3Tabs.map((tab) => (
                  <button
                    key={tab.key}
                    type="button"
                    className={`type-toggle-btn ${dw3ActiveTab === tab.key ? 'type-toggle-active' : ''}`}
                    onClick={() => setDw3ActiveTab(tab.key)}
                    disabled={submitting}
                  >
                    {tab.label}
                  </button>
                ))}
              </div>
              <div className="direction-grid">
                {activeDw3Tab.groups.map((group) => {
                  const checked = dw3ActiveTab === 'bs'
                    ? dw3SelectedBs.includes(group.token)
                    : dw3SelectedOe.includes(group.token);
                  return (
                    <label key={group.token} className="direction-option">
                      <input
                        type="checkbox"
                        className="direction-checkbox"
                        checked={checked}
                        onChange={() => toggleDw3Group(group.token)}
                        disabled={submitting}
                      />
                      <span className="direction-text">{group.label}</span>
                    </label>
                  );
                })}
              </div>
              <div className="form-hint">
                已选大小组合：{dw3SelectedBs.map(getDw3GroupLabel).join('、') || '-'}
              </div>
              <div className="form-hint">
                已选单双组合：{dw3SelectedOe.map(getDw3GroupLabel).join('、') || '-'}
              </div>
              <div className="form-hint">
                理论最大投注额：{dw3TheoreticalMaxStake.toFixed(2)}
              </div>
            </div>

            <div className="form-field">
              <label htmlFor="sf-gate-window-issues" className="form-label">最近期数</label>
              <input
                id="sf-gate-window-issues"
                type="number"
                className="form-input"
                inputMode="numeric"
                value={gateWindowIssues}
                onChange={(e) => setGateWindowIssues(e.target.value)}
                min="1"
                step="1"
                disabled={submitting}
              />
              <div className="form-hint">最近 N 期用于{DW3_DISPLAY_NAME}阀门过滤。</div>
            </div>
          </>
        )}

        {isWaveStrategy && activeWaveStrategyType && !dw3Mode && (
          <div className="form-field">
            <span className="form-label">{getWaveDirectionLabel(activeWaveStrategyType)}</span>
            <div className="direction-grid">
              {getWaveDirectionOptions(activeWaveStrategyType).map((option) => {
                const checked = waveDirections.includes(option.code);
                return (
                  <label key={option.code} className="direction-option">
                    <input
                      type="checkbox"
                      className="direction-checkbox"
                      checked={checked}
                      onChange={() => toggleWaveDirection(option.code)}
                      disabled={submitting}
                    />
                    <span className="direction-text">{option.label}</span>
                  </label>
                );
              })}
            </div>
          </div>
        )}

        {isRandomPickStrategy && !dw3Mode && !isLuckySb && (
          <>
            <div className="form-field">
              <span className="form-label">随机选号类型</span>
              <div className="direction-grid">
                {randomPickCategoryOptions.map((option) => {
                  const checked = omissionRandomCategories.includes(option.key);
                  return (
                    <label key={option.key} className="direction-option">
                      <input
                        type="checkbox"
                        className="direction-checkbox"
                        checked={checked}
                        onChange={() => toggleOmissionRandomCategory(option.key)}
                        disabled={submitting}
                      />
                      <span className="direction-text">{option.label}</span>
                    </label>
                  );
                })}
              </div>
            </div>

            <div className="form-field">
              <span className="form-label">每类产出号码数</span>
              <div className="type-toggle">
                {OMISSION_RANDOM_PICK_COUNTS.map((count) => (
                  <button
                    key={count}
                    type="button"
                    className={`type-toggle-btn ${omissionRandomPickCount === count ? 'type-toggle-active' : ''}`}
                    onClick={() => setOmissionRandomPickCount(count)}
                    disabled={submitting}
                  >
                    {count}个
                  </button>
                ))}
              </div>
              <div className="form-hint">
                {isAiRandomStrategy
                  ? '每个已选类型纯随机产出号码，不使用遗漏或概率权重。'
                  : '每个已选类型固定3个按遗漏权重产出，其余号码随机产出。'}
              </div>
            </div>
          </>
        )}

        {!isEdit && isLuckySb && !dw3Mode && (
          <div className="form-field">
            <label className="form-label">极速飞艇玩法</label>
            <PlayCodeSelect
              value={luckySbPlayCode}
              onChange={setLuckySbPlayCode}
              disabled={submitting}
              platformType={LUCKYSB_PLATFORM_TYPE}
            />
          </div>
        )}

        {!isEdit && !isLuckySb && !isWaveStrategy && !isRandomPickStrategy && !dw3Mode && (
          <div className="form-field">
            <label className="form-label">玩法</label>
            <PlayCodeMultiSelect
              value={playCode}
              onChange={setPlayCode}
              disabled={submitting}
            />
          </div>
        )}

        <div className="form-field">
          <label htmlFor="sf-platform" className="form-label">{LABEL_PLATFORM}</label>
          <select
            id="sf-platform"
            className="form-select"
            value={displayedPlatformType}
            onChange={(e) => applyPlatformChange(e.target.value)}
            disabled={submitting || Boolean(verificationGateError) || effectivePlatformTypes.length <= 1}
          >
            {effectivePlatformTypes.length === 0 ? (
              <option value="">暂无可用盘口</option>
            ) : (
              effectivePlatformTypes.map((item) => (
                <option key={item} value={item}>
                  {getStrategyPlatformLabel(item)}
                </option>
              ))
            )}
          </select>
        </div>

        <div className="form-field">
          <label htmlFor="sf-amount" className="form-label">{LABEL_AMOUNT}</label>
          <input
            id="sf-amount"
            type="number"
            className="form-input"
            inputMode="decimal"
            value={baseAmount}
            onChange={(e) => setBaseAmount(e.target.value)}
            placeholder="1"
            min="1"
            step="0.01"
            disabled={submitting}
          />
        </div>

        {isMartinLike(type) && (
          <div className="form-field">
            <label htmlFor="sf-martin" className="form-label">马丁序列</label>
            <input
              id="sf-martin"
              type="text"
              className="form-input"
              value={martinSequence}
              onChange={(e) => setMartinSequence(e.target.value)}
              placeholder="1,2,4,8,16"
              disabled={submitting}
              autoComplete="off"
            />
          </div>
        )}

        <div className="form-field">
          <label htmlFor="sf-timing" className="form-label">下注时机（秒）</label>
          <input
            id="sf-timing"
            type="number"
            className="form-input"
            inputMode="numeric"
            value={betTiming}
            onChange={(e) => {
              setBetTimingTouched(true);
              setBetTiming(e.target.value);
            }}
            min={BET_TIMING_MIN}
            max={BET_TIMING_MAX}
            disabled={submitting}
          />
        </div>

        {isEdit && <div className="form-field">
          <div className="toggle-row">
            <span className="form-label">模拟模式</span>
            <button
              type="button"
              role="switch"
              aria-checked={simulation}
              className="sim-switch"
              onClick={() => setSimulation(!simulation)}
              disabled={submitting}
            >
              <span className="sim-switch-knob" />
            </button>
          </div>
        </div>}

        <div className="form-field">
          <label htmlFor="sf-stoploss" className="form-label">止损金额</label>
          <input
            id="sf-stoploss"
            type="number"
            className="form-input"
            inputMode="decimal"
            value={stopLoss}
            onChange={(e) => setStopLoss(e.target.value)}
            placeholder="选填"
            min="0.01"
            step="0.01"
            disabled={submitting}
          />
        </div>

        <div className="form-field">
          <label htmlFor="sf-takeprofit" className="form-label">止盈金额</label>
          <input
            id="sf-takeprofit"
            type="number"
            className="form-input"
            inputMode="decimal"
            value={takeProfit}
            onChange={(e) => setTakeProfit(e.target.value)}
            placeholder="选填"
            min="0.01"
            step="0.01"
            disabled={submitting}
          />
        </div>

        <div className="form-actions">
          <button type="button" className="form-cancel-btn" onClick={onCancel} disabled={submitting}>
            取消
          </button>
          <button type="submit" className="form-submit-btn" disabled={submitDisabled}>
            {submitting ? '提交中...' : isEdit ? '更新' : BUTTON_CREATE}
          </button>
        </div>
      </form>
    </div>
  );
}
