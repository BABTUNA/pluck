// mirrors the api, the frontend consumes crawl output verbatim

export interface Price {
  price: number;
  currency: string;
  compare_at_price: number | null;
}

export interface ProductSummary {
  id: string;
  name: string;
  brand: string;
  price: Price;
  image_url: string | null;
  hover_image_url: string | null;
  category: string;
  source: string;
}

export interface Variant {
  name: string;
  price: number | null;
  compare_at: number | null;
  available?: boolean;
}

export interface Product {
  id: string;
  url: string;
  name: string;
  brand: string | null;
  description: string | null;
  variants: Variant[];
  price: Price;
  image_urls: string[];
  category: string | null;
  sources: Record<string, string>;
  latency_s: number;
  worker: string | null;
  processed_at: string;
}

export interface Progress {
  done: number;
  queued: number;
  leased: number;
  dead: number;
  total: number;
}
