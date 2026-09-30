// One unresolved request per actor/claim. Keep its payload and key unchanged on retry.
export interface PendingAction {
  key: string;
  path: string;
  payload: unknown;
  actor: string;
  claimId: string;
  success: string;
}
async function database(): Promise<IDBDatabase> {
  return new Promise((resolve, reject) => {
    const r = indexedDB.open("cmev-review", 1);
    r.onupgradeneeded = () => r.result.createObjectStore("local");
    r.onsuccess = () => resolve(r.result);
    r.onerror = () => reject(r.error);
  });
}
export async function localValue<T>(key: string): Promise<T | undefined> {
  const db = await database();
  try {
    return await new Promise<T | undefined>((resolve, reject) => {
      const r = db.transaction("local").objectStore("local").get(key);
      r.onsuccess = () => resolve(r.result);
      r.onerror = () => reject(r.error);
    });
  } finally {
    db.close();
  }
}
export async function storeLocal(key: string, value?: unknown): Promise<void> {
  const db = await database();
  try {
    await new Promise<void>((resolve, reject) => {
      const tx = db.transaction("local", "readwrite");
      if (value === undefined) tx.objectStore("local").delete(key);
      else tx.objectStore("local").put(value, key);
      tx.oncomplete = () => resolve();
      tx.onerror = tx.onabort = () => reject(tx.error);
    });
  } finally {
    db.close();
  }
}

// Compare and update in one transaction: another tab cannot replace or delete
// a request whose acknowledgement is still unknown.
export async function savePending(
  key: string,
  action: PendingAction,
): Promise<void> {
  return changePending(key, action.key, action);
}
export async function removePending(
  key: string,
  actionKey: string,
): Promise<void> {
  return changePending(key, actionKey);
}
async function changePending(
  key: string,
  expected: string,
  value?: PendingAction,
): Promise<void> {
  const db = await database();
  try {
    await new Promise<void>((resolve, reject) => {
      const tx = db.transaction("local", "readwrite");
      const store = tx.objectStore("local");
      const read = store.get(key);
      let conflict = false;
      read.onsuccess = () => {
        if (read.result && read.result.key !== expected) {
          // Removing a request that is no longer in the slot is a no-op: it was
          // already resolved or discarded, and another tab's request must stay.
          if (!value) return;
          conflict = true;
          tx.abort();
        } else if (value) store.put(value, key);
        else store.delete(key);
      };
      tx.oncomplete = () => resolve();
      tx.onerror = tx.onabort = () =>
        reject(
          conflict
            ? new Error(
                "Another tab has a saved request. Refresh to resolve it before submitting this edit. Your form values are retained.",
              )
            : tx.error,
        );
    });
  } finally {
    db.close();
  }
}

// Unsent note and amount drafts, one per actor, claim and browser tab.
export interface Draft {
  note: string;
  amounts: Record<string, string>;
  updatedAt?: number;
}
export const DRAFT_MAX_AGE_MS = 14 * 24 * 60 * 60 * 1000;
export const draftKey = (actor: string, claimId: string, tabId: string) =>
  "draft:" + actor + ":" + claimId + ":" + tabId;
const isEmptyDraft = (d: Draft | undefined) =>
  !d || (!d.note?.trim() && !Object.values(d.amounts ?? {}).some(Boolean));

