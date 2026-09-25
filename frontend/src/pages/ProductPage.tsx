import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import type { Product } from "../types";
import { fetchProduct } from "../api";
import { Gallery } from "../components/Gallery";
import { PriceBlock } from "../components/PriceBlock";
import { formatPrice } from "../lib/format";

// short provenance tags, the values themselves are the star
const SOURCE_LABEL: Record<string, string> = {
  declared: "page data",
  shipped: "embedded state",
  computed: "page js",
  inferred: "model",
  none: "",
};

export function ProductPage() {
  const { id } = useParams();
  return <ProductContent key={id} id={id} />;
}

function ProductContent({ id }: { id: string | undefined }) {
  const [product, setProduct] = useState<Product | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!id) return;
    let active = true;
    fetchProduct(id)
      .then((value) => active && setProduct(value))
      .catch((e) => active && setError(String(e)));
    return () => {
      active = false;
    };
  }, [id]);

  if (error)
    return (
      <main className="mx-auto max-w-6xl px-4 py-24 text-center text-muted">
        <p>Product not found.</p>
        <Link to="/" className="mt-2 inline-block underline">Back to catalog</Link>
      </main>
    );
  if (!product)
    return (
      <main className="mx-auto max-w-6xl animate-pulse px-4 py-10">
        <div className="grid gap-10 lg:grid-cols-2">
          <div className="aspect-[4/5] bg-surface" />
          <div className="space-y-4">
            <div className="h-3 w-24 bg-surface" />
            <div className="h-8 w-3/4 bg-surface" />
            <div className="h-5 w-32 bg-surface" />
          </div>
        </div>
      </main>
    );

  const brand = product.brand ?? product.url.split("/")[2]?.replace(/^www\./, "") ?? "";
  const crumbs = (product.category ?? "Uncategorized").split(" > ");

  return (
    <main className="mx-auto max-w-6xl px-4 pb-24">
      <nav className="flex flex-wrap items-center gap-1.5 py-6 text-xs text-muted">
        <Link to="/" className="hover:text-ink">Catalog</Link>
        {crumbs.map((c, i) => (
          <span key={i} className="flex items-center gap-1.5">
            <span className="text-faint">/</span>
            <span className={i === crumbs.length - 1 ? "text-ink" : ""}>{c}</span>
          </span>
        ))}
      </nav>

      <div className="grid gap-10 lg:grid-cols-2">
        <div className="min-w-0">
          <Gallery
            images={product.image_urls}
            videoUrl={null}
            name={product.name}
            brand={brand}
            jumpToUrl={null}
          />
        </div>

        <div>
          <p className="eyebrow">{brand}</p>
          <h1 className="font-display mt-1 text-3xl font-medium tracking-tight">
            {product.name}
          </h1>
          <div className="mt-4">
            <PriceBlock price={product.price} />
          </div>

          {product.description && (
            <p className="mt-5 max-w-prose text-sm leading-relaxed text-muted">
              {product.description}
            </p>
          )}

          {product.variants.length > 0 && (
            <section className="mt-8">
              <p className="eyebrow mb-3">Variants</p>
              <div className="flex flex-wrap gap-2">
                {product.variants.map((v, i) => (
                  <span
                    key={i}
                    className={`border border-line px-3 py-1.5 text-xs ${
                      v.available === false ? "text-faint line-through" : ""
                    }`}
                  >
                    {v.name}
                    {v.price != null && ` · ${formatPrice(v.price, product.price.currency)}`}
                  </span>
                ))}
              </div>
            </section>
          )}

          <a
            href={product.url}
            target="_blank"
            rel="noreferrer"
            className="mt-6 inline-block border border-ink px-4 py-2 text-sm font-medium transition-colors hover:bg-ink hover:text-white"
          >
            View original page
          </a>

          <section className="mt-10">
            <p className="eyebrow mb-3">Extracted data</p>
            <ul className="divide-y divide-line border-y border-line text-sm">
              {[
                ["name", product.name],
                ["brand", product.brand],
                ["price", formatPrice(product.price.price, product.price.currency)],
                ["compare at", product.price.compare_at_price != null
                  ? formatPrice(product.price.compare_at_price, product.price.currency)
                  : null],
                ["currency", product.price.currency],
                ["category", product.category],
              ].map(([field, value]) => {
                const source = product.sources[String(field).replace(" ", "_")] ?? "";
                return (
                  <li key={String(field)} className="flex items-baseline gap-4 py-2.5">
                    <span className="w-24 shrink-0 text-muted">{field}</span>
                    <span className={`min-w-0 flex-1 font-medium ${value ? "" : "text-faint"}`}>
                      {value ?? "—"}
                    </span>
                    {SOURCE_LABEL[source] && (
                      <span className="shrink-0 text-xs text-faint">{SOURCE_LABEL[source]}</span>
                    )}
                  </li>
                );
              })}
            </ul>
            <p className="mt-3 text-xs text-faint">
              Extracted in {product.latency_s}s
            </p>
          </section>
        </div>
      </div>
    </main>
  );
}
