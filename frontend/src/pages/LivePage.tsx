import { useEffect, useRef, useState } from "react";
import type { ProductSummary, Progress } from "../types";
import { fetchProducts, fetchProgress } from "../api";
import { ProductCard } from "../components/ProductCard";

// the live view polls the queue and the catalog so new products pop in as
// workers finish them and the bar tracks the crawl frontier
export function LivePage() {
  const [progress, setProgress] = useState<Progress | null>(null);
  const [products, setProducts] = useState<ProductSummary[]>([]);
  const [fresh, setFresh] = useState<Set<string>>(new Set());
  const known = useRef<Set<string>>(new Set());

  useEffect(() => {
    let alive = true;
    const tick = async () => {
      try {
        const [prog, prods] = await Promise.all([fetchProgress(), fetchProducts()]);
        if (!alive) return;
        setProgress(prog);
        const newest = prods.slice(0, 24);
        const arrivals = new Set(
          newest.filter((p) => known.current.size && !known.current.has(p.id)).map((p) => p.id),
        );
        newest.forEach((p) => known.current.add(p.id));
        if (arrivals.size) setFresh(arrivals);
        setProducts(newest);
      } catch {
        /* keep polling */
      }
    };
    tick();
    const t = setInterval(tick, 2500);
    return () => {
      alive = false;
      clearInterval(t);
    };
  }, []);

  const pct = progress && progress.total > 0 ? (progress.done / progress.total) * 100 : 0;

  return (
    <main className="mx-auto max-w-6xl px-4 pb-24">
      <header className="py-10">
        <p className="eyebrow">Live extraction</p>
        <h1 className="font-display mt-1 text-3xl font-medium tracking-tight">
          Workers on the queue
        </h1>
      </header>

      {progress && (
        <section className="mb-10 space-y-3">
          <div className="flex items-baseline justify-between text-sm">
            <span className="font-medium">
              {progress.queued + progress.leased === 0
                ? `Crawl complete: ${progress.done} pages extracted`
                : `${progress.done} of ${progress.total} pages extracted`}
            </span>
            <span className="text-muted">
              {progress.queued} queued · {progress.leased} in flight · {progress.dead} dead-lettered
            </span>
          </div>
          <div className="h-2 w-full bg-surface">
            <div
              className="h-2 bg-ink transition-all duration-700"
              style={{ width: `${pct}%` }}
            />
          </div>
        </section>
      )}

      <section>
        <p className="eyebrow mb-4">Latest products</p>
        <div className="grid grid-cols-2 gap-x-4 gap-y-10 sm:grid-cols-3 lg:grid-cols-4">
          {products.map((p) => (
            <div
              key={p.id}
              className={fresh.has(p.id) ? "animate-[fadeIn_0.8s_ease-out]" : ""}
            >
              <ProductCard product={p} />
            </div>
          ))}
        </div>
        {!products.length && (
          <p className="text-muted text-sm">Nothing extracted yet. Seed the queue and start a worker.</p>
        )}
      </section>
    </main>
  );
}
