import { useState } from "react";
import { Link } from "react-router-dom";
import type { ProductSummary } from "../types";
import { SafeImage } from "./SafeImage";
import { PriceBlock } from "./PriceBlock";

export function ProductCard({ product }: { product: ProductSummary }) {
  // the hover photo is layered OVER the primary instead of swapping src, so
  // the card never goes blank while it downloads and a dead url (extracted
  // from the wild) just means the primary stays visible. The layer mounts on
  // first hover to avoid downloading 50 full-res second photos upfront.
  const [hoverStarted, setHoverStarted] = useState(false);
  const [hoverBroken, setHoverBroken] = useState(false);
  // some CDNs block one rendition as an <img> while serving another (mattel's
  // _348x thumb hotlink-blocks; the full-size loads), so a failed primary
  // retries with the second photo before falling back to the placeholder
  const [primarySrc, setPrimarySrc] = useState(product.image_url);
  const onSale =
    product.price.compare_at_price !== null &&
    product.price.compare_at_price > product.price.price;

  return (
    <Link
      to={`/product/${product.id}`}
      className="group block focus-visible:outline-2 focus-visible:outline-offset-4 focus-visible:outline-ink"
      onMouseEnter={() => setHoverStarted(true)}
    >
      <div className="relative aspect-[4/5] overflow-hidden bg-surface">
        <SafeImage
          src={primarySrc}
          alt={product.name}
          brand={product.brand}
          className="h-full w-full object-contain transition-transform duration-300 group-hover:scale-[1.02]"
          onBroken={() => {
            if (product.hover_image_url && primarySrc !== product.hover_image_url)
              setPrimarySrc(product.hover_image_url);
          }}
        />
        {hoverStarted && !hoverBroken && product.hover_image_url && (
          <img
            src={product.hover_image_url}
            alt=""
            className="absolute inset-0 h-full w-full object-contain opacity-0 transition-opacity duration-300 group-hover:opacity-100"
            onError={() => setHoverBroken(true)}
          />
        )}
        {onSale && (
          <span className="absolute top-3 left-3 bg-ink px-2 py-0.5 text-[11px] font-medium uppercase tracking-[0.08em] text-white">
            Sale
          </span>
        )}
      </div>
      <div className="mt-3 space-y-1">
        <p className="eyebrow">{product.brand}</p>
        <h3 className="text-sm leading-snug font-medium">{product.name}</h3>
        <PriceBlock price={product.price} compact />
      </div>
    </Link>
  );
}
