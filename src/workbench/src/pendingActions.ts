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
