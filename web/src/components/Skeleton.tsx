// Skeleton: placeholder shapes while data loads. Prefer SkeletonLines or
// SkeletonBlock over spinners for page content. Respects reduced motion.
export function Skeleton({ width = "100%", height = 12 }: { width?: string | number; height?: number }) {
  return <span className="skeleton" style={{ width, height }} aria-hidden="true" />;
}

export function SkeletonLines({ lines = 3 }: { lines?: number }) {
  return (
    <div className="stack-sm" role="status" aria-label="Loading">
      {Array.from({ length: lines }, (_, i) => (
        <Skeleton key={i} width={`${92 - ((i * 23) % 40)}%`} />
      ))}
    </div>
  );
}

export function SkeletonBlock({ height = 120 }: { height?: number }) {
  return <span className="skeleton skeleton-block" style={{ height }} role="status" aria-label="Loading" />;
}
