import { BrowserRouter, NavLink, Route, Routes } from "react-router-dom";
import { CatalogPage } from "./pages/CatalogPage";
import { ProductPage } from "./pages/ProductPage";
import { LivePage } from "./pages/LivePage";

export default function App() {
  const tab = ({ isActive }: { isActive: boolean }) =>
    `px-3 py-1.5 text-sm transition-colors ${
      isActive ? "bg-ink text-white" : "text-muted hover:text-ink"
    }`;
  return (
    <BrowserRouter>
      <header className="border-b border-line">
        <div className="mx-auto flex max-w-6xl items-center justify-between px-4 py-4">
          <NavLink to="/" className="font-display text-lg font-medium tracking-tight">
            pluck
          </NavLink>
          <nav className="flex border border-line">
            <NavLink to="/" end className={tab}>
              Catalog
            </NavLink>
            <NavLink to="/live" className={tab}>
              Live extraction
            </NavLink>
          </nav>
        </div>
      </header>
      <Routes>
        <Route path="/" element={<CatalogPage />} />
        <Route path="/live" element={<LivePage />} />
        <Route path="/product/:id" element={<ProductPage />} />
      </Routes>
    </BrowserRouter>
  );
}
