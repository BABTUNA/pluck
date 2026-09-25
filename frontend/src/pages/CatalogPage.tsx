import { useEffect, useMemo, useState } from "react";
import type { ProductSummary } from "../types";
import { fetchProducts } from "../api";
import { ProductCard } from "../components/ProductCard";

// three collections: the assignment's 50 pages, its original 5, and the live crawl
const BATCHES = [
  { key: "fifty", label: "Original 50" },
  { key: "five", label: "Original 5" },
  { key: "live", label: "Live crawl" },
] as const;
type Batch = (typeof BATCHES)[number]["key"];

export function CatalogPage() {
  const [products, setProducts] = useState<ProductSummary[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [batch, setBatch] = useState<Batch>("fifty");
  const [query, setQuery] = useState("");

  useEffect(() => {
    setProducts(null);
    fetchProducts(batch).then(setProducts).catch((e) => setError(String(e)));
  }, [batch]);

  const visible = useMemo(() => {
    if (!products) return [];
    const q = query.trim().toLowerCase();
    return products.filter((p) => !q || `${p.name} ${p.brand}`.toLowerCase().includes(q));
  }, [products, query]);

  return (
    <main className="mx-auto max-w-6xl px-4 pb-24">
      <header className="flex flex-col gap-6 py-10 sm:flex-row sm:items-end sm:justify-between">
        <div>
          <p className="eyebrow">Catalog</p>
          <h1 className="font-display mt-1 text-3xl font-medium tracking-tight">
            All products
          </h1>
        </div>
        <div className="flex flex-wrap items-center gap-3">
          <div className="flex border border-line">
            {BATCHES.map((b) => (
              <button
                key={b.key}
                onClick={() => setBatch(b.key)}
                className={`px-3 py-1.5 text-sm transition-colors ${
                  batch === b.key ? "bg-ink text-white" : "text-muted hover:text-ink"
                }`}
              >
                {b.label}
              </button>
            ))}
          </div>
          <input
            type="search"
            placeholder="Search products, brands..."
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            className="border border-line bg-transparent px-3 py-1.5 text-sm outline-none placeholder:text-faint focus:border-ink"
          />
        </div>
      </header>

      {error && <p className="text-muted">{error}</p>}
      {!error && !products && (
        <div className="grid grid-cols-2 gap-x-4 gap-y-10 sm:grid-cols-3 lg:grid-cols-4">
          {Array.from({ length: 8 }).map((_, i) => (
            <div key={i} className="aspect-[4/5] animate-pulse bg-surface" />
          ))}
        </div>
      )}
      {products && (
        <div className="grid grid-cols-2 gap-x-4 gap-y-10 sm:grid-cols-3 lg:grid-cols-4">
          {visible.map((p) => (
            <ProductCard key={p.id} product={p} />
          ))}
        </div>
      )}
      {products && !visible.length && (
        <p className="text-muted text-sm">No products match.</p>
      )}
    </main>
  );
}
