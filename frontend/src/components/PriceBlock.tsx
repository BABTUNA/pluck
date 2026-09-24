import type { Price } from "../types";
import { formatPrice, percentOff } from "../lib/format";

interface Props {
  price: Price;
  override?: number | null; // resolved variant price, when one is selected
  compact?: boolean;
}

export function PriceBlock({ price, override, compact }: Props) {
  const current = override ?? price.price;
  const compareAt = price.compare_at_price;
  const onSale = compareAt !== null && compareAt > current;

  return (
    <div className={`flex items-baseline gap-2 ${compact ? "text-sm" : "text-xl"}`}>
      <span className="font-medium">{formatPrice(current, price.currency)}</span>
      {onSale && (
        <>
          <span className="text-faint line-through">{formatPrice(compareAt, price.currency)}</span>
          {!compact && (
            <span className="text-sale text-sm font-medium">{percentOff(current, compareAt)}% off</span>
          )}
        </>
      )}
    </div>
  );
}
