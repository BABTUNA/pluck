import { useEffect, useState } from "react";

interface Props {
  src: string | null;
  alt: string;
  brand?: string;
  className?: string;
  onBroken?: () => void;
}

// extracted urls come from the wild; a broken image renders as a quiet
// placeholder with the brand initial instead of a browser error glyph
export function SafeImage({ src, alt, brand, className = "", onBroken }: Props) {
  const [broken, setBroken] = useState(false);

  // a failure applies to one url only; a new src deserves a fresh attempt
  // (otherwise a dead hover image leaves the card stuck on the placeholder)
  useEffect(() => setBroken(false), [src]);

  if (!src || broken) {
    return (
      <div className={`flex items-center justify-center bg-surface text-faint ${className}`}>
        <span className="font-display text-3xl">{(brand ?? alt ?? "?").charAt(0)}</span>
      </div>
    );
  }
  return (
    <img
      src={src}
      alt={alt}
      loading="lazy"
      className={className}
      onError={() => {
        setBroken(true);
        onBroken?.();
      }}
    />
  );
}
