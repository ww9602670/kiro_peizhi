import { useEffect, useState } from 'react';
import { fetchCurrentInstall } from '@/api/lottery';
import { isApiError } from '@/api/request';
import { DEFAULT_CURRENT_INSTALL, type CurrentInstall } from '@/types/api/lottery';

const RETRY_INTERVAL_MS = 5000;
const DRAW_PENDING_POLL_INTERVAL_MS = 5000;
const POST_DRAW_REFRESH_DELAY_MS = 10000;
const DRAW_WAIT_RETRY_DEFAULT_DELAY_MS = 10000;
const TICK_INTERVAL_MS = 1000;

export interface UseLotteryCountdownOptions {
  platformType?: string;
}

interface CountdownSnapshot {
  data: CurrentInstall | null;
  closeCountdown: number;
  openCountdown: number;
  error: string | null;
  lastUpdateTime: Date | null;
}

type Listener = (snapshot: CountdownSnapshot) => void;

class CountdownStore {
  private snapshot: CountdownSnapshot = {
    data: null,
    closeCountdown: 0,
    openCountdown: 0,
    error: null,
    lastUpdateTime: null,
  };

  private readonly listeners = new Set<Listener>();
  private readonly platformType: string;
  private tickHandle: ReturnType<typeof setInterval> | null = null;
  private refreshHandle: ReturnType<typeof setTimeout> | null = null;
  private started = false;
  private fetching = false;
  private drawRefreshScheduled = false;

  constructor(platformType: string) {
    this.platformType = platformType;
  }

  destroy() {
    if (this.tickHandle) {
      clearInterval(this.tickHandle);
      this.tickHandle = null;
    }
    if (this.refreshHandle) {
      clearTimeout(this.refreshHandle);
      this.refreshHandle = null;
    }
    this.listeners.clear();
    this.started = false;
    this.fetching = false;
    this.drawRefreshScheduled = false;
    this.snapshot = {
      data: null,
      closeCountdown: 0,
      openCountdown: 0,
      error: null,
      lastUpdateTime: null,
    };
  }

  subscribe(listener: Listener) {
    this.listeners.add(listener);
    listener(this.snapshot);
    if (!this.started) {
      this.start();
    }
    return () => {
      this.listeners.delete(listener);
    };
  }

  private emit() {
    for (const listener of this.listeners) {
      listener(this.snapshot);
    }
  }

  private setSnapshot(nextSnapshot: CountdownSnapshot) {
    this.snapshot = nextSnapshot;
    this.emit();
  }

  private hasUsableSnapshot(data: CurrentInstall | null): boolean {
    if (!data) {
      return false;
    }

    if (data.installments?.trim()) {
      return true;
    }

    if (data.pre_installments?.trim()) {
      return true;
    }

    return data.close_countdown_sec > 0 || data.open_countdown_sec > 0;
  }

  private resolveDrawWaitRetryDelayMs(data: CurrentInstall): number {
    const retryAt = data.next_draw_retry_at?.trim();
    if (!retryAt) {
      return DRAW_WAIT_RETRY_DEFAULT_DELAY_MS;
    }
    const parsed = new Date(retryAt).getTime();
    if (!Number.isFinite(parsed)) {
      return DRAW_WAIT_RETRY_DEFAULT_DELAY_MS;
    }
    const deltaMs = parsed - Date.now();
    if (deltaMs <= 0) {
      return DRAW_WAIT_RETRY_DEFAULT_DELAY_MS;
    }
    return deltaMs;
  }