// Read this tab's draft. Earlier builds stored drafts per claim without a tab
// (and, before that, under an undefined actor). Move one such legacy draft into
// this tab once, in the same transaction, so no other tab adopts it again.
export async function loadDraft(
  actor: string,
  claimId: string,
  tabId: string,
): Promise<Draft | undefined> {
  const db = await database();
  try {
    return await new Promise((resolve, reject) => {
      const tx = db.transaction("local", "readwrite");
      const store = tx.objectStore("local");
      const key = draftKey(actor, claimId, tabId);
      let result: Draft | undefined;
      const read = store.get(key);
      read.onsuccess = () => {
        result = read.result;
        if (!isEmptyDraft(result)) return;
        const legacyKeys = [
          "draft:" + actor + ":" + claimId,
          "draft:undefined:" + claimId,
        ];
        const next = (i: number) => {
          if (i >= legacyKeys.length) return;
          const legacy = store.get(legacyKeys[i]);
          legacy.onsuccess = () => {
            if (isEmptyDraft(legacy.result)) {
              if (legacy.result) store.delete(legacyKeys[i]);
              next(i + 1);
              return;
            }
            result = {
              note: legacy.result.note ?? "",
              amounts: legacy.result.amounts ?? {},
              updatedAt: Date.now(),
            };
            store.put(result, key);
            store.delete(legacyKeys[i]);
          };
        };
        next(0);
      };
      tx.oncomplete = () => resolve(result);
      tx.onerror = tx.onabort = () => reject(tx.error);
    });
  } finally {
    db.close();
  }
}
// An empty draft is removed rather than stored, so visiting a claim leaves no key.
export async function saveDraft(key: string, draft: Draft): Promise<void> {
  return storeLocal(
    key,
    isEmptyDraft(draft) ? undefined : { ...draft, updatedAt: Date.now() },
  );
}
async function liveTabIds(): Promise<Set<string> | null> {
  if (!navigator.locks?.query) return null;
  const { held = [] } = await navigator.locks.query();
  return new Set(
    held
      .map((lock) => lock.name ?? "")
      .filter((name) => name.startsWith("cmev-review-tab:"))
      .map((name) => name.slice("cmev-review-tab:".length)),
  );
}
// Bound per-tab draft growth: drop drafts older than the maximum age, and empty
// drafts of tabs that are no longer open. Unstamped drafts get a timestamp now,
// so they age out later instead of being deleted with unsent work in them.
export async function pruneDrafts(
  keep: string,
  maxAgeMs = DRAFT_MAX_AGE_MS,
): Promise<number> {
  const live = await liveTabIds().catch(() => null);
  const now = Date.now();
  const db = await database();
  try {
    return await new Promise((resolve, reject) => {
      const tx = db.transaction("local", "readwrite");
      let removed = 0;
      const cursor = tx
        .objectStore("local")
        .openCursor(IDBKeyRange.bound("draft:", "draft:￿"));
      cursor.onsuccess = () => {
        const c = cursor.result;
        if (!c) return;
        const key = String(c.key);
        const value = c.value as Draft | undefined;
        const tab = key.split(":")[3];
        if (key !== keep) {
          if (typeof value?.updatedAt !== "number") {
            if (isEmptyDraft(value) && tab && live && !live.has(tab)) {
              c.delete();
              removed++;
            } else c.update({ ...value, updatedAt: now });
          } else if (
            now - value.updatedAt > maxAgeMs ||
            (isEmptyDraft(value) && tab && live && !live.has(tab))
          ) {
            c.delete();
            removed++;
          }
        }
        c.continue();
      };
      tx.oncomplete = () => resolve(removed);
      tx.onerror = tx.onabort = () => reject(tx.error);
    });
  } finally {
    db.close();
  }
}

let tabIdentity: Promise<string> | undefined;
export function reviewTabId(): Promise<string> {
  return (tabIdentity ??= (async () => {
    let id = sessionStorage.getItem("cmev-review-tab") ?? crypto.randomUUID();
    // A duplicated tab can inherit sessionStorage. Hold a window-lifetime lock
    // so its draft namespace is re-keyed without altering the original tab.
    if (navigator.locks) {
      const reserve = (candidate: string) =>
        new Promise<boolean>((resolve, reject) => {
          void navigator.locks
            .request(
              "cmev-review-tab:" + candidate,
              { ifAvailable: true },
              (lock) => {
                resolve(!!lock);
                if (lock)
                  return new Promise<void>((release) =>
                    window.addEventListener("pagehide", () => release(), {
                      once: true,
                    }),
                  );
              },
            )
            .catch(reject);
        });
      while (!(await reserve(id))) id = crypto.randomUUID();
    }
    sessionStorage.setItem("cmev-review-tab", id);
    return id;
  })());
}

export async function loadPending(
  key: string,
  actor: string,
  claimId: string,
): Promise<PendingAction | undefined> {
  const db = await database();
  try {
    return await new Promise((resolve, reject) => {
      const tx = db.transaction("local", "readwrite");
      const store = tx.objectStore("local");
      let result: PendingAction | undefined;
      const read = store.get(key);
      read.onsuccess = () => {
        if (read.result) {
          result = read.result;
          return;
        }
        const legacyKey = "pending:undefined:" + claimId;
        const legacy = store.get(legacyKey);
        legacy.onsuccess = () => {
          if (
            legacy.result &&
            legacy.result.claimId === claimId &&
            !legacy.result.actor
          ) {
            result = { ...legacy.result, actor };
            store.put(result, key);
            store.delete(legacyKey);
          }
        };
      };
      tx.oncomplete = () => resolve(result);
      tx.onerror = tx.onabort = () => reject(tx.error);
    });
  } finally {
    db.close();
  }
}
