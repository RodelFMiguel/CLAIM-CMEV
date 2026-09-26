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
