"use client";

import { createContext, useCallback, useContext, useMemo, useState } from "react";

type Kind = "success" | "error" | "info";
interface Toast { id: number; kind: Kind; text: string }

const Ctx = createContext<{ push: (kind: Kind, text: string) => void } | null>(null);

export function ToastProvider({ children }: { children: React.ReactNode }) {
  const [items, setItems] = useState<Toast[]>([]);

  const push = useCallback((kind: Kind, text: string) => {
    const id = Date.now() + Math.random();
    setItems((x) => [...x, { id, kind, text }]);
    setTimeout(() => setItems((x) => x.filter((t) => t.id !== id)), 4500);
  }, []);

  const value = useMemo(() => ({ push }), [push]);

  const tone: Record<Kind, string> = {
    success: "border-up/40 bg-up/10 text-up",
    error: "border-down/40 bg-down/10 text-down",
    info: "border-line bg-panel2 text-ink",
  };

  return (
    <Ctx.Provider value={value}>
      {children}
      <div className="pointer-events-none fixed bottom-4 right-4 z-50 flex w-80 flex-col gap-2">
        {items.map((t) => (
          <div key={t.id} className={`pointer-events-auto rounded-md border px-3 py-2 text-sm shadow-lg ${tone[t.kind]}`}>
            {t.text}
          </div>
        ))}
      </div>
    </Ctx.Provider>
  );
}

export function useToast() {
  const ctx = useContext(Ctx);
  if (!ctx) throw new Error("useToast must be used inside <ToastProvider>");
  return ctx;
}
