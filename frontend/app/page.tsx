import SymbolTable from "@/components/SymbolTable";
import { Card } from "@/components/ui";

const STEPS = [
  { n: 1, title: "Create an account", body: "Sign up instantly and get your own trader identity." },
  { n: 2, title: "Deposit funds", body: "Add virtual USD to your wallet to build buying power." },
  { n: 3, title: "Start trading", body: "Place limit and market orders on the matching engine." },
];

export default function Home() {
  return (
    <div className="space-y-10">
      <div className="text-center">
        <h1 className="text-3xl font-bold text-ink">Mini Stock Exchange</h1>
        <p className="mx-auto mt-2 max-w-2xl text-sm text-muted">
          A microservices-based paper trading exchange. Trade live market data, manage your
          portfolio, and test strategies with zero real-money risk.
        </p>
      </div>

      <SymbolTable />

      <div className="grid grid-cols-1 gap-4 border-t border-line pt-8 md:grid-cols-3">
        {STEPS.map((step) => (
          <Card key={step.n} className="text-center">
            <div className="mx-auto mb-3 flex h-10 w-10 items-center justify-center rounded-full bg-accent/15 text-sm font-bold text-accent">
              {step.n}
            </div>
            <h3 className="mb-1 text-sm font-semibold text-ink">{step.title}</h3>
            <p className="text-xs text-muted">{step.body}</p>
          </Card>
        ))}
      </div>
    </div>
  );
}