  private scheduleNextFetch(nextInstall: CurrentInstall) {
    if (nextInstall.market_data_state === 'market_closed') {
      return;
    }

    if (nextInstall.draw_state === 'draw_pending') {
      this.scheduleFetch(DRAW_PENDING_POLL_INTERVAL_MS);
      return;
    }

    if (nextInstall.draw_state === 'draw_wait_retry') {
      this.scheduleFetch(this.resolveDrawWaitRetryDelayMs(nextInstall));
      return;
    }

    if (!this.hasUsableSnapshot(nextInstall)) {
      this.scheduleFetch(RETRY_INTERVAL_MS);
      return;
    }

    if (nextInstall.open_countdown_sec <= 0) {
      // Usable snapshot already at zero-countdown: draw has already finished.
      // Schedule the post-draw refresh so the UI can advance to the next issue
      // instead of freezing on a stale zero-countdown snapshot.
      this.scheduleFetch(POST_DRAW_REFRESH_DELAY_MS);
      this.drawRefreshScheduled = true;
    }
  }

  private start() {
    this.started = true;
    this.tickHandle = setInterval(() => {
      const nextClose = Math.max(0, this.snapshot.closeCountdown - 1);
      const nextOpen = Math.max(0, this.snapshot.openCountdown - 1);
      const openReachedZero = this.snapshot.openCountdown > 0 && nextOpen === 0;

      if (
        nextClose !== this.snapshot.closeCountdown ||
        nextOpen !== this.snapshot.openCountdown
      ) {
        this.setSnapshot({
          ...this.snapshot,
          closeCountdown: nextClose,
          openCountdown: nextOpen,
        });
      }

      if (openReachedZero && !this.fetching && !this.drawRefreshScheduled) {
        this.scheduleFetch(POST_DRAW_REFRESH_DELAY_MS);
        this.drawRefreshScheduled = true;
      }
    }, TICK_INTERVAL_MS);

    void this.fetchNow();
  }

  private scheduleFetch(delayMs: number) {
    if (this.refreshHandle) {
      clearTimeout(this.refreshHandle);
    }
    this.refreshHandle = setTimeout(() => {
      this.refreshHandle = null;
      void this.fetchNow();
    }, delayMs);
  }

  private async fetchNow() {
    if (this.fetching) {
      return;
    }

    this.fetching = true;
    this.drawRefreshScheduled = false;

    try {
      const response = await fetchCurrentInstall(this.platformType);
      const nextInstall = response.data ?? DEFAULT_CURRENT_INSTALL;
      this.setSnapshot({
        data: nextInstall,
        closeCountdown: nextInstall.close_countdown_sec,
        openCountdown: nextInstall.open_countdown_sec,
        error: null,
        lastUpdateTime: new Date(),
      });
      this.scheduleNextFetch(nextInstall);
    } catch (err) {
      const message = isApiError(err) ? err.message || '数据延迟' : '数据延迟';
      this.setSnapshot({
        ...this.snapshot,
        error: message,
      });
      this.scheduleFetch(RETRY_INTERVAL_MS);
    } finally {
      this.fetching = false;
    }
  }
}

const storeRegistry = new Map<string, CountdownStore>();

function getStore(platformType: string): CountdownStore {
  const normalizedPlatformType = platformType.trim().toUpperCase() || 'JND28WEB';
  let store = storeRegistry.get(normalizedPlatformType);
  if (!store) {
    store = new CountdownStore(normalizedPlatformType);
    storeRegistry.set(normalizedPlatformType, store);
  }
  return store;
}

export function useLotteryCountdown(options?: UseLotteryCountdownOptions) {
  const platformType = options?.platformType ?? 'JND28WEB';
  const [snapshot, setSnapshot] = useState<CountdownSnapshot>({
    data: null,
    closeCountdown: 0,
    openCountdown: 0,
    error: null,
    lastUpdateTime: null,
  });

  useEffect(() => {
    const store = getStore(platformType);
    return store.subscribe(setSnapshot);
  }, [platformType]);

  return {
    data: snapshot.data,
    closeCountdown: snapshot.closeCountdown,
    openCountdown: snapshot.openCountdown,
    closeTimestamp: snapshot.closeCountdown,
    openTimestamp: snapshot.openCountdown,
    error: snapshot.error,
    lastUpdateTime: snapshot.lastUpdateTime,
  };
}

export function __resetLotteryCountdownStoresForTest() {
  for (const store of storeRegistry.values()) {
    store.destroy();
  }
  storeRegistry.clear();
}
