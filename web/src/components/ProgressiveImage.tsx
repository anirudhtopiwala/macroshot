import { useState, useRef, useEffect } from 'react';

const IMG_BASE = '/macro_app/api/v1/images/';

function thumbUrl(imagePath: string): string {
  return IMG_BASE + imagePath.replace(/(\.\w+)$/, '_thumb$1');
}

interface Props {
  imagePath: string;
  alt: string;
  className?: string;
  style?: React.CSSProperties;
  width?: number;
  height?: number;
  /** If true, only show the thumbnail (no full-res load). Good for list views. */
  thumbOnly?: boolean;
}

/**
 * Progressive image: shows a blurred thumbnail immediately,
 * then crossfades to the full-res image once loaded.
 *
 * Layout: thumbnail is the "layout image" (position: relative) so the
 * container always has a height.  Full-res is overlaid absolutely on top.
 */
export default function ProgressiveImage({ imagePath, alt, className = '', style, width, height, thumbOnly }: Props) {
  const [fullLoaded, setFullLoaded] = useState(false);
  const [failed, setFailed] = useState(false);
  const currentPathRef = useRef(imagePath);

  const thumb = thumbUrl(imagePath);
  const full = IMG_BASE + imagePath;

  // Reset state when imagePath changes
  useEffect(() => {
    currentPathRef.current = imagePath;
    setFullLoaded(false);
    setFailed(false);
  }, [imagePath]);

  // If thumb-only mode, just render the thumbnail with no blur-up.
  // object-cover + explicit CSS width/height forces a square-ish box
  // even when the underlying thumbnail file is aspect-ratio-preserved
  // (e.g. tall Open Food Facts product shots). Without this, HTML
  // width/height attributes would still respect the intrinsic aspect
  // ratio and render the image as a tall rectangle.
  if (thumbOnly) {
    return (
      <img
        src={thumb}
        alt={alt}
        width={width}
        height={height}
        className={`object-cover ${className}`}
        style={{
          ...style,
          width: width ? `${width}px` : undefined,
          height: height ? `${height}px` : undefined,
        }}
        loading="lazy"
        onError={(e) => {
          const img = e.target as HTMLImageElement;
          // Only fallback once - check if already pointing at full
          if (!img.src.endsWith(imagePath)) img.src = full;
        }}
      />
    );
  }

  return (
    <div
      className={`relative overflow-hidden ${className}`}
      style={{ ...style, width: width ? `${width}px` : undefined, height: height ? `${height}px` : undefined }}
    >
      {/* Thumbnail is position:relative so it defines the container height */}
      <img
        src={failed ? full : thumb}
        alt=""
        aria-hidden="true"
        className="w-full object-cover"
        style={{
          filter: fullLoaded ? 'none' : 'blur(8px)',
          transform: fullLoaded ? 'none' : 'scale(1.05)',
          opacity: fullLoaded ? 0 : 1,
          transition: 'opacity 0.3s ease, filter 0.3s ease, transform 0.3s ease',
        }}
        onError={() => setFailed(true)}
      />
      {/* Full-res overlaid on top - fades in when loaded */}
      <img
        src={full}
        alt={alt}
        loading="lazy"
        className="absolute inset-0 w-full h-full object-cover"
        style={{
          opacity: fullLoaded ? 1 : 0,
          transition: 'opacity 0.3s ease',
        }}
        onLoad={() => {
          // Guard against stale onLoad from a previous imagePath
          if (currentPathRef.current === imagePath) setFullLoaded(true);
        }}
        onError={() => {
          // Both failed - just show the thumbnail unblurred
          setFailed(true);
        }}
      />
    </div>
  );
}

export { IMG_BASE, thumbUrl };
