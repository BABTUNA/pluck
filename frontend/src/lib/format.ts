// currency comes from extraction as an ISO code; Intl handles the locale
// conventions (JPY has no decimals, SEK puts the symbol after)
export function formatPrice(amount: number, currency: string): string {
  try {
    return new Intl.NumberFormat(undefined, {
      style: "currency",
      currency,
      currencyDisplay: "narrowSymbol",
    }).format(amount);
  } catch {
    return `${amount} ${currency}`; // unknown code: show it rather than crash
  }
}

export function percentOff(price: number, compareAt: number): number {
  return Math.round((1 - price / compareAt) * 100);
}
