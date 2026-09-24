import { useEffect, useMemo, useState } from "react";
import type { ProductSummary } from "../types";
import { fetchProducts } from "../api";
import { ProductCard } from "../components/ProductCard";

export function CatalogPage() {
  const [products, setProducts] = useState<ProductSummary[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [query, setQuery] = useState("");

  useEffect(() => {
    fetchProducts().then(setProducts).catch((e) => setError(String(e)));
  }, []);

  const visible = useMemo(() => {
    if (!products) return [];
    const q = query.trim().toLowerCase();
    return products.filter(
      (p) =>
        (!q || `${p.name} ${p.brand}`.toLowerCase().includes(q)),
    );
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
          <input
            type="search"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Search products, brands…"
            className="w-56 border border-line px-3 py-1.5 text-sm placeholder:text-faint focus:border-ink focus:outline-none"
          />
        </div>
      </header>

      {error && (
        <p className="py-20 text-center text-muted">
          Couldn't reach the catalog server ({error}). Is `uv run uvicorn server:app` running?
        </p>
      )}

      {!error && !products && (
        <div className="grid grid-cols-2 gap-x-5 gap-y-10 sm:grid-cols-3 lg:grid-cols-4">
          {Array.from({ length: 8 }).map((_, i) => (
            <div key={i} className="animate-pulse">
              <div className="aspect-[4/5] bg-surface" />
              <div className="mt-3 h-3 w-16 bg-surface" />
              <div className="mt-2 h-4 w-40 bg-surface" />
            </div>
          ))}
        </div>
      )}

      {products && visible.length === 0 && (
        <p className="py-20 text-center text-muted">No products match “{query}”.</p>
      )}

      <div className="grid grid-cols-2 gap-x-5 gap-y-10 sm:grid-cols-3 lg:grid-cols-4">
        {visible.map((p) => (
          <ProductCard key={p.id} product={p} />
        ))}
      </div>
    </main>
  );
}
