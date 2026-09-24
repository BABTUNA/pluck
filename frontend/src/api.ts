import type { Product, ProductSummary, Progress } from "./types";

// same origin in prod, override for local dev against the deployed api
const BASE = import.meta.env.VITE_API_URL ?? "";

async function get<T>(path: string): Promise<T> {
  const res = await fetch(`${BASE}${path}`);
  if (!res.ok) throw new Error(`${res.status} on ${path}`);
  return res.json();
}

export const fetchProducts = () => get<ProductSummary[]>("/products");
export const fetchProduct = (id: string) => get<Product>(`/products/${id}`);
export const fetchProgress = () => get<Progress>("/progress");
